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


def process(pid=21, create_time=100.0, confidence='confirmed', cmdline=()):
    return ZapretItem('process', 'netpilot', confidence, True, ('netpilot-root',),
                      process=ProcessRecord(pid, 'winws.exe',
                          r'C:\NetPilot\zapret\bin\winws.exe', cmdline=cmdline,
                          create_time=create_time))


def service(name='Custom Zapret', pid=11, active=True):
    return ZapretItem('service', 'external', 'confirmed', active, ('zapret-arguments',),
                      service=ServiceRecord(name, COMMAND, 'running' if active else 'stopped', pid))


def task(name=r'\Custom\Startup', active=True):
    return ZapretItem('task', 'external', 'confirmed', active, ('netpilot-root',),
                      task=TaskRecord(name, COMMAND, 'running' if active else 'ready'))


class TransferAdapter:
    """Stateful SCM/task boundary, with failures before or after one mutation."""
    def __init__(self, configs=(), task_active=False, fail=None):
        self.configs = {c.name: c for c in configs}
        self.tasks = {r'\Custom\Startup': [True, task_active]} if task_active else {}
        self.calls = []
        self.fail = {fail} if isinstance(fail, tuple) else set(fail or ())
        self.mutations = 0
        self.warnings = ()

    def mutate(self, action, change):
        self.mutations += 1
        self.calls.append(action)
        if (self.mutations, 'before') in self.fail:
            raise RuntimeError('injected before mutation')
        change()
        if (self.mutations, 'after') in self.fail:
            raise RuntimeError('injected after mutation')

    def snapshot(self):
        items = [ZapretItem('service', 'netpilot' if c.adapter_fields.get('owned') else 'external',
            'confirmed', c.state == 'running', ('zapret-arguments',),
            service=ServiceRecord(c.name, c.image_path, c.state, 11 if c.state == 'running' else None))
            for c in self.configs.values()]
        items += [task(name, active=values[1]) for name, values in self.tasks.items()]
        return ZapretSnapshot(tuple(items), self.warnings)

    def get_service_backup(self, name):
        return self.configs.get(name)

    def validate_service_backup(self, backup):
        if not {'account', 'owned'} <= backup.adapter_fields.keys():
            raise ValueError('missing restoration account/ownership metadata')

    def stop_service(self, name):
        self.mutate(('stop', name), lambda: self.configs.update({name: replace(self.configs[name], state='stopped')}))

    def start_service(self, name):
        self.mutate(('start', name), lambda: self.configs.update({name: replace(self.configs[name], state='running')}))

    def delete_service(self, name):
        self.mutate(('delete', name), lambda: self.configs.pop(name))

    def create_netpilot_service(self, validated):
        from zapret_control import ServiceBackup
        def create():
            if 'zapret' in self.configs:
                raise RuntimeError('service already exists')
            self.configs['zapret'] = ServiceBackup('zapret', validated.image_path, 'automatic',
                'stopped', 'zapret', 'NetPilot Zapret', {'account': 'LocalSystem', 'owned': True,
                                                         'transaction_id': validated.transaction_id})
        self.mutate(('create', 'zapret'), create)

    def restore_service(self, backup):
        self.mutate(('restore', backup.name), lambda: self.configs.update({backup.name: backup}))

    def get_service_start_mode(self, name):
        return self.configs[name].start_mode

    def set_service_start_mode(self, name, mode):
        self.mutate(('mode', name, mode), lambda: self.configs.update({name: replace(self.configs[name], start_mode=mode)}))

    def get_task_enabled(self, name):
        return self.tasks[name][0]

    def set_task_enabled(self, name, enabled):
        self.mutate(('enabled', name, enabled), lambda: self.tasks[name].__setitem__(0, enabled))

    def stop_task(self, name):
        self.mutate(('stop_task', name), lambda: self.tasks[name].__setitem__(1, False))

    def start_task(self, name):
        self.mutate(('start_task', name), lambda: self.tasks[name].__setitem__(1, True))


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'bin').mkdir()
        (self.root / 'bin' / 'winws.exe').write_bytes(b'MZ')
        (self.root / 'general.bat').write_text('start "zapret" "%BIN%winws.exe" --wf-tcp=443 --dpi-desync=fake\n')
        self.storage = self.root / 'data'
        p = patch('zapret_control.AUTOSTART_DATA_DIR', self.storage)
        p.start()
        self.addCleanup(p.stop)

    def config(self, name='zapret', owned=False, state='running'):
        from zapret_control import ServiceBackup
        image = (f'"{self.root / "bin" / "winws.exe"}" --wf-tcp=443 --dpi-desync=fake' if owned
                 else r'"C:\External App\bin\winws.exe" --wf-tcp=80 --dpi-desync=split2')
        return ServiceBackup(name, image, 'automatic_delayed', state, 'My external service',
                             'Exact description', {'account': 'LocalSystem', 'owned': owned, 'dependencies': ['Tcpip']})

    def request(self, adapter, **changes):
        from zapret_control import TransferRequest
        values = dict(folder=self.root, strategy='general.bat', snapshot=adapter.snapshot(), confirmed=True)
        values.update(changes)
        return TransferRequest(**values)

    def test_preflight_rejects_missing_traversing_and_malformed_strategy_without_mutation(self):
        from zapret_control import validate_transfer
        adapter = TransferAdapter()
        for strategy in ('missing.bat', '../general.bat', r'..\general.bat'):
            with self.subTest(strategy=strategy), self.assertRaises(ValueError):
                validate_transfer(self.request(adapter, strategy=strategy), adapter)
        for text in ('echo hello', '"%BIN%winws.exe" invalid', '"%BIN%winws.exe" --wf-tcp=%UNKNOWN%',
                     '"%BIN%winws.exe" --wf-tcp=443 & echo bad', '"%BIN%winws.exe" --hostlist="unclosed'):
            (self.root / 'general.bat').write_text(text)
            with self.subTest(text=text), self.assertRaises(ValueError):
                validate_transfer(self.request(adapter), adapter)
        self.assertEqual(adapter.calls, [])

    def test_missing_executable_and_unconfirmed_request_are_rejected(self):
        from zapret_control import validate_transfer
        adapter = TransferAdapter()
        with self.assertRaises(ValueError):
            validate_transfer(self.request(adapter, confirmed=False), adapter)
        (self.root / 'bin' / 'winws.exe').unlink()
        with self.assertRaises(FileNotFoundError):
            validate_transfer(self.request(adapter), adapter)
        self.assertEqual(adapter.calls, [])

    def test_matching_service_is_idempotent_and_only_starts_when_stopped(self):
        from zapret_control import transfer_to_netpilot
        for state in ('running', 'stopped'):
            adapter = TransferAdapter([self.config(owned=True, state=state)])
            result = transfer_to_netpilot(self.request(adapter), adapter)
            self.assertEqual(result.status, 'success', result.errors)
            self.assertEqual(adapter.calls, [] if state == 'running' else [('start', 'zapret')])
            self.assertEqual(adapter.configs['zapret'].state, 'running')

    def test_explicit_transfer_backs_up_external_service_and_disables_other_owners(self):
        from zapret_control import transfer_to_netpilot, load_backup
        original = self.config()
        adapter = TransferAdapter([original, self.config('Custom Zapret')], task_active=True)
        original_delete = adapter.delete_service
        def checked_delete(name):
            raw = json.loads((self.storage / 'zapret-transfer-v1.json').read_text())
            saved = raw['services']['zapret']
            self.assertEqual(saved['image_path'], original.image_path)
            self.assertEqual(saved['adapter_fields'], original.adapter_fields)
            self.assertEqual(saved['display_name'], original.display_name)
            self.assertEqual(saved['description'], original.description)
            self.assertEqual(saved['start_mode'], 'automatic_delayed')
            self.assertEqual(saved['state'], 'running')
            original_delete(name)
        adapter.delete_service = checked_delete
        result = transfer_to_netpilot(self.request(adapter), adapter)
        self.assertEqual(result.status, 'success', result.errors)
        self.assertEqual([c for c in adapter.calls if c[0] == 'delete'], [('delete', 'zapret')])
        self.assertEqual(adapter.configs['Custom Zapret'].start_mode, 'disabled')
        self.assertEqual(adapter.configs['Custom Zapret'].state, 'stopped')
        self.assertEqual(adapter.tasks[r'\Custom\Startup'], [False, False])
        backup = load_backup()
        self.assertEqual(backup.services, {'Custom Zapret': 'automatic_delayed'})
        self.assertEqual(backup.tasks, {r'\Custom\Startup': True})

    def test_failure_before_and_after_every_mutation_restores_exact_configuration_and_state(self):
        from zapret_control import transfer_to_netpilot
        configs = [self.config(), self.config('Custom Zapret')]
        baseline = TransferAdapter(configs, task_active=True)
        self.assertTrue(transfer_to_netpilot(self.request(baseline), baseline).success)
        for index in range(1, baseline.mutations + 1):
            for phase in ('before', 'after'):
                with self.subTest(index=index, action=baseline.calls[index-1], phase=phase):
                    adapter = TransferAdapter(configs, task_active=True, fail=(index, phase))
                    result = transfer_to_netpilot(self.request(adapter), adapter)
                    self.assertEqual(result.status, 'rolled_back', result.errors)
                    self.assertEqual(adapter.configs, {c.name: c for c in configs})
                    self.assertEqual(adapter.tasks[r'\Custom\Startup'], [True, True])
                    self.assertIsNotNone(result.final_snapshot)

    def test_backup_write_failure_prevents_all_system_mutations(self):
        from zapret_control import transfer_to_netpilot
        adapter = TransferAdapter([self.config()])
        with patch('zapret_control.os.replace', side_effect=OSError('disk full')):
            result = transfer_to_netpilot(self.request(adapter), adapter)
        self.assertFalse(result.success)
        self.assertEqual(adapter.calls, [])

    def test_failed_restoration_reports_manual_recovery_with_persisted_original(self):
        from zapret_control import transfer_to_netpilot
        adapter = TransferAdapter([self.config()], fail=(3, 'after'))
        adapter.restore_service = lambda backup: (_ for _ in ()).throw(PermissionError('restore denied'))
        result = transfer_to_netpilot(self.request(adapter), adapter)
        self.assertEqual(result.status, 'manual_recovery_required', result.errors)
        self.assertTrue((self.storage / 'zapret-transfer-v1.json').is_file())
        self.assertTrue(any('restore denied' in error for error in result.errors))

    def test_stale_or_ambiguous_name_conflict_and_incomplete_inventory_reject_before_mutation(self):
        from zapret_control import validate_transfer
        adapter = TransferAdapter([self.config()])
        request = self.request(adapter)
        adapter.configs['zapret'] = replace(adapter.configs['zapret'], image_path='changed')
        with self.assertRaises(ValueError):
            validate_transfer(request, adapter)
        adapter.warnings = ('services inaccessible',)
        with self.assertRaises(ValueError):
            validate_transfer(self.request(adapter), adapter)
        adapter.warnings = ()
        snap = adapter.snapshot()
        adapter.snapshot = lambda: ZapretSnapshot(tuple(replace(i, confidence='ambiguous') for i in snap.items))
        with self.assertRaises(ValueError):
            validate_transfer(self.request(adapter), adapter)
        self.assertEqual(adapter.calls, [])

    def test_failed_start_wrong_command_and_final_conflict_never_report_success(self):
        from zapret_control import transfer_to_netpilot
        for problem in ('no_start', 'wrong_args', 'conflict', 'incomplete'):
            with self.subTest(problem=problem):
                adapter = TransferAdapter([self.config()])
                start = adapter.start_service
                def broken_start(name):
                    if problem != 'no_start':
                        start(name)
                    if problem == 'wrong_args':
                        adapter.configs[name] = replace(adapter.configs[name], image_path=adapter.configs[name].image_path + ' --wrong')
                    elif problem == 'conflict':
                        adapter.configs['New Zapret'] = self.config('New Zapret')
                    elif problem == 'incomplete':
                        adapter.warnings = ('tasks inaccessible',)
                adapter.start_service = broken_start
                result = transfer_to_netpilot(self.request(adapter), adapter)
                self.assertFalse(result.success)
                self.assertEqual(result.status, 'rolled_back' if problem == 'no_start' else 'manual_recovery_required')
                if problem == 'conflict':
                    self.assertNotIn(('delete', 'New Zapret'), adapter.calls)

    def test_missing_adapter_restoration_fields_are_rejected_before_mutation(self):
        from zapret_control import validate_transfer
        adapter = TransferAdapter([replace(self.config(), adapter_fields={})])
        with self.assertRaises(ValueError):
            validate_transfer(self.request(adapter), adapter)
        self.assertEqual(adapter.calls, [])

    def test_recovery_journal_preserves_earlier_external_configuration_on_next_transfer(self):
        from zapret_control import transfer_to_netpilot
        original = self.config()
        adapter = TransferAdapter([original])
        self.assertTrue(transfer_to_netpilot(self.request(adapter), adapter).success)
        self.assertTrue(transfer_to_netpilot(self.request(adapter), adapter).success)
        raw = json.loads((self.storage / 'zapret-transfer-v1.json').read_text())
        self.assertTrue(any(entry['services']['zapret']['image_path'] == original.image_path
                            for entry in raw['history']))

    def test_step_results_include_disabled_owners_and_verified_restoration(self):
        from zapret_control import transfer_to_netpilot
        adapter = TransferAdapter([self.config(), self.config('Custom Zapret')], task_active=True)
        result = transfer_to_netpilot(self.request(adapter), adapter)
        actions = [s.action.value for s in result.completed_steps]
        self.assertIn('disable_service', actions)
        self.assertIn('disable_task', actions)
        adapter = TransferAdapter([self.config()], fail=(3, 'after'))
        result = transfer_to_netpilot(self.request(adapter), adapter)
        self.assertEqual(result.status, 'rolled_back')
        self.assertIn('restore_service', [s.action.value for s in result.completed_steps])

    def test_stopped_standalone_process_cannot_be_claimed_as_rolled_back(self):
        from zapret_control import transfer_to_netpilot
        adapter = TransferAdapter([self.config()], fail=(3, 'before'))
        processes = [process(21)]
        snapshot = adapter.snapshot
        adapter.snapshot = lambda: replace(snapshot(), items=snapshot().items + tuple(processes))
        adapter.terminate_process = lambda pid, create_time: processes.clear()
        adapter.wait_process = lambda pid, create_time, timeout: not processes
        result = transfer_to_netpilot(self.request(adapter), adapter)
        self.assertEqual(result.status, 'manual_recovery_required')
        self.assertFalse(processes)

    def test_matching_stopped_service_start_failure_restores_without_delete_or_create(self):
        from zapret_control import transfer_to_netpilot
        for phase in ('before', 'after'):
            original = self.config(owned=True, state='stopped')
            adapter = TransferAdapter([original], fail=(1, phase))
            result = transfer_to_netpilot(self.request(adapter), adapter)
            self.assertEqual(result.status, 'rolled_back', result.errors)
            self.assertEqual(adapter.configs['zapret'], original)
            self.assertFalse(any(c[0] in {'delete', 'create'} for c in adapter.calls))

    def test_restarted_service_owned_process_with_new_pid_is_verified_as_restored(self):
        from zapret_control import transfer_to_netpilot
        adapter = TransferAdapter([self.config()], fail=(3, 'before'))
        snapshot = adapter.snapshot
        pid = [11]
        def with_process():
            current = snapshot()
            items = [replace(i, service=replace(i.service, pid=pid[0] if i.active else None)) for i in current.items]
            if any(i.active for i in items):
                items.append(process(pid[0], create_time=float(pid[0])))
            return replace(current, items=tuple(items))
        adapter.snapshot = with_process
        restore = adapter.restore_service
        def restart(backup):
            restore(backup)
            pid[0] = 99
        adapter.restore_service = restart
        result = transfer_to_netpilot(self.request(adapter), adapter)
        self.assertEqual(result.status, 'rolled_back', result.errors)

    def test_corrupt_recovery_journal_is_preserved_without_system_changes(self):
        from zapret_control import transfer_to_netpilot
        self.storage.mkdir()
        path = self.storage / 'zapret-transfer-v1.json'
        path.write_text('not json')
        adapter = TransferAdapter([self.config()])
        result = transfer_to_netpilot(self.request(adapter), adapter)
        self.assertFalse(result.success)
        self.assertEqual(adapter.calls, [])
        self.assertEqual(path.read_text(), 'not json')

    def test_cleanup_and_restore_failure_results_follow_verified_state(self):
        from zapret_control import transfer_to_netpilot
        # Start mutates and raises, then recovery tries stop, delete, restore.
        for index, phase, expected in ((5, 'before', 'manual_recovery_required'),
                (5, 'after', 'manual_recovery_required'), (6, 'before', 'manual_recovery_required'),
                (6, 'after', 'rolled_back'), (7, 'before', 'manual_recovery_required'),
                (7, 'after', 'rolled_back')):
            with self.subTest(index=index, phase=phase):
                original = self.config()
                adapter = TransferAdapter([original], fail=[(4, 'after'), (index, phase)])
                result = transfer_to_netpilot(self.request(adapter), adapter)
                self.assertEqual(result.status, expected, result.errors)
                if expected == 'rolled_back':
                    self.assertEqual(adapter.configs['zapret'], original)
                else:
                    self.assertNotEqual(adapter.configs.get('zapret'), original)
                self.assertTrue((self.storage / 'zapret-transfer-v1.json').is_file())

    def test_concurrent_creator_service_is_never_deleted_by_failed_creation_cleanup(self):
        from zapret_control import ServiceBackup, transfer_to_netpilot
        adapter = TransferAdapter()
        def race(_validated):
            adapter.configs['zapret'] = ServiceBackup(
                'zapret', f'"{self.root / "bin" / "winws.exe"}" --wf-tcp=443 --dpi-desync=fake',
                'automatic', 'stopped', 'zapret', 'Other transaction',
                {'account': 'LocalSystem', 'owned': True, 'transaction_id': 'other-transaction'})
            raise RuntimeError('service already exists')
        adapter.create_netpilot_service = race

        result = transfer_to_netpilot(self.request(adapter), adapter)

        self.assertEqual(result.status, 'manual_recovery_required', result.errors)
        self.assertIn('zapret', adapter.configs)
        self.assertEqual(adapter.configs['zapret'].adapter_fields['transaction_id'], 'other-transaction')
        self.assertFalse(any(call[0] == 'delete' for call in adapter.calls))

    def test_matching_service_with_mismatched_running_process_never_reports_success(self):
        from zapret_control import transfer_to_netpilot
        adapter = TransferAdapter([self.config(owned=True, state='running')])
        snapshot = adapter.snapshot
        def with_observed_process():
            current = snapshot()
            service_item = next(item for item in current.items if item.service)
            if not service_item.active or not service_item.pid:
                return current
            executable = service_item.service.command.split()[0].strip('"')
            observed = process(service_item.pid, cmdline=(executable, '--wf-tcp=80', '--dpi-desync=fake'))
            return replace(current, items=current.items + (observed,))
        adapter.snapshot = with_observed_process

        result = transfer_to_netpilot(self.request(adapter), adapter)

        self.assertFalse(result.success)
        self.assertFalse(any(call[0] in {'delete', 'create'} for call in adapter.calls))

    def test_verified_rollback_removes_only_backup_entries_added_by_that_transaction(self):
        from zapret_control import load_backup, transfer_to_netpilot
        external = self.config()
        custom = self.config('Custom Zapret')
        failed = TransferAdapter([external, custom], fail=(6, 'before'))

        first = transfer_to_netpilot(self.request(failed), failed)

        self.assertEqual(first.status, 'rolled_back', first.errors)
        self.assertEqual(load_backup().services, {})
        failed.configs['Custom Zapret'] = replace(failed.configs['Custom Zapret'], start_mode='manual')
        failed.fail.clear()
        second = transfer_to_netpilot(self.request(failed), failed)
        self.assertTrue(second.success, second.errors)
        self.assertEqual(load_backup().services, {'Custom Zapret': 'manual'})


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
