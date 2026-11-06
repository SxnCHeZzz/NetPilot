"""Cross-platform tests for Windows SCM query parsing and safe status semantics."""
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import managed

class ServiceProbeTests(unittest.TestCase):
    def fake(self, text='', code=0, stderr=''):
        return SimpleNamespace(returncode=code, stdout=text, stderr=stderr)

    def test_service_query_reports_absence_only_on_1060(self):
        with patch.object(managed.os, 'name', 'nt'), patch.object(managed, 'cli_run', return_value=self.fake(code=1060, stderr='FAILED 1060')):
            self.assertEqual(managed.service_state('zapret'), 'not_installed')
        with patch.object(managed.os, 'name', 'nt'), patch.object(managed, 'cli_run', return_value=self.fake(code=5, stderr='FAILED 5 access denied')):
            self.assertEqual(managed.service_state('zapret'), 'access_denied')
        with patch.object(managed.os, 'name', 'nt'), patch.object(managed, 'cli_run', return_value=self.fake(code=87, stderr='FAILED 87')):
            self.assertEqual(managed.service_state('zapret'), 'unknown')

    def test_state_and_start_type_do_not_mix(self):
        with patch.object(managed.os, 'name', 'nt'), patch.object(managed, 'cli_run', return_value=self.fake('STATE : 4 RUNNING')):
            self.assertEqual(managed.service_state('zapret'), 'RUNNING')
        with patch.object(managed.os, 'name', 'nt'), patch.object(managed, 'cli_run', return_value=self.fake('START_TYPE : 2 AUTO_START')):
            self.assertEqual(managed.service_start_mode('zapret'), 'AUTO_START')
        with patch.object(managed.os, 'name', 'nt'), patch.object(managed, 'cli_run', return_value=self.fake('START_TYPE : 3 DEMAND_START')):
            self.assertEqual(managed.service_start_mode('zapret'), 'DEMAND_START')

    def test_query_failure_does_not_mean_service_missing(self):
        with patch.object(managed.os, 'name', 'nt'), patch.object(managed, 'cli_run', side_effect=subprocess.TimeoutExpired('sc.exe', 6)):
            self.assertEqual(managed.service_state('zapret'), 'unknown')
            self.assertEqual(managed.service_start_mode('zapret'), 'unknown')

if __name__ == '__main__': unittest.main()
