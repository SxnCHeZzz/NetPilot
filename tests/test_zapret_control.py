import sys
from pathlib import Path
import unittest
from dataclasses import replace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from zapret_inventory import ProcessRecord, ServiceRecord, TaskRecord, ZapretItem, ZapretSnapshot
from zapret_control import StopMode, make_stop_plan


COMMAND = r'"C:\NetPilot\zapret\bin\winws.exe" --wf-tcp=443'


def process(pid=21, create_time=100.0, confidence='confirmed'):
    return ZapretItem('process', 'netpilot', confidence, True, ('netpilot-root',),
                      process=ProcessRecord(pid, 'winws.exe',
                          r'C:\NetPilot\zapret\bin\winws.exe', create_time=create_time))


def service(name='Custom Zapret', pid=11, active=True):
    return ZapretItem('service', 'external', 'confirmed', active, ('zapret-arguments',),
                      service=ServiceRecord(name, COMMAND, 'running' if active else 'stopped', pid))


def task(name=r'\Custom\Startup', active=True):
    return ZapretItem('task', 'external', 'confirmed', active, ('netpilot-root',),
                      task=TaskRecord(name, COMMAND, 'running' if active else 'ready'))


class StopPlanTests(unittest.TestCase):
    def test_temporary_plan_stops_owners_before_processes_without_startup_changes(self):
        snapshot = ZapretSnapshot((process(), task(), service()))
        plan = make_stop_plan(snapshot, StopMode.TEMPORARY)
        self.assertEqual([(s.action.value, s.identifier) for s in plan.steps], [
            ('stop_service', 'Custom Zapret'), ('stop_task', r'\Custom\Startup'),
            ('stop_process', 21)])
        self.assertEqual(plan.mode, StopMode.TEMPORARY)
        self.assertTrue(all(s.evidence == s.source.evidence for s in plan.steps))

    def test_ambiguous_and_inactive_items_never_become_steps(self):
        plan = make_stop_plan(ZapretSnapshot((process(confidence='ambiguous'),
                                             service(active=False), task(active=False))), StopMode.TEMPORARY)
        self.assertEqual(plan.steps, ())

    def test_service_owned_process_and_duplicate_owners_are_deduplicated(self):
        snapshot = ZapretSnapshot((process(11), service(), service(), process(21), process(21)))
        plan = make_stop_plan(snapshot, StopMode.TEMPORARY)
        self.assertEqual([s.identifier for s in plan.steps], ['Custom Zapret', 21])

    def test_invalid_identifiers_are_rejected_before_building_a_plan(self):
        for item in (process(0), process(-1), process(True), process('21'),
                     process(create_time=None), process(create_time=float('nan')),
                     service('../service'), service('bad\nname'), service('-all'),
                     task('relative'), task(r'\Custom\..\Startup'), task('\\Custom\\*')):
            with self.subTest(item=item), self.assertRaises(ValueError):
                make_stop_plan(ZapretSnapshot((item,)), StopMode.TEMPORARY)

    def test_unknown_mode_and_inconsistent_records_are_rejected(self):
        with self.assertRaises(ValueError):
            make_stop_plan(ZapretSnapshot(), 'other')
        malformed = ZapretItem('service', 'external', 'confirmed', True, (), process=process().process)
        with self.assertRaises(ValueError):
            make_stop_plan(ZapretSnapshot((malformed,)), StopMode.TEMPORARY)

    def test_disable_restart_mode_is_preserved_for_configuration_layer(self):
        plan = make_stop_plan(ZapretSnapshot((service(),)), StopMode.DISABLE_RESTART)
        self.assertEqual(plan.mode, StopMode.DISABLE_RESTART)


