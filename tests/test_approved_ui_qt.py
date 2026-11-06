"""Visual-reference acceptance tests. Run on Windows with PySide6 offscreen."""
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
    import managed as m
    import telegram_headless as tg
    from ui_icons import IconKind
    HAVE_QT=True
except ImportError:
    HAVE_QT=False

@unittest.skipUnless(HAVE_QT,'PySide6 needed for visual tests')
class ApprovedInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt=QApplication.instance() or QApplication([])
        cls.qt.setStyleSheet(ui.CSS)

    def setUp(self):
        with patch.object(ui.NetPilot,'on_first_run',lambda self:None), \
             patch.object(ui.NetPilot,'check_updates',lambda self,*a:None), \
             patch.object(ui.NetPilot,'refresh_zapret_inventory',lambda self:None), \
             patch.object(m,'service_state',return_value='not_installed'), \
             patch.object(m,'warp_cli',return_value=None):
            self.window=ui.NetPilot(smoke_mode=True)
        self.window.window_transition.reduced_motion=True
        self.window.show();self.qt.processEvents()

    def tearDown(self):
        for name in ('status_timer','daily_timer','auto_strategy_timer','message_timer','traffic_timer'):
            getattr(self.window,name).stop()
        self.window.window_transition.finish_immediately()
        self.window.tray.hide()
        self.window.hide()
        self.window.pool.shutdown(wait=False,cancel_futures=True)
        self.window.deleteLater();self.qt.processEvents()

    def test_approved_primary_surface_and_icons(self):
        self.assertEqual(self.window.pages.currentIndex(),1)
        self.assertEqual(self.window.width(),400)
        self.assertGreaterEqual(self.window.height(),280)
        self.assertEqual(self.window.detail_status_labels['zapret'][0].kind,IconKind.SHIELD)
        self.assertTrue(self.window.settings_btn.isVisible())
        self.assertEqual(self.window.detail_stack.count(),3)
        self.assertEqual(len(self.window.settings_nav),3)
        self.assertEqual(self.window.settings_sections.count(),3)
        self.assertIn('#7DE7A1',ui.CSS)

    def test_cancelled_telegram_start_restores_confirmed_off_state(self):
        self.window.select_detail(1)
        switch=self.window.controls['telegram'][0]
        with patch.object(self.window,'confirm',return_value=False), \
             patch.object(tg,'spawn') as spawn, \
             patch.object(self.window,'refresh',lambda:None):
            switch.click()
            spawn.assert_not_called()
        self.assertFalse(switch.confirmed)
        self.assertFalse(switch.isChecked())
        self.assertFalse(switch.pending)

    def test_settings_return_to_previous_screen_without_cutting_switches(self):
        self.window.toggle_page()
        self.assertEqual(self.window.pages.currentIndex(),2)
        self.window.select_settings_section(1)
        self.assertTrue(self.window.settings_nav[1].isChecked())
        self.window.back_btn.click()
        self.assertEqual(self.window.pages.currentIndex(),1)
        self.assertTrue(self.window.settings_btn.isVisible())

    def test_progress_does_not_change_window_size(self):
        self.window.test_running=True
        self.window.progress_update(42,'Проверяем ALT3: 42 из 100 адресов')
        self.assertFalse(self.window.progress_box.isHidden())
        self.assertEqual(self.window.test_progress.value(),42)
        self.assertEqual(self.window.height(),self.window._base_size[1])
        self.window.test_running=False
        self.window._hide_completed_progress()
        self.assertTrue(self.window.progress_box.isHidden())

@unittest.skipUnless(HAVE_QT,'PySide6 needed for visual tests')
class LayoutRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt=QApplication.instance() or QApplication([])
        cls.qt.setStyleSheet(ui.CSS)

    def setUp(self):
        with patch.object(ui.NetPilot,'on_first_run',lambda self:None), \
             patch.object(ui.NetPilot,'check_updates',lambda self,*a:None), \
             patch.object(ui.NetPilot,'refresh_zapret_inventory',lambda self:None):
            self.window=ui.NetPilot(smoke_mode=True)
        self.window.window_transition.reduced_motion=True
        self.window.show();self.qt.processEvents()

    def tearDown(self):
        self.window.message_timer.stop();self.window.traffic_timer.stop()
        self.window.window_transition.finish_immediately()
        self.window.tray.hide();self.window.hide()
        self.window.pool.shutdown(wait=False,cancel_futures=True)
        self.window.deleteLater();self.qt.processEvents()

    def test_component_cards_change_height_without_blank_stretch(self):
        self.window.show_page(1)
        self.window.select_detail(1)
        telegram_height=self.window.detail_stack.height()
        self.window.select_detail(2)
        warp_height=self.window.detail_stack.height()
        self.assertLessEqual(telegram_height,260)
        self.assertLessEqual(warp_height,260)
        self.window.select_detail(0)
        normal_height=self.window.detail_stack.height()
        self.window._render_zapret_state('managed','Работает',True,True)
        self.assertEqual(self.window.detail_stack.height(),normal_height)

    def test_settings_sidebar_is_readable_and_has_separate_width(self):
        self.window.show_page(2)
        self.qt.processEvents()
        self.assertEqual(self.window.width(),485)
        nav=self.window.settings_nav
        self.assertEqual(len(nav),3)
        self.assertTrue(nav[1].y()>nav[0].y())
        self.assertTrue(nav[2].y()>nav[1].y())
        for button in nav:
            self.assertGreater(button.width(),110)
            self.assertLessEqual(button.geometry().right(),button.parentWidget().width())

    def test_header_heading_and_tabs_are_packed_without_large_blank_gaps(self):
        # Regression for Windows captures where the title and tab row floated
        # 50-80 px apart because the hidden settings page enlarged the stack.
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QLabel
        self.window.show_page(1)
        self.window.select_detail(0)
        self.qt.processEvents()
        headings = [widget for widget in self.window.findChildren(QLabel)
                    if widget.objectName() == 'heading' and widget.text() == 'Подробности'
                    and widget.isVisible()]
        self.assertEqual(len(headings), 1)
        heading = headings[0]
        top = lambda widget: widget.mapTo(self.window, QPoint(0, 0)).y()
        gap_after_header = top(heading) - (top(self.window.brand_icon) + self.window.brand_icon.height())
        first_tab = self.window.detail_tabs['zapret']
        gap_after_heading = top(first_tab) - (top(heading) + heading.height())
        gap_after_tabs = top(self.window.detail_stack) - (top(first_tab) + first_tab.height())
        self.assertLessEqual(gap_after_header, 32)
        self.assertLessEqual(gap_after_heading, 28)
        self.assertLessEqual(gap_after_tabs, 28)
        self.assertGreaterEqual(gap_after_header, 0)
        self.assertGreaterEqual(gap_after_heading, 0)
        self.assertGreaterEqual(gap_after_tabs, 0)

    def test_unverified_zapret_does_not_claim_everything_is_fine(self):
        self.window._zapret_inventory_ready=False
        self.window.zapret_semantic_state='error'
        self.window._render_home_hero()
        self.assertEqual(self.window.home_title.text(),'Состояние не подтверждено')

if __name__=='__main__':unittest.main()

