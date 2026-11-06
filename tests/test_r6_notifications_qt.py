"""R6 offscreen assertions (run on Windows after installing PySide6)."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
try:
    from PySide6.QtWidgets import QApplication
    import app as ui
    QT_OK=True
except ImportError:
    QT_OK=False


@unittest.skipUnless(QT_OK, 'PySide6 required')
class R6NotificationUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setStyleSheet(ui.CSS)

    def setUp(self):
        with patch.object(ui.NetPilot, 'on_first_run', lambda *args:None), \
             patch.object(ui.NetPilot, 'check_updates', lambda *args:None), \
             patch.object(ui.NetPilot, 'refresh_zapret_inventory', lambda *args:None):
            self.panel=ui.NetPilot(smoke_mode=True)
        self.panel.window_transition.reduced_motion=True
        self.panel.show()
        self.qt.processEvents()

    def tearDown(self):
        for timer in ('status_timer','daily_timer','auto_strategy_timer','message_timer','traffic_timer'):
            getattr(self.panel,timer).stop()
        self.panel.window_transition.finish_immediately()
        self.panel.tray.hide()
        self.panel.hide()
        self.panel.pool.shutdown(wait=False,cancel_futures=True)
        self.panel.deleteLater()
        self.qt.processEvents()

    def test_component_tabs_and_notifications_do_not_resize_window(self):
        initial=(self.panel.width(),self.panel.height())
        self.assertEqual(initial,(400,354))
        for tab in range(3):
            self.panel.select_detail(tab)
            self.panel.note('Информационное сообщение')
            self.panel.note('Системная ошибка', True)
            self.qt.processEvents()
            self.assertEqual((self.panel.width(),self.panel.height()),initial)
        self.assertFalse(hasattr(self.panel,'message_card'))

    def test_settings_sections_keep_one_size_and_show_history(self):
        self.panel.show_page(2)
        for i in range(3):
            self.panel.select_settings_section(i)
            self.qt.processEvents()
            self.assertEqual((self.panel.width(),self.panel.height()),(485,526))
        self.panel.note('Тестовая ошибка', True)
        self.panel.update_diagnostics_info()
        self.assertIn('Тестовая ошибка',self.panel.events_info.text())

    def test_notifications_settings_exist(self):
        self.assertIn('notifications_enabled',self.panel.cfg)
        self.assertIn('notify_success',self.panel.cfg)
        self.assertEqual(self.panel.cfg['notifications_enabled'],True)
        self.assertEqual(self.panel.cfg['notify_success'],True)

if __name__=='__main__': unittest.main()
