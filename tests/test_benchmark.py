"""Static, sandbox-safe tests: no WinDivert or network traffic."""
import sys
import tempfile
import os
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import benchmark as b


class StrategyEngineTests(unittest.TestCase):
    def test_rewrite_expected_upstream_start_without_touching_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'general (ALT).bat'
            original = (b'@echo off\r\nstart "zapret: %~n0" /min "%BIN%winws.exe" --wf-tcp=443\r\n')
            source.write_bytes(original)
            changed = b.prepare_hidden_strategy(source)
            self.assertEqual(source.read_bytes(), original)
            self.assertIn(b'start "" /b "%BIN%winws.exe"', changed.read_bytes())
            changed.unlink()

    def test_unknown_upstream_format_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'general.bat'
            source.write_text('start powershell update-driver.ps1', encoding='ascii')
            with self.assertRaises(b.BenchmarkError):
                b.prepare_hidden_strategy(source)

    def test_best_prefers_accessibility_over_speed(self):
        rows = [
            {'strategy':'fast.bat','passed':2,'latency_ms':30},
            {'strategy':'better.bat','passed':3,'latency_ms':210},
            {'strategy':'slow.bat','passed':3,'latency_ms':500},
        ]
        self.assertEqual(b.choose_best(rows)['strategy'],'better.bat')

    def test_nothing_passes_no_best(self):
        self.assertIsNone(b.choose_best([{'passed':0,'latency_ms':None}]))

    def test_partial_http_reachability(self):
        self.assertEqual(b.score([{'ok':True,'ms':200},{'ok':False,'ms':None}]),(1,200))

    def test_existing_service_preflight_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'bin').mkdir();(root/'bin'/'winws.exe').touch()
            (root/'general.bat').write_text('echo test',encoding='ascii')
            with patch.object(b,'os',SimpleNamespace(name='nt',path=os.path)),patch.object(b,'is_admin',return_value=True), \
                 patch.object(b.managed,'service_state',return_value='RUNNING'):
                with self.assertRaisesRegex(b.BenchmarkError,'служба zapret'):
                    b.preflight(root)

if __name__=='__main__':unittest.main()
