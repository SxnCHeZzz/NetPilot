"""Compact card and branded toast integration checks for a Windows Qt runner."""
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
    import managed as managed
    HAVE_QT=True
except ImportError:
    HAVE_QT=False


@unittest.skipUnless(HAVE_QT,'PySide6 required')
class R7CompactUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.app.setStyleSheet(ui.CSS)

    def setUp(self):
        with patch.object(ui.NetPilot,'refresh_zapret_inventory',lambda self:None), \
             patch.object(ui.NetPilot,'check_updates',lambda *args:None), \
             patch.object(ui.NetPilot,'on_first_run',lambda *args:None), \
             patch.object(managed,'service_state',return_value='not_installed'), \
             patch.object(managed,'warp_cli',return_value=None):
            self.panel=ui.NetPilot(smoke_mode=True)
        self.panel.window_transition.reduced_motion=True
        self.panel.show();self.app.processEvents()

    def tearDown(self):
        for name in ('status_timer','daily_timer','auto_strategy_timer','message_timer','traffic_timer'):
            getattr(self.panel,name).stop()
        self.panel.window_transition.finish_immediately()
        self.panel.tray.hide();self.panel.hide()
        self.panel.pool.shutdown(wait=False,cancel_futures=True)
        self.panel.deleteLater();self.app.processEvents()

    def test_all_tabs_and_notification_keep_compact_dimensions(self):
        for tab in range(3):
            self.panel.select_detail(tab)
            self.panel.note('Тестовое сообщение')
            self.app.processEvents()
            self.assertEqual((self.panel.width(),self.panel.height()),(400,354))
            self.assertEqual(self.panel.detail_stack.height(),184)

    def test_controls_are_not_pushed_down_by_elastic_gap(self):
        self.panel.select_detail(1)
        self.app.processEvents()
        toggle=self.panel.controls['telegram'][0]
        card=self.panel.detail_stack.currentWidget()
        self.assertLess(toggle.mapTo(card, toggle.rect().topLeft()).y(),
                        card.height()-50)

    def test_named_brand_icon_is_available(self):
        self.assertFalse(ui.app_icon().isNull())
        self.assertFalse(ui.status_icon(error=True).isNull())

if __name__=='__main__':unittest.main()
