"""Targeted Qt visual regression checks for the latest small UI corrections."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
try:
    from PySide6.QtWidgets import QApplication, QLabel
    from PySide6.QtGui import QFont, QFontMetrics
    from ui_icons import IconTextButton, IconKind
    import app as ui
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

@unittest.skipUnless(HAVE_QT, 'PySide6 is required for Qt layout tests')
class PolishRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt=QApplication.instance() or QApplication([])
        cls.qt.setStyleSheet(ui.CSS)

    def setUp(self):
        with patch.object(ui.NetPilot,'on_first_run',lambda *a:None), \
             patch.object(ui.NetPilot,'check_updates',lambda *a:None), \
             patch.object(ui.NetPilot,'refresh_zapret_inventory',lambda *a:None):
            self.window=ui.NetPilot(smoke_mode=True)
        self.window.window_transition.reduced_motion=True
        self.window.show();self.qt.processEvents()

    def tearDown(self):
        for name in ('status_timer','daily_timer','auto_strategy_timer','message_timer','traffic_timer'):
            getattr(self.window,name).stop()
        self.window.window_transition.finish_immediately()
        self.window.tray.hide();self.window.hide()
        self.window.pool.shutdown(wait=False,cancel_futures=True)
        self.window.deleteLater();self.qt.processEvents()

    def test_components_only_in_settings(self):
        self.assertFalse(hasattr(self.window, 'details_install_button'))
        self.assertFalse(hasattr(self.window, 'install_button'))
        self.assertEqual(self.window.settings_nav[1].text(), 'Компоненты')
        self.window.show_components()
        self.assertEqual(self.window.pages.currentIndex(), 2)
        self.assertEqual(self.window.settings_sections.currentIndex(), 1)

    def test_zapret_stop_action_does_not_clip_text(self):
        button=self.window.stop_all_zapret_button
        self.assertGreaterEqual(button.minimumHeight(),36)
        self.assertNotIn('›',button.text())

    def test_telegram_control_caption_matches_warp_style(self):
        labels=self.window.findChildren(QLabel)
        proxy=next(x for x in labels if x.text()=='Включить Telegram Proxy')
        warp=next(x for x in labels if x.text()=='Подключить WARP')
        self.assertNotEqual(proxy.objectName(),'small')
        self.assertEqual(proxy.font().pixelSize(),warp.font().pixelSize())

    def test_tab_does_not_cover_adjacent_label_with_hover_tooltip(self):
        self.window.select_detail(1)
        self.assertEqual(self.window.detail_tabs['telegram'].toolTip(),'')
        self.assertIn('Telegram',self.window.detail_tabs['telegram'].accessibleName())

if __name__=='__main__': unittest.main()