class MemoryAdapter:
    """Mutations affect only this in-memory inventory, never the host system."""
    def __init__(self, items=(), graceful=True, failures=()):
        self.items = list(items)
        self.graceful = graceful
        self.failures = failures
        self.calls = []
        self.scans = 0
        self.warnings = ()

    def snapshot(self):
        self.scans += 1
        return ZapretSnapshot(tuple(self.items), self.warnings)

    def stop_service(self, name):
        self.calls.append(('service', name))
        if 'service' in self.failures:
            raise PermissionError('service access denied')
        pids = {i.pid for i in self.items if i.service and i.service.name == name}
        self.items = [i for i in self.items if not (i.service and i.service.name == name)
                      and not (i.process and i.pid in pids)]

    def stop_task(self, path):
        self.calls.append(('task', path))
        if 'task' in self.failures:
            raise PermissionError('task access denied')
        self.items = [i for i in self.items if not (i.task and i.task.name == path)]

    def terminate_process(self, pid, *, create_time):
        self.calls.append(('terminate', pid, create_time))
        if self.graceful:
            self.items = [i for i in self.items if i.pid != pid]

    def wait_process(self, pid, *, create_time, timeout):
        self.calls.append(('wait', pid, create_time, timeout))
        return not any(i.pid == pid for i in self.items)

    def kill_process(self, pid, *, create_time):
        self.calls.append(('kill', pid, create_time))
        self.items = [i for i in self.items if i.pid != pid]


