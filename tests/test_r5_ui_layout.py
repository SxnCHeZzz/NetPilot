"""R5 UX acceptance checks for the approved compact NetPilot design."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
try:
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication, QLabel
    import app as ui
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

@unittest.skipUnless(HAVE_QT, 'PySide6 is required to test layout rendering')
class R5LayoutRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setStyleSheet(ui.CSS)

    def setUp(self):
        with patch.object(ui.NetPilot, 'on_first_run', lambda *args: None), \
             patch.object(ui.NetPilot, 'check_updates', lambda *args: None), \
             patch.object(ui.NetPilot, 'refresh_zapret_inventory', lambda *args: None):
            self.window = ui.NetPilot(smoke_mode=True)
        self.window.window_transition.reduced_motion = True
        self.window.show()
        self.qt.processEvents()

    def tearDown(self):
        for key in ('status_timer', 'daily_timer', 'auto_strategy_timer',
                    'message_timer', 'traffic_timer'):
            getattr(self.window, key).stop()
        self.window.window_transition.finish_immediately()
        self.window.tray.hide()
        self.window.hide()
        self.window.pool.shutdown(wait=False, cancel_futures=True)
        self.window.deleteLater()
        self.qt.processEvents()

    def test_warning_uses_event_journal_and_keeps_window_size(self):
        before=self.window.size()
        self.window.note('Тестовое предупреждение', True)
        self.qt.processEvents()
        self.assertEqual(self.window.size(),before)
        self.assertFalse(hasattr(self.window,'message_card'))
        self.assertIn('Тестовое предупреждение',self.window.event_journal.recent_text())

    def test_header_to_tabs_gap_is_stable_with_notice(self):
        self.window.select_detail(0)
        self.qt.processEvents()
        heading = next(x for x in self.window.findChildren(QLabel)
                       if x.objectName()=='heading' and x.text()=='Подробности' and x.isVisible())
        tab = self.window.detail_tabs['zapret']
        top=lambda widget: widget.mapTo(self.window, QPoint(0, 0)).y()
        before=top(tab) - (top(heading)+heading.height())
        self.window.note('Проверка состояния сети неполна', True)
        self.qt.processEvents()
        after=top(tab) - (top(heading)+heading.height())
        self.assertEqual(before, after)
        self.assertLessEqual(after, 24)

    def test_components_exist_only_under_settings(self):
        self.assertFalse(hasattr(self.window, 'details_install_button'))
        self.window.show_components()
        self.assertEqual(self.window.pages.currentIndex(), 2)
        self.assertEqual(self.window.settings_sections.currentIndex(), 1)

    def test_modal_is_horizontally_centered_in_parent(self):
        popup=ui.InlineModal(self.window, 'Проверка', 'Тест')
        popup.show()
        self.qt.processEvents()
        pane_mid=popup.card.geometry().center().x()
        self.assertLessEqual(abs(pane_mid-popup.width()/2), 3)
        popup.owner.centralWidget().removeEventFilter(popup)
        popup.hide()
        popup.deleteLater()

if __name__ == '__main__':
    unittest.main()
