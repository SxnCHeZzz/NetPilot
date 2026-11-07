"""Read-only SCM PID parsing, with no Windows service changes."""
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from windows_zapret import WindowsZapretAdapter


class ServicePidTests(unittest.TestCase):
    def test_running_service_pid_is_read(self):
        adapter = WindowsZapretAdapter()
        adapter._run = lambda *a, **kw: SimpleNamespace(stdout='SERVICE_NAME: zapret\n STATE : 4 RUNNING\n PID : 1234\n')
        self.assertEqual(adapter.get_service_pid('zapret'), 1234)

    def test_stopped_service_has_no_pid(self):
        adapter = WindowsZapretAdapter()
        adapter._run = lambda *a, **kw: SimpleNamespace(stdout='PID : 0\n')
        self.assertIsNone(adapter.get_service_pid('zapret'))

    def test_other_service_cannot_be_queried_as_transaction_service(self):
        adapter = WindowsZapretAdapter()
        with self.assertRaises(ValueError):
            adapter.get_service_pid('Unrelated Service')


if __name__ == '__main__':
    unittest.main()

class ServicePendingTests(unittest.TestCase):
    def test_start_waits_for_running_after_scm_pending(self):
        adapter = WindowsZapretAdapter(timeout=3)
        calls = []
        adapter._run = lambda argv, **kw: calls.append(tuple(argv))
        states = iter(('pending', 'stopped', 'running'))
        adapter._service_state = lambda name: next(states)
        from unittest.mock import patch
        with patch('windows_zapret.time.sleep', lambda _: None):
            adapter.start_service('zapret')
        self.assertEqual(calls, [('sc.exe','start','zapret')])

    def test_stop_waits_for_stopped_after_scm_pending(self):
        adapter = WindowsZapretAdapter(timeout=3)
        calls = []
        adapter._run = lambda argv, **kw: calls.append(tuple(argv))
        states = iter(('running', 'stopped'))
        adapter._service_state = lambda name: next(states)
        from unittest.mock import patch
        with patch('windows_zapret.time.sleep', lambda _: None):
            adapter.stop_service('zapret')
        self.assertEqual(calls, [('sc.exe','stop','zapret')])
