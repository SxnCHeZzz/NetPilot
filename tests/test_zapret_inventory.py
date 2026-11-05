import sys
from pathlib import Path
import unittest
from unittest.mock import patch
import json
import subprocess
import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from zapret_inventory import (
    ProcessRecord, ServiceRecord, TaskRecord, classify_process, build_snapshot,
)


ROOT = r'C:\NetPilot\tools\zapret'
EXTERNAL = r'D:\Flowseal\zapret-discord-youtube'
ARGS = ('--wf-tcp=80,443', '--dpi-desync=fake',
        r'--hostlist=D:\Flowseal\zapret-discord-youtube\lists\list-general.txt')


class Inventory:
    def __init__(self, processes=(), services=(), tasks=()):
        self.processes, self.services, self.tasks = processes, services, tasks

    def iter_processes(self):
        yield from self.processes

    def iter_services(self):
        yield from self.services

    def iter_tasks(self):
        yield from self.tasks


class TestZapretInventory(unittest.TestCase):
    def test_netpilot_process_is_confirmed(self):
        item = classify_process(ProcessRecord(11, 'WINWS.EXE',
            r'c:/NETPILOT/tools/zapret/bin/../bin/winws.exe'), (ROOT,))
        self.assertEqual((item.origin, item.confidence), ('netpilot', 'confirmed'))
        self.assertTrue(item.active)
        self.assertIsNone(classify_process(ProcessRecord(12, 'python.exe',
            r'C:\NetPilot\tools\zapret\python.exe'), (ROOT,)))

    def test_flowseal_structure_and_arguments_confirm_external_process(self):
        item = classify_process(ProcessRecord(21, 'winws.exe',
            EXTERNAL + r'\bin\winws.exe', ARGS,
            root_entries=('bin', 'lists', 'utils', 'general.bat')), (ROOT,))
        self.assertEqual((item.origin, item.confidence), ('external', 'confirmed'))

    def test_filename_only_is_not_confirmed(self):
        item = classify_process(ProcessRecord(31, 'winws.exe', r'C:\Other\winws.exe'), (ROOT,))
        self.assertEqual(item.confidence, 'ambiguous')
        snapshot = build_snapshot(Inventory((item.process,)), (ROOT,))
        self.assertEqual(len(snapshot.active), 0)
        self.assertEqual(len(snapshot.ambiguous), 1)

    def test_custom_service_and_task_are_linked_by_confirmed_action(self):
        command = '"D:\\Flowseal\\zapret-discord-youtube\\bin\\winws.exe" ' + ' '.join(ARGS)
        snapshot = build_snapshot(Inventory(
            services=(ServiceRecord('custom-service', command, 'running', 21),),
            tasks=(TaskRecord(r'\Custom\Startup', command, 'running'),)), (ROOT,))
        self.assertEqual({item.kind for item in snapshot.active}, {'service', 'task'})
        self.assertTrue(all(item.confidence == 'confirmed' for item in snapshot.active))
        self.assertTrue(all(item.origin == 'external' for item in snapshot.active))

    def test_snapshot_reports_managed_external_multiple_and_ambiguous(self):
        managed = ProcessRecord(11, 'winws.exe', ROOT + r'\bin\winws.exe')
        external = ProcessRecord(21, 'winws.exe', EXTERNAL + r'\bin\winws.exe', ARGS)
        ambiguous = ProcessRecord(31, 'winws.exe', r'C:\Other\winws.exe')
        for records, state, count in [
            ((), 'inactive', 0), ((managed,), 'managed', 1),
            ((external,), 'external', 1), ((managed, external, ambiguous), 'multiple', 2),
            ((ambiguous,), 'ambiguous', 0),
        ]:
            with self.subTest(state=state):
                snapshot = build_snapshot(Inventory(records), (ROOT,))
                self.assertEqual((snapshot.state, snapshot.active_count), (state, count))

    def test_winws2_and_stopped_service_and_running_task(self):
        command = '"C:\\NetPilot\\tools\\zapret\\bin\\winws2.exe" --wf-tcp=443'
        snapshot = build_snapshot(Inventory(
            services=(ServiceRecord('custom', command, 'stopped'),),
            tasks=(TaskRecord('custom task', command, 'running'),)), (ROOT,))
        self.assertEqual(len(snapshot.items), 2)
        self.assertEqual(snapshot.active_count, 1)
        self.assertEqual(snapshot.active[0].kind, 'task')

    def test_duplicate_evidence_does_not_inflate_active_count(self):
        process = ProcessRecord(11, 'winws.exe', ROOT + r'\bin\winws.exe')
        service = ServiceRecord('custom', '"' + ROOT + r'\bin\winws.exe"', 'running', 11)
        snapshot = build_snapshot(Inventory((process, process), (service,)), (ROOT,))
        self.assertEqual(len(snapshot.items), 2)
        self.assertEqual(snapshot.active_count, 1)
        self.assertEqual(snapshot.state, 'managed')

    def test_adapter_failure_retains_partial_records_and_other_sources(self):
        class Partial(Inventory):
            def iter_processes(self):
                yield ProcessRecord(11, 'winws.exe', ROOT + r'\bin\winws.exe')
                raise PermissionError('denied')

            def iter_services(self):
                raise TimeoutError('service query timed out')

        snapshot = build_snapshot(Partial(tasks=(TaskRecord('startup',
            '"' + ROOT + r'\bin\winws.exe"', 'running'),)), (ROOT,))
        self.assertEqual(snapshot.active_count, 2)
        self.assertEqual(len(snapshot.warnings), 2)

    def test_psutil_access_denied_details_remain_ambiguous(self):
        from zapret_inventory import PsutilWindowsInventoryAdapter
        import psutil

        class Process:
            pid = 31

            def name(self):
                return 'winws.exe'

            def exe(self):
                raise psutil.AccessDenied(31)

            cmdline = exe
            create_time = exe

        with patch('psutil.process_iter', return_value=iter((Process(),))), \
             patch('zapret_inventory.subprocess.run', side_effect=subprocess.TimeoutExpired('powershell', 2)):
            snapshot = build_snapshot(PsutilWindowsInventoryAdapter(timeout=2), (ROOT,))
        self.assertEqual(snapshot.active_count, 0)
        self.assertEqual(len(snapshot.ambiguous), 1)
        self.assertTrue(any('AccessDenied' in warning for warning in snapshot.warnings))
        self.assertTrue(any('TimeoutExpired' in warning for warning in snapshot.warnings))

    def test_windows_adapter_normalizes_service_and_task_json(self):
        from zapret_inventory import PsutilWindowsInventoryAdapter
        command = '"C:\\NetPilot\\tools\\zapret\\bin\\winws2.exe" --wf-tcp=443'

        def run(args, **kwargs):
            self.assertEqual(kwargs['timeout'], 2)
            script = args[-1]
            if 'Win32_Service' in script:
                data = [{'Name': 'odd name', 'PathName': command, 'State': 'Running', 'ProcessId': 11}]
            elif 'Get-ScheduledTask' in script:
                data = [{'Name': r'\Mine\Startup', 'Command': command, 'State': 'Running'}]
            else:
                self.fail('unexpected discovery command')
            return types.SimpleNamespace(stdout=json.dumps(data), returncode=0, stderr='')

        with patch('psutil.process_iter', return_value=iter(())), \
             patch('zapret_inventory.subprocess.run', side_effect=run):
            snapshot = build_snapshot(PsutilWindowsInventoryAdapter(timeout=2), (ROOT,))
        self.assertEqual({item.kind for item in snapshot.active}, {'service', 'task'})
        self.assertEqual(snapshot.warnings, ())

    def test_adapter_invalid_json_warns_without_losing_processes(self):
        from zapret_inventory import PsutilWindowsInventoryAdapter

        process = types.SimpleNamespace(
            pid=11,
            name=lambda: 'winws.exe',
            exe=lambda: ROOT + r'\bin\winws.exe',
            cmdline=lambda: (ROOT + r'\bin\winws.exe', '--wf-tcp=443'),
            create_time=lambda: 1.0,
        )
        with patch('psutil.process_iter', return_value=iter((process,))), \
             patch('zapret_inventory.subprocess.run', return_value=types.SimpleNamespace(
                 stdout='not JSON', stderr='', returncode=0)):
            snapshot = build_snapshot(PsutilWindowsInventoryAdapter(), (ROOT,))
        self.assertEqual(snapshot.active_count, 1)
        self.assertEqual(snapshot.active[0].process.pid, 11)
        self.assertEqual(len(snapshot.warnings), 2)

    def test_adapter_retains_successful_rows_alongside_command_failure(self):
        from zapret_inventory import PsutilWindowsInventoryAdapter
        command = '"' + ROOT + r'\bin\winws.exe"'

        def run(args, **kwargs):
            if 'Win32_Service' in args[-1]:
                rows = [{'Name': 'good service', 'PathName': command, 'State': 'Running', 'ProcessId': 11}]
            else:
                rows = [{'Name': r'\Good task', 'Command': command, 'State': 'Running'}]
            return types.SimpleNamespace(stdout=json.dumps(rows), stderr='Access is denied for another object', returncode=1)

        with patch('psutil.process_iter', return_value=iter(())), \
             patch('zapret_inventory.subprocess.run', side_effect=run):
            snapshot = build_snapshot(PsutilWindowsInventoryAdapter(), (ROOT,))
        self.assertEqual({item.kind for item in snapshot.active}, {'service', 'task'})
        self.assertEqual(snapshot.active_count, 2)
        self.assertEqual(len(snapshot.warnings), 2)
        self.assertTrue(all('Access is denied' in warning for warning in snapshot.warnings))

    def test_adapter_retains_rows_and_per_object_errors_from_query_envelope(self):
        from zapret_inventory import PsutilWindowsInventoryAdapter
        command = '"' + ROOT + r'\bin\winws.exe"'

        def run(args, **kwargs):
            if 'Win32_Service' in args[-1]:
                rows = [{'Name': 'good service', 'PathName': command, 'State': 'Running', 'ProcessId': 11}]
            else:
                rows = [{'Name': r'\Good task', 'Command': command, 'State': 'Running'}]
            data = {'Records': rows, 'Warnings': ['Access denied: restricted object']}
            return types.SimpleNamespace(stdout=json.dumps(data), stderr='', returncode=0)

        with patch('psutil.process_iter', return_value=iter(())), \
             patch('zapret_inventory.subprocess.run', side_effect=run):
            snapshot = build_snapshot(PsutilWindowsInventoryAdapter(), (ROOT,))
        self.assertEqual({item.kind for item in snapshot.active}, {'service', 'task'})
        self.assertEqual(len(snapshot.warnings), 2)
        self.assertTrue(all('restricted object' in warning for warning in snapshot.warnings))

    def test_task_multiple_actions_preserve_confirmed_action(self):
        snapshot = build_snapshot(Inventory(tasks=(
            TaskRecord('multi', r'C:\Windows\System32\notepad.exe', 'running'),
            TaskRecord('multi', '"' + ROOT + r'\bin\winws.exe"', 'running'),
        )), (ROOT,))
        self.assertEqual(snapshot.active_count, 1)
        self.assertEqual(snapshot.active[0].task.name, 'multi')

    def test_external_arguments_alone_and_layout_alone_stay_ambiguous(self):
        for record in (
            ProcessRecord(31, 'winws.exe', r'C:\Other\winws.exe', ('--wf-tcp=443', '--dpi-desync=fake')),
            ProcessRecord(32, 'winws.exe', EXTERNAL + r'\bin\winws.exe', root_entries=('bin', 'lists', 'utils')),
            ProcessRecord(33, 'winws.exe', ROOT + r'-other\bin\winws.exe'),
        ):
            with self.subTest(pid=record.pid):
                self.assertEqual(classify_process(record, (ROOT,)).confidence, 'ambiguous')

    def test_quoted_paths_with_spaces_confirm_service(self):
        command = (r'"D:\Flow seal\bin\winws.exe" --wf-tcp=443 --dpi-desync=fake '
                   r'--hostlist="D:\Flow seal\lists\list-general.txt"')
        snapshot = build_snapshot(Inventory(services=(ServiceRecord('custom', command, 'running'),)), (ROOT,))
        self.assertEqual(snapshot.active_count, 1)

    def test_non_running_service_and_ready_task_are_visible_but_inactive(self):
        command = '"' + ROOT + r'\bin\winws.exe"'
        snapshot = build_snapshot(Inventory(
            services=(ServiceRecord('stopped', command, 'stopped'),),
            tasks=(TaskRecord('ready', command, 'ready'),)), (ROOT,))
        self.assertEqual(len(snapshot.items), 2)
        self.assertEqual(snapshot.active_count, 0)


if __name__ == '__main__':
    unittest.main()
