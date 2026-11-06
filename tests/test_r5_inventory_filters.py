"""R5 regression tests: do not confuse inaccessible OS processes with Zapret."""
import json
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from zapret_inventory import PsutilWindowsInventoryAdapter, build_snapshot


class InventoryFilterTests(unittest.TestCase):
    def test_protected_non_zapret_process_does_not_poison_inventory(self):
        import psutil

        class Protected:
            pid = 236
            def name(self):
                raise psutil.AccessDenied(self.pid)
            def exe(self):
                raise AssertionError('never inspect unknown process executable')
            def cmdline(self):
                raise AssertionError('never inspect unknown process cmdline')
            def create_time(self):
                raise AssertionError('never inspect unknown process create_time')

        with patch('psutil.process_iter', return_value=iter((Protected(),))), \
             patch.object(PsutilWindowsInventoryAdapter, 'iter_services', return_value=iter(())), \
             patch.object(PsutilWindowsInventoryAdapter, 'iter_tasks', return_value=iter(())):
            snapshot = build_snapshot(PsutilWindowsInventoryAdapter(), ())
        self.assertEqual(snapshot.warnings, ())
        self.assertEqual(snapshot.active_count, 0)

    def test_cim_fallback_can_identify_named_winws(self):
        import psutil
        exe = r'C:\\NetPilot\\tools\\zapret\\bin\\winws.exe'
        class Protected:
            pid = 563
            def name(self):
                return 'winws.exe'
            def exe(self):
                raise psutil.AccessDenied(self.pid)
            def cmdline(self):
                raise psutil.AccessDenied(self.pid)
            def create_time(self):
                return 10.0
        data = {'ProcessId': 563, 'Name': 'winws.exe', 'ExecutablePath': exe,
                'CommandLine': '"'+exe+'" --wf-tcp=443 --dpi-desync=fake'}
        with patch('psutil.process_iter', return_value=iter((Protected(),))), \
             patch('zapret_inventory.subprocess.run', return_value=types.SimpleNamespace(
                 returncode=0, stdout=json.dumps(data), stderr='')), \
             patch.object(PsutilWindowsInventoryAdapter, 'iter_services', return_value=iter(())), \
             patch.object(PsutilWindowsInventoryAdapter, 'iter_tasks', return_value=iter(())):
            snapshot = build_snapshot(PsutilWindowsInventoryAdapter(), (r'C:\\NetPilot\\tools\\zapret',))
        self.assertEqual(snapshot.active_count, 1)
        self.assertFalse(any('cmdline: AccessDenied' in w for w in snapshot.warnings))
        self.assertFalse(any('exe: AccessDenied' in w for w in snapshot.warnings))

    def test_cim_fallback_rejects_mismatched_identity(self):
        with patch('zapret_inventory.subprocess.run', return_value=types.SimpleNamespace(
                returncode=0, stdout=json.dumps({'ProcessId': 100, 'Name': 'notepad.exe',
                    'ExecutablePath':r'C:\\Windows\\notepad.exe', 'CommandLine':'bad'}), stderr='')):
            self.assertEqual(PsutilWindowsInventoryAdapter()._cim_process_details(100), {})


if __name__ == '__main__':
    unittest.main()
