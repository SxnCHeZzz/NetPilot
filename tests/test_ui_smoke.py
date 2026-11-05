"""Offscreen smoke test; skipped where the Qt package is not installed."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
try:
    from PySide6.QtWidgets import QApplication
    import app as ui
    import managed
    QT_AVAILABLE=True
except ImportError:
    QT_AVAILABLE=False

@unittest.skipUnless(QT_AVAILABLE,'PySide6 not installed in this environment')
class InterfaceSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp=QApplication.instance() or QApplication([])
        cls.qapp.setStyleSheet(ui.CSS)

    def test_main_and_settings_render(self):
        with patch.object(ui.NetPilot,'on_first_run',lambda self:None), \
             patch.object(ui.NetPilot,'check_updates',lambda self:None), \
             patch.object(managed,'service_state',return_value='not_installed'), \
             patch.object(managed,'warp_cli',return_value=None):
            window=ui.NetPilot()
            try:
                self.assertEqual(window.pages.count(),3)
                self.assertEqual(len(window.controls),3)
                window.open_details()
                window.resize(432,572)
                window.show()
                self.qapp.processEvents()
                self.assertEqual(window.pages.currentIndex(),1)
                window.toggle_page()
                self.assertEqual(window.pages.currentIndex(),0)
                window.toggle_page()
                self.assertEqual(window.pages.currentIndex(),2)
            finally:
                window.status_timer.stop()
                window.daily_timer.stop()
                window.auto_strategy_timer.stop()
                window.message_timer.stop()
                window.window_transition.finish_immediately()
                window.tray.hide()
                window.hide()
                window.pool.shutdown(wait=False,cancel_futures=True)
                window.deleteLater();self.qapp.processEvents()

    def test_packaged_smoke_is_local_and_exits_cleanly(self):
        with patch.object(ui,'find_overflow_rect',return_value=None), \
             patch.object(managed,'service_state',side_effect=AssertionError('no system inventory')), \
             patch.object(managed,'warp_cli',side_effect=AssertionError('no WARP lookup')):
            window=ui.NetPilot(smoke_mode=True)
            try:
                self.assertEqual(ui.run_packaged_smoke(self.qapp,window),0)
                self.assertEqual(window.pages.count(),3)
                self.assertTrue(window.isVisible())
                self.assertEqual(window.size().toTuple(),(324,414))
            finally:
                window.window_transition.finish_immediately()
                window.tray.hide();window.hide()
                window.pool.shutdown(wait=False,cancel_futures=True)
                window.deleteLater();self.qapp.processEvents()
