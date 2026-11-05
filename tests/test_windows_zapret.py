import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


class WindowsAdapterTests(unittest.TestCase):
    def test_process_mutation_revalidates_creation_time(self):
        from windows_zapret import WindowsZapretAdapter
        process = SimpleNamespace(create_time=lambda: 200.0, terminate=lambda: None)
        with patch('psutil.Process', return_value=process):
            with self.assertRaises(ValueError):
                WindowsZapretAdapter(()).terminate_process(42, create_time=100.0)

    def test_create_service_uses_argument_list_and_transaction_marker(self):
        from windows_zapret import WindowsZapretAdapter
        calls = []
        adapter = WindowsZapretAdapter(())
        adapter._run = lambda argv, **kwargs: calls.append(tuple(argv)) or subprocess.CompletedProcess(argv, 0, '', '')
        adapter._write_service_values = lambda name, values: calls.append(('registry', name, values))
        transfer = SimpleNamespace(image_path=r'"C:\NetPilot\zapret\bin\winws.exe" --wf-tcp=443',
                                   transaction_id='tx-123', request=SimpleNamespace(strategy='general.bat'))

        adapter.create_netpilot_service(transfer)

        create = next(call for call in calls if call[:2] == ('sc.exe', 'create'))
        self.assertIn('binPath=', create)
        marker = next(call for call in calls if call[0] == 'registry')[2]
        self.assertEqual(marker['NetPilotTransaction'], 'tx-123')
        self.assertEqual(marker['NetPilotManaged'], '1')

    def test_service_backup_validation_refuses_unrestorable_accounts(self):
        from windows_zapret import WindowsZapretAdapter
        from zapret_control import ServiceBackup
        backup = ServiceBackup('zapret', r'"C:\x\winws.exe" --wf-tcp=443', 'automatic',
                                'running', 'zapret', '', {'account': r'.\named-user',
                                'dependencies': [], 'service_type': 16, 'error_control': 1,
                                'delayed': False, 'owned': False, 'strategy': '',
                                'transaction_id': '', 'security': '', 'failure_actions': ''})
        with self.assertRaises(ValueError):
            WindowsZapretAdapter(()).validate_service_backup(backup)

    def test_task_commands_never_use_shell(self):
        from windows_zapret import WindowsZapretAdapter
        calls = []
        adapter = WindowsZapretAdapter(())
        adapter._run = lambda argv, **kwargs: calls.append(tuple(argv)) or subprocess.CompletedProcess(argv, 0, '', '')
        adapter.set_task_enabled(r'\Zapret\Startup', False)
        adapter.start_task(r'\Zapret\Startup')
        adapter.stop_task(r'\Zapret\Startup')
        self.assertEqual(calls[0], ('schtasks.exe', '/Change', '/TN', r'\Zapret\Startup', '/Disable'))
        self.assertEqual(calls[1], ('schtasks.exe', '/Run', '/TN', r'\Zapret\Startup'))
        self.assertEqual(calls[2], ('schtasks.exe', '/End', '/TN', r'\Zapret\Startup'))


if __name__ == '__main__':
    unittest.main()
