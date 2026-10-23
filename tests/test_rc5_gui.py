"""Offscreen regression tests for the RC5 setup and settings UI."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
try:
    from PySide6.QtWidgets import QApplication, QCheckBox, QPushButton
    import app as ui
    import managed
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, 'PySide6 needed for visual interaction tests')
class SetupAndPreferences(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QApplication.instance() or QApplication([])
        cls.qapp.setStyleSheet(ui.CSS)

    def setUp(self):
        with patch.object(ui.NetPilot, 'on_first_run', lambda self: None), \
             patch.object(ui.NetPilot, 'check_updates', lambda self, *a: None), \
             patch.object(managed, 'service_state', return_value='not_installed'), \
             patch.object(managed, 'warp_cli', return_value=None):
            self.window = ui.NetPilot()
        self.qapp.processEvents()

    def tearDown(self):
        self.window.status_timer.stop()
        self.window.daily_timer.stop()
        self.window.tray.hide()
        self.window.hide()
        self.window.pool.shutdown(wait=False, cancel_futures=True)

    def test_setup_offers_warp_not_telegram(self):
        with patch.object(managed, 'warp_cli', return_value=None):
            wizard = ui.SetupDialog(self.window, self.window.cfg)
        labels = [widget.text() for widget in wizard.findChildren(QCheckBox)]
        self.assertTrue(any('WARP' in label for label in labels))
        self.assertFalse(any('Telegram' in label for label in labels))
        self.assertTrue(wizard.w.isChecked())
        wizard.deleteLater()

    def test_warp_installed_unticked_by_default(self):
        with patch.object(managed, 'warp_cli', return_value=r'C:\\Program Files\\Cloudflare\\warp-cli.exe'):
            wizard = ui.SetupDialog(self.window, self.window.cfg)
        self.assertFalse(wizard.w.isChecked())
        wizard.deleteLater()

    def test_no_obsolete_settings_switches(self):
        labels = [w.text().lower() for w in self.window.findChildren(__import__('PySide6.QtWidgets',fromlist=['QLabel']).QLabel)]
        self.assertFalse(any('тишина во время игр' in x for x in labels))
        self.assertFalse(any('автоматически применять лидера' in x for x in labels))

    def test_wizard_starts_both_installs_when_selected(self):
        class DummySetup:
            class path:
                @staticmethod
                def text(): return r'C:\\NetPilot'
            class z:
                @staticmethod
                def isChecked(): return True
            class w:
                @staticmethod
                def isChecked(): return True
            def __init__(self, *_): pass
            def exec(self): return ui.QDialog.DialogCode.Accepted
        submitted=[]
        with patch.object(ui, 'SetupDialog', DummySetup), \
             patch.object(ui.m, 'write_cfg'), \
             patch.object(ui.m, 'release', return_value={'kind':'zapret','tag':'1.0'}), \
             patch.object(ui.m, 'install_component'), \
             patch.object(ui.m, 'warp_cli', return_value=None), \
             patch.object(ui.m, 'install_warp_silently', return_value='ok'), \
             patch.object(self.window, 'job', side_effect=lambda key,fn:submitted.append((key,fn))):
            self.window.install_dialog()
            self.assertEqual(submitted[0][0],'install')
            result=submitted[0][1]()
            self.assertEqual(len(result['installed']),2)
            self.assertEqual(result['failures'],[])

if __name__ == '__main__': unittest.main()