class StopExecutionTests(unittest.TestCase):
    def execute(self, items, adapter=None):
        from zapret_control import execute_stop
        adapter = adapter or MemoryAdapter(items)
        plan = make_stop_plan(ZapretSnapshot(tuple(items)), StopMode.TEMPORARY)
        return execute_stop(plan, adapter), adapter

    def test_graceful_stop_uses_identity_and_bounded_wait_then_rescans(self):
        result, adapter = self.execute((process(), task(), service()))
        self.assertTrue(result.success)
        self.assertEqual(result.status, 'success')
        self.assertEqual(adapter.calls[:3], [('service', 'Custom Zapret'),
                         ('task', r'\Custom\Startup'), ('terminate', 21, 100.0)])
        self.assertEqual(adapter.calls[3][:3], ('wait', 21, 100.0))
        self.assertGreater(adapter.calls[3][3], 0)
        self.assertLessEqual(adapter.calls[3][3], 30)
        self.assertEqual(result.final_snapshot.active_count, 0)
        self.assertGreaterEqual(adapter.scans, 4)

    def test_force_only_after_graceful_timeout_and_identity_recheck(self):
        result, adapter = self.execute((process(),), MemoryAdapter((process(),), graceful=False))
        self.assertTrue(result.success)
        self.assertEqual([call[0] for call in adapter.calls], ['terminate', 'wait', 'kill', 'wait'])

    def test_owner_failures_continue_safe_steps_and_return_unresolved_confirmed_items(self):
        items = (service(), task(), process(11), process(21))
        result, adapter = self.execute(items, MemoryAdapter(items, failures=('service', 'task')))
        self.assertEqual(result.status, 'partial')
        self.assertFalse(result.success)
        self.assertEqual({i.kind for i in result.remaining}, {'service', 'task', 'process'})
        self.assertEqual([call[0] for call in adapter.calls], ['service', 'task', 'terminate', 'wait'])
        self.assertEqual(len(result.errors), 2)

    def test_command_return_does_not_override_final_rescan(self):
        class IgnoredStop(MemoryAdapter):
            def stop_service(self, name):
                return 0
        result, _ = self.execute((service(),), IgnoredStop((service(),)))
        self.assertEqual(result.status, 'partial')
        self.assertEqual(result.remaining, (service(),))

    def test_final_truth_can_confirm_stop_despite_command_error(self):
        class StoppedWithError(MemoryAdapter):
            def stop_service(self, name):
                super().stop_service(name)
                raise OSError('command reported an error after stop')
        result, _ = self.execute((service(),), StoppedWithError((service(),)))
        self.assertTrue(result.success)
        self.assertEqual(len(result.errors), 1)

    def test_force_failure_does_not_prevent_next_process_or_final_scan(self):
        class ForceDenied(MemoryAdapter):
            def kill_process(self, pid, *, create_time):
                if pid == 21:
                    raise PermissionError('cannot force this process')
                super().kill_process(pid, create_time=create_time)
        items = (process(21), process(22))
        result, adapter = self.execute(items, ForceDenied(items, graceful=False))
        self.assertEqual(result.status, 'partial')
        self.assertEqual([i.pid for i in result.remaining], [21])
        self.assertIn(('kill', 22, 100.0), adapter.calls)
        self.assertEqual(len(result.errors), 1)

    def test_final_scan_failure_after_mutation_cannot_report_success(self):
        class FinalScanFails(MemoryAdapter):
            def snapshot(self):
                if not self.items:
                    raise TimeoutError('final service query timed out')
                return super().snapshot()
        result, adapter = self.execute((service(),), FinalScanFails((service(),)))
        self.assertEqual(adapter.calls, [('service', 'Custom Zapret')])
        self.assertEqual(result.status, 'error')
        self.assertIsNone(result.final_snapshot)

    def test_new_confirmed_instance_in_final_scan_is_reported_not_implicitly_stopped(self):
        class Restart(MemoryAdapter):
            def stop_service(self, name):
                super().stop_service(name)
                self.items.append(process(99))
        result, adapter = self.execute((service(),), Restart((service(),)))
        self.assertEqual(result.status, 'partial')
        self.assertEqual([i.pid for i in result.remaining], [99])
        self.assertEqual(adapter.calls, [('service', 'Custom Zapret')])

    def test_reused_pid_and_reconfigured_service_or_task_are_never_mutated(self):
        for old, new in ((process(), process(create_time=200.0)),
                         (service(), replace(service(), service=replace(service().service, command='other'))),
                         (task(), replace(task(), task=replace(task().task, command='other')))):
            with self.subTest(kind=old.kind):
                result, adapter = self.execute((old,), MemoryAdapter((new,)))
                self.assertEqual(result.status, 'partial')
                self.assertEqual(adapter.calls, [])
                self.assertTrue(result.errors)

    def test_pid_reused_during_wait_is_not_force_killed(self):
        class ReusedDuringWait(MemoryAdapter):
            def wait_process(self, pid, **kwargs):
                self.items = [process(pid, create_time=200.0)]
                return False
        result, adapter = self.execute((process(),), ReusedDuringWait((process(),), graceful=False))
        self.assertEqual(result.status, 'partial')
        self.assertEqual([c[0] for c in adapter.calls], ['terminate'])
        self.assertTrue(result.errors)

    def test_ambiguous_final_items_are_visible_but_not_stop_targets(self):
        result, adapter = self.execute((), MemoryAdapter((process(confidence='ambiguous'),)))
        self.assertTrue(result.success)
        self.assertEqual(len(result.final_snapshot.ambiguous), 1)
        self.assertEqual(adapter.calls, [])

    def test_final_scan_failure_or_incomplete_discovery_cannot_report_success(self):
        class FailingScan(MemoryAdapter):
            def snapshot(self):
                raise TimeoutError('inventory timed out')
        result, _ = self.execute((), FailingScan())
        self.assertEqual(result.status, 'error')
        self.assertIsNone(result.final_snapshot)
        adapter = MemoryAdapter()
        adapter.warnings = ('service discovery incomplete',)
        result, _ = self.execute((), adapter)
        self.assertEqual(result.status, 'error')

    def test_forged_plan_is_rejected_and_final_snapshot_still_taken(self):
        from zapret_control import execute_stop
        plan = make_stop_plan(ZapretSnapshot((process(),)), StopMode.TEMPORARY)
        plan = replace(plan, steps=(replace(plan.steps[0], identifier=99),))
        adapter = MemoryAdapter((process(),))
        result = execute_stop(plan, adapter)
        self.assertEqual(adapter.calls, [])
        self.assertEqual(adapter.scans, 1)
        self.assertTrue(result.errors)

    def test_disable_restart_requires_configuration_layer_before_execution(self):
        from zapret_control import execute_stop
        plan = make_stop_plan(ZapretSnapshot(), StopMode.DISABLE_RESTART)
        result = execute_stop(plan, MemoryAdapter())
        self.assertEqual(result.status, 'error')
        self.assertTrue(result.errors)


if __name__ == '__main__':
    unittest.main()
