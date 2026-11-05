import sys
from pathlib import Path
import unittest
from dataclasses import replace
import json
import tempfile
from unittest.mock import patch

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


class AutostartBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'NetPilot'
        self.path = self.root / 'zapret-autostart-v1.json'
        self.patcher = patch('zapret_control.AUTOSTART_DATA_DIR', self.root, create=True)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_version_one_round_trip_preserves_exact_original_configuration(self):
        from zapret_control import AutostartBackup, save_backup, load_backup
        save_backup(self.path, AutostartBackup(
            services={'Custom Zapret': 'automatic_delayed'}, tasks={r'\Custom\Startup': False}))
        raw = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(raw, {'owner': 'NetPilot', 'version': 1,
            'services': {'Custom Zapret': 'automatic_delayed'}, 'tasks': {r'\Custom\Startup': False}})
        loaded = load_backup(self.path)
        self.assertEqual(loaded.services, {'Custom Zapret': 'automatic_delayed'})
        self.assertEqual(loaded.tasks, {r'\Custom\Startup': False})

    def test_repeated_save_keeps_earliest_unrestored_state_and_other_records(self):
        from zapret_control import AutostartBackup, save_backup, load_backup
        save_backup(self.path, AutostartBackup(services={'Custom Zapret': 'manual'},
                                              tasks={r'\Custom\Startup': True}))
        save_backup(self.path, AutostartBackup(services={'custom zapret': 'disabled', 'Other': 'automatic'},
                                              tasks={r'\custom\startup': False, r'\Other': True}))
        loaded = load_backup(self.path)
        self.assertEqual(loaded.services, {'Custom Zapret': 'manual', 'Other': 'automatic'})
        self.assertEqual(loaded.tasks, {r'\Custom\Startup': True, r'\Other': True})

    def test_old_backup_has_no_time_based_expiry(self):
        from zapret_control import AutostartBackup, save_backup, load_backup
        import os
        save_backup(self.path, AutostartBackup(services={'Custom Zapret': 'automatic'}))
        os.utime(self.path, (1, 1))
        self.assertEqual(load_backup(self.path).services, {'Custom Zapret': 'automatic'})

    def test_path_like_identifiers_unknown_modes_and_wrong_types_are_rejected(self):
        from zapret_control import AutostartBackup, save_backup
        for services, tasks in (({'../bad': 'manual'}, {}), ({r'C:\bad': 'manual'}, {}),
                 ({}, {'../task': True}), ({}, {r'\Custom\..\bad': True}),
                 ({}, {r'\C:\bad.exe': True}), ({'Custom Zapret': 'unknown'}, {}),
                 ({}, {r'\Custom\Startup': 1})):
            with self.subTest(services=services, tasks=tasks), self.assertRaises(ValueError):
                save_backup(self.path, AutostartBackup(services=services, tasks=tasks))
        self.assertFalse(self.path.exists())

    def test_foreign_version_owner_and_extra_payload_are_rejected_on_load(self):
        from zapret_control import load_backup
        self.root.mkdir()
        for changes in ({'version': 2}, {'version': True}, {'owner': 'other'},
                        {'command': 'execute me'}, {'services': {'../bad': 'automatic'}}):
            raw = dict(owner='NetPilot', version=1, services={}, tasks={})
            raw.update(changes)
            self.path.write_text(json.dumps(raw), encoding='utf-8')
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                load_backup(self.path)

    def test_atomic_replace_failure_preserves_previous_backup(self):
        from zapret_control import AutostartBackup, save_backup, load_backup
        save_backup(self.path, AutostartBackup(services={'Custom Zapret': 'manual'}))
        with patch('zapret_control.os.replace', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                save_backup(self.path, AutostartBackup(tasks={r'\Custom\Startup': True}))
        self.assertEqual(load_backup(self.path).services, {'Custom Zapret': 'manual'})
        self.assertEqual(load_backup(self.path).tasks, {})
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_missing_backup_is_none_and_storage_outside_netpilot_is_rejected(self):
        from zapret_control import AutostartBackup, save_backup, load_backup
        self.assertIsNone(load_backup(self.path))
        with self.assertRaises(ValueError):
            save_backup(Path(self.temp.name) / 'outside.json', AutostartBackup())

    def test_duplicate_json_fields_are_rejected_instead_of_losing_original_values(self):
        from zapret_control import load_backup
        self.root.mkdir()
        self.path.write_text('{"owner":"NetPilot","version":1,"services":'
            '{"Custom Zapret":"automatic","Custom Zapret":"disabled"},"tasks":{}}', encoding='utf-8')
        with self.assertRaises(ValueError):
            load_backup(self.path)


class ConfigurationAdapter(MemoryAdapter):
    def __init__(self, items=(), modes=None, enabled=None):
        super().__init__(items)
        self.modes = dict(modes or {'Custom Zapret': 'automatic'})
        self.enabled = dict(enabled or {r'\Custom\Startup': True})

    def get_service_start_mode(self, name):
        return self.modes[name]

    def set_service_start_mode(self, name, mode):
        self.calls.append(('service_mode', name, mode))
        self.modes[name] = mode

    def get_task_enabled(self, name):
        return self.enabled[name]

    def set_task_enabled(self, name, enabled):
        self.calls.append(('task_enabled', name, enabled))
        self.enabled[name] = enabled

    def stop_service(self, name):
        self.calls.append(('service', name))
        self.items = [replace(i, active=False) if i.service and i.service.name == name else i
                      for i in self.items]

    def stop_task(self, name):
        self.calls.append(('task', name))
        self.items = [replace(i, active=False) if i.task and i.task.name == name else i
                      for i in self.items]


class AutostartOperationTests(unittest.TestCase):
    setUp = AutostartBackupTests.setUp

    def disable(self, adapter):
        from zapret_control import execute_stop
        plan = make_stop_plan(adapter.snapshot(), StopMode.DISABLE_RESTART)
        return execute_stop(plan, adapter, backup_path=self.path)

    def test_disable_saves_original_state_before_each_mutation_and_stops_then_rescans(self):
        from zapret_control import load_backup
        path = self.path
        class CheckDurability(ConfigurationAdapter):
            def set_service_start_mode(self, name, mode):
                assert load_backup(path).services[name] == 'automatic_delayed'
                super().set_service_start_mode(name, mode)
            def set_task_enabled(self, name, enabled):
                assert load_backup(path).tasks[name] is True
                super().set_task_enabled(name, enabled)
        adapter = CheckDurability((service(), task()), modes={'Custom Zapret': 'automatic_delayed'})
        result = self.disable(adapter)
        self.assertTrue(result.success, result.errors)
        self.assertEqual(adapter.modes, {'Custom Zapret': 'disabled'})
        self.assertEqual(adapter.enabled, {r'\Custom\Startup': False})
        self.assertEqual(result.final_snapshot.active_count, 0)
        self.assertGreaterEqual(adapter.scans, 4)
        self.assertEqual(result.backup.services, {'Custom Zapret': 'automatic_delayed'})

    def test_inactive_confirmed_owners_are_disabled_and_ambiguous_owners_are_untouched(self):
        adapter = ConfigurationAdapter((service(active=False), task(active=False),
            replace(service('Uncertain', active=False), confidence='ambiguous')))
        result = self.disable(adapter)
        self.assertTrue(result.success, result.errors)
        self.assertEqual(adapter.calls, [('service_mode', 'Custom Zapret', 'disabled'),
                                         ('task_enabled', r'\Custom\Startup', False)])

    def test_inactive_service_stale_pid_does_not_suppress_a_running_process(self):
        items = (service(pid=21, active=False), process(21))
        plan = make_stop_plan(ZapretSnapshot(items), StopMode.DISABLE_RESTART)
        self.assertEqual([step.identifier for step in plan.steps], ['Custom Zapret', 21])

    def test_repeated_disable_preserves_original_configuration_after_already_disabled(self):
        from zapret_control import load_backup
        adapter = ConfigurationAdapter((service(), task()))
        self.assertTrue(self.disable(adapter).success)
        self.assertTrue(self.disable(adapter).success)
        self.assertEqual(load_backup(self.path).services, {'Custom Zapret': 'automatic'})
        self.assertEqual(load_backup(self.path).tasks, {r'\Custom\Startup': True})

    def test_backup_write_failure_prevents_all_configuration_and_runtime_mutations(self):
        adapter = ConfigurationAdapter((service(), task()))
        with patch('zapret_control.os.replace', side_effect=OSError('disk full')):
            result = self.disable(adapter)
        self.assertFalse(result.success)
        self.assertEqual(adapter.calls, [])
        self.assertTrue(any('disk full' in error for error in result.errors))

    def test_unknown_original_mode_is_rejected_before_mutation(self):
        adapter = ConfigurationAdapter((service(),), modes={'Custom Zapret': 'unknown'})
        result = self.disable(adapter)
        self.assertFalse(result.success)
        self.assertEqual(adapter.calls, [])
        self.assertFalse(self.path.exists())

    def test_no_op_disable_readback_cannot_report_success_even_when_runtime_stop_succeeds(self):
        class IgnoreDisable(ConfigurationAdapter):
            def set_service_start_mode(self, name, mode):
                pass
        adapter = IgnoreDisable((service(),))
        result = self.disable(adapter)
        self.assertFalse(result.success)
        self.assertTrue(any('verify' in error.lower() for error in result.errors))
        self.assertEqual(result.backup.services, {'Custom Zapret': 'automatic'})

    def test_temporary_stop_never_reads_writes_or_mutates_autostart(self):
        from zapret_control import execute_stop
        adapter = ConfigurationAdapter((service(), task()))
        result = execute_stop(make_stop_plan(adapter.snapshot(), StopMode.TEMPORARY), adapter)
        self.assertTrue(result.success)
        self.assertEqual(adapter.modes['Custom Zapret'], 'automatic')
        self.assertTrue(adapter.enabled[r'\Custom\Startup'])
        self.assertFalse(self.path.exists())

    def test_restore_only_persisted_records_and_removes_confirmed_restores(self):
        from zapret_control import restore_autostart, load_backup
        adapter = ConfigurationAdapter((service(), task()))
        backup = self.disable(adapter).backup
        adapter.items.append(service('Other'))
        adapter.calls.clear()
        before = adapter.scans
        result = restore_autostart(backup, adapter)
        self.assertTrue(result.success, result.errors)
        self.assertEqual(adapter.modes, {'Custom Zapret': 'automatic'})
        self.assertTrue(adapter.enabled[r'\Custom\Startup'])
        self.assertEqual(adapter.calls, [('service_mode', 'Custom Zapret', 'automatic'),
                                         ('task_enabled', r'\Custom\Startup', True)])
        self.assertGreater(adapter.scans, before)
        self.assertEqual(load_backup(self.path).services, {})
        self.assertEqual(load_backup(self.path).tasks, {})

    def test_partial_restore_keeps_failed_entry_with_actionable_error(self):
        from zapret_control import restore_autostart, load_backup
        adapter = ConfigurationAdapter((service(), task()))
        backup = self.disable(adapter).backup
        original = adapter.set_service_start_mode
        def deny(name, mode):
            raise PermissionError('run with administrator rights')
        adapter.set_service_start_mode = deny
        result = restore_autostart(backup, adapter)
        self.assertEqual(result.status, 'partial')
        self.assertEqual(load_backup(self.path).services, {'Custom Zapret': 'automatic'})
        self.assertEqual(load_backup(self.path).tasks, {})
        self.assertTrue(any('Custom Zapret' in error and 'administrator' in error for error in result.errors))
        adapter.set_service_start_mode = original
        self.assertTrue(restore_autostart(load_backup(self.path), adapter).success)

    def test_restore_task_with_multiple_inventory_action_records(self):
        from zapret_control import AutostartBackup, restore_autostart, save_backup
        backup = save_backup(self.path, AutostartBackup(tasks={r'\Custom\Startup': True}))
        adapter = ConfigurationAdapter((task(active=False), task(active=False)))
        adapter.enabled[r'\Custom\Startup'] = False
        result = restore_autostart(backup, adapter)
        self.assertTrue(result.success, result.errors)
        self.assertTrue(adapter.enabled[r'\Custom\Startup'])

    def test_unpersisted_tampered_or_stale_backup_is_rejected_without_mutation(self):
        from zapret_control import AutostartBackup, restore_autostart, save_backup
        adapter = ConfigurationAdapter((service(active=False), task(active=False)))
        plain = AutostartBackup(services={'Custom Zapret': 'manual'})
        stored = save_backup(self.path, plain)
        for invalid in (plain, replace(stored, services={'Other': 'automatic'}),
                        replace(stored, services={'../bad': 'manual'})):
            with self.subTest(backup=invalid):
                result = restore_autostart(invalid, adapter)
                self.assertFalse(result.success)
                self.assertEqual(adapter.calls, [])
        save_backup(self.path, AutostartBackup(tasks={r'\Custom\Startup': True}))
        self.assertFalse(restore_autostart(stored, adapter).success)
        self.assertEqual(adapter.calls, [])

    def test_unconfirmed_or_missing_restore_target_is_retained(self):
        from zapret_control import AutostartBackup, restore_autostart, save_backup, load_backup
        backup = save_backup(self.path, AutostartBackup(services={'Custom Zapret': 'automatic'}))
        for items in ((), (replace(service(active=False), confidence='ambiguous'),)):
            adapter = ConfigurationAdapter(items)
            result = restore_autostart(backup, adapter)
            self.assertEqual(result.status, 'partial')
            self.assertEqual(adapter.calls, [])
            self.assertEqual(load_backup(self.path).services, {'Custom Zapret': 'automatic'})

    def test_unverified_restore_and_backup_update_failure_retain_original_records(self):
        from zapret_control import restore_autostart, load_backup
        adapter = ConfigurationAdapter((service(),))
        backup = self.disable(adapter).backup
        with patch.object(adapter, 'set_service_start_mode', return_value=None):
            result = restore_autostart(backup, adapter)
        self.assertFalse(result.success)
        self.assertEqual(load_backup(self.path).services, {'Custom Zapret': 'automatic'})
        with patch('zapret_control.os.replace', side_effect=OSError('disk full')):
            result = restore_autostart(backup, adapter)
        self.assertFalse(result.success)
        self.assertEqual(load_backup(self.path).services, {'Custom Zapret': 'automatic'})
        self.assertTrue(any('disk full' in error for error in result.errors))


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

    def test_empty_disable_restart_plan_is_successful_without_startup_changes(self):
        from zapret_control import execute_stop
        plan = make_stop_plan(ZapretSnapshot(), StopMode.DISABLE_RESTART)
        result = execute_stop(plan, MemoryAdapter())
        self.assertTrue(result.success, result.errors)
        self.assertIsNone(result.backup)


if __name__ == '__main__':
    unittest.main()
