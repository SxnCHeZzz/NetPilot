"""Offscreen smoke test; skipped where the Qt package is not installed."""
import os
import sys
import tempfile
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
                self.assertEqual(window.pages.currentIndex(),2)
                window.toggle_page()
                self.assertEqual(window.pages.currentIndex(),1)
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
                self.assertEqual(window.width(),485)
                self.assertGreaterEqual(window.height(),360)
                self.assertLessEqual(window.height(),650)
            finally:
                window.window_transition.finish_immediately()
                window.tray.hide();window.hide()
                window.pool.shutdown(wait=False,cancel_futures=True)
                window.deleteLater();self.qapp.processEvents()

    @unittest.skipUnless(os.name == 'nt', 'Windows DLL search paths only')
    def test_frozen_qt_and_shiboken_dll_directories_are_registered(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'shiboken6').mkdir();(root/'PySide6').mkdir()
            (root/'icuuc.dll').touch()
            (root/'PySide6'/'Qt6Core.dll').touch()
            (root/'shiboken6'/'shiboken6.abi3.dll').touch()
            (root/'PySide6'/'pyside6.abi3.dll').touch()
            with patch.object(ui.sys,'frozen',True,create=True), \
                 patch.object(ui.sys,'_MEIPASS',str(root),create=True), \
                 patch.dict(ui.os.environ,{'PATH':'original'}), \
                 patch.object(ui.os,'add_dll_directory') as add, \
                 patch.object(ui,'_load_frozen_bridge',side_effect=(99,100,101,102)) as load:
                before=len(ui._FROZEN_DLL_DIR_HANDLES)
                ui._configure_frozen_qt_paths()
                configured_path=ui.os.environ['PATH']
            self.assertEqual(add.call_count,3)
            self.assertEqual(load.call_count,4)
            self.assertEqual(len(ui._FROZEN_DLL_DIR_HANDLES),before+7)
            self.assertTrue(configured_path.endswith('original'))
