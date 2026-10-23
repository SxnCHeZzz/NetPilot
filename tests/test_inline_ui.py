"""Regression tests for the single-window UI (QT_QPA_PLATFORM=offscreen)."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
try:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QDialog
    import app as ui
    import managed
    QT_AVAILABLE=True
except ImportError:
    QT_AVAILABLE=False

@unittest.skipUnless(QT_AVAILABLE,'Requires PySide6/Qt')
class InlineModalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyleSheet(ui.CSS)

    def setUp(self):
        with patch.object(ui.NetPilot, 'on_first_run', lambda self:None), \
             patch.object(ui.NetPilot, 'check_updates', lambda self,*args:None), \
             patch.object(managed,'service_state',return_value='not_installed'), \
             patch.object(managed,'warp_cli',return_value=None):
            self.window=ui.NetPilot()
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.status_timer.stop()
        self.window.daily_timer.stop()
        self.window.tray.hide()
        self.window.hide()
        self.window.pool.shutdown(wait=False,cancel_futures=True)

    def _respond(self, accepted):
        sheet = self.window.centralWidget().findChild(ui.InlineModal)
        self.assertIsNotNone(sheet)
        self.assertTrue(sheet.isVisible())
        sheet.finish(accepted)

    def test_confirmation_remains_in_main_window(self):
        QTimer.singleShot(20, lambda:self._respond(True))
        self.assertTrue(self.window.confirm('Действие','Подтвердить?'))
        self.assertFalse(any(w.isVisible() for w in self.app.topLevelWidgets() if isinstance(w,QDialog)))

    def test_dismiss_confirmation(self):
        QTimer.singleShot(20, lambda:self._respond(False))
        self.assertFalse(self.window.confirm('Действие','Подтвердить?'))

    def test_notice_does_not_open_system_dialog(self):
        QTimer.singleShot(20, lambda:self._respond(True))
        ui.QMessageBox.information(self.window, 'Результат','Проверка завершена')
        self.assertFalse(any(w.isVisible() for w in self.app.topLevelWidgets() if isinstance(w,QDialog)))

    def test_settings_have_only_top_navigation(self):
        self.window.toggle_page()
        self.assertEqual(self.window.settings_btn.kind,'back')
        self.assertFalse(any(b.text().strip()=='←  Вернуться' for b in self.window.findChildren(ui.QPushButton)))
        self.window.toggle_page()
        self.assertEqual(self.window.settings_btn.kind,'settings')

    def test_setup_is_inline(self):
        setup=ui.SetupDialog(self.window,self.window.cfg)
        QTimer.singleShot(20, lambda:self._respond(False))
        self.assertEqual(setup.exec(), QDialog.DialogCode.Rejected)

    def test_expert_is_inline(self):
        expert=ui.ExpertDialog(self.window)
        QTimer.singleShot(20, lambda:self._respond(False))
        self.assertFalse(expert.exec())
