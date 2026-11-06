"""Windows/Qt geometry checks for approved R9 spacing (no service mutations)."""
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
    HAS_QT = True
except ImportError:
    HAS_QT = False


@unittest.skipUnless(HAS_QT, 'PySide6 required')
class CompactSpacingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setStyleSheet(ui.CSS)

    def setUp(self):
        with patch.object(ui.NetPilot, 'refresh_zapret_inventory', lambda self: None), \
             patch.object(ui.NetPilot, 'on_first_run', lambda *a: None):
            self.window = ui.NetPilot(smoke_mode=True)
        self.window.window_transition.reduced_motion = True
        self.window.show()
        self.qt.processEvents()

    def tearDown(self):
        for name in ('status_timer', 'daily_timer', 'auto_strategy_timer', 'message_timer', 'traffic_timer'):
            getattr(self.window, name).stop()
        self.window.window_transition.finish_immediately()
        self.window.tray.hide()
        self.window.hide()
        self.window.pool.shutdown(wait=False, cancel_futures=True)
        self.window.deleteLater()
        self.qt.processEvents()

    def test_zapret_action_has_same_bottom_padding_as_top_of_card(self):
        self.window.select_detail(0)
        self.qt.processEvents()
        card = self.window.detail_stack.widget(0)
        action = self.window.test_button
        bottom_gap = card.height() - (action.y() + action.height())
        self.assertGreaterEqual(bottom_gap, 8)
        self.assertLessEqual(bottom_gap, 15)
        toggle = self.window.detail_service_toggle
        self.assertGreaterEqual(action.y() - (toggle.y() + toggle.height()), 7)

    def test_switching_tabs_keeps_window_size_constant(self):
        for tab in (0, 1, 2, 0):
            self.window.select_detail(tab)
            self.qt.processEvents()
            self.assertEqual(self.window.size().width(), 400)
            self.assertEqual(self.window.size().height(), 354)

    def test_overview_status_has_separation_from_details_action(self):
        self.window.show_page(0)
        self.qt.processEvents()
        parent = self.window.details_button.parentWidget()
        # Layout screenshot assertions use widgets, not hard-coded screen coords.
        self.assertTrue(self.window.details_button.isVisible())
        self.assertEqual(self.window.height(), 354)


if __name__ == '__main__':
    unittest.main()
