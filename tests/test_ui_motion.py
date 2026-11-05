"""Behavior tests for confirmed toggles and window-to-tray motion."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

try:
    from PySide6.QtCore import QRect
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QWidget
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

if HAVE_QT:
    from ui_motion import AnimatedToggle, MotionState, MotionTokens, WindowTransition
    import app as ui
    import managed


@unittest.skipUnless(HAVE_QT, 'PySide6 needed for motion tests')
class ToggleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def test_request_is_visually_pending_until_backend_confirmation(self):
        toggle=AnimatedToggle(False);toggle.show()
        size=toggle.size()
        toggle.click();QTest.qWait(MotionTokens.TOGGLE_MS+25)
        self.assertTrue(toggle.isChecked())
        self.assertTrue(toggle.pending)
        self.assertFalse(toggle.confirmed)
        self.assertAlmostEqual(toggle.handle_progress,1.0,delta=.02)
        toggle.set_confirmed(True,MotionState.SUCCESS)
        self.assertFalse(toggle.pending);self.assertTrue(toggle.confirmed)
        self.assertEqual(toggle.size(),size)

    def test_backend_error_reverses_from_current_position(self):
        toggle=AnimatedToggle(False);toggle.show();toggle.click();QTest.qWait(70)
        before=toggle.handle_progress
        toggle.set_confirmed(False,MotionState.ERROR)
        self.assertAlmostEqual(toggle.handle_progress,before,delta=.08)
        QTest.qWait(MotionTokens.TOGGLE_MS+30)
        self.assertFalse(toggle.isChecked());self.assertFalse(toggle.confirmed)
        self.assertAlmostEqual(toggle.handle_progress,0.0,delta=.02)

    def test_reduced_motion_is_immediate_and_accessible(self):
        toggle=AnimatedToggle(False,reduced_motion=True);toggle.show();toggle.click()
        self.assertEqual(toggle.handle_progress,1.0)
        toggle.set_confirmed(True)
        self.assertEqual(toggle.accessibleName(),'Переключатель')
        self.assertEqual(toggle.size().toTuple(),(47,26))

    def test_pending_indicator_animates_until_backend_confirms(self):
        toggle=AnimatedToggle(False);toggle.show();toggle.click()
        QTest.qWait(55)
        self.assertTrue(toggle.pending)
        self.assertTrue(hasattr(toggle,'is_busy_animating'),'pending animation API is missing')
        self.assertTrue(hasattr(toggle,'pending_phase'),'pending phase is missing')
        self.assertTrue(toggle.is_busy_animating())
        self.assertGreater(toggle.pending_phase,0.0)
        toggle.set_confirmed(True,MotionState.SUCCESS)
        QTest.qWait(25)
        self.assertFalse(toggle.pending)
        self.assertFalse(toggle.is_busy_animating())


@unittest.skipUnless(HAVE_QT, 'PySide6 needed for motion tests')
class WindowTransitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.window=QWidget();self.window.setGeometry(80,80,324,414);self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.hide();self.window.deleteLater();self.app.processEvents()

    def test_hide_waits_for_animation_and_restore_uses_final_geometry(self):
        motion=WindowTransition(self.window)
        original=QRect(self.window.geometry())
        motion.hide_to_tray()
        QTest.qWait(80);self.assertTrue(self.window.isVisible())
        QTest.qWait(MotionTokens.HIDE_MS);self.assertFalse(self.window.isVisible())
        motion.restore(original)
        self.assertTrue(self.window.isVisible())
        QTest.qWait(MotionTokens.RESTORE_MS+30)
        self.assertEqual(self.window.geometry(),original)
        self.assertAlmostEqual(self.window.windowOpacity(),1.0,delta=.01)

    def test_repeated_requests_cancel_to_latest_state(self):
        motion=WindowTransition(self.window)
        final=QRect(120,100,324,414)
        motion.hide_to_tray();QTest.qWait(45);motion.restore(final)
        QTest.qWait(MotionTokens.RESTORE_MS+40)
        self.assertTrue(self.window.isVisible())
        self.assertEqual(self.window.geometry(),final)

    def test_unavailable_geometry_is_clamped_and_reduced_motion_is_immediate(self):
        motion=WindowTransition(self.window,reduced_motion=True)
        motion.hide_to_tray();self.assertFalse(self.window.isVisible())
        motion.restore(QRect(-100000,-100000,324,414))
        self.assertTrue(self.window.isVisible())
        available=QApplication.primaryScreen().availableGeometry()
        self.assertTrue(available.contains(self.window.geometry().center()))
        self.assertFalse(motion.is_animating())


@unittest.skipUnless(HAVE_QT, 'PySide6 needed for motion tests')
class MotionIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        with patch.object(ui.NetPilot,'on_first_run',lambda self:None), \
             patch.object(ui.NetPilot,'check_updates',lambda self,*a:None), \
             patch.object(ui.NetPilot,'refresh_zapret_inventory',lambda self:None), \
             patch.object(ui,'find_overflow_rect',return_value=None), \
             patch.object(managed,'service_state',return_value='not_installed'), \
             patch.object(managed,'warp_cli',return_value=None):
            self.window=ui.NetPilot()

    def tearDown(self):
        self.window.window_transition.finish_immediately()
        for name in ('status_timer','daily_timer','auto_strategy_timer','message_timer'):
            getattr(self.window,name).stop()
        self.window.tray.hide();self.window.pool.shutdown(wait=False,cancel_futures=True)
        self.window.hide();self.window.deleteLater();self.app.processEvents()

    def test_netpilot_uses_confirmed_toggles_and_cancellable_tray_transition(self):
        self.assertIsInstance(self.window.detail_service_toggle,AnimatedToggle)
        self.assertEqual(self.window.size().toTuple(),(308,392))
        self.window.show();self.app.processEvents()
        self.window.managed_pids=[123]
        self.window.hide_to_tray();QTest.qWait(MotionTokens.HIDE_MS+25)
        self.assertFalse(self.window.isVisible())
        self.window.show_panel();QTest.qWait(MotionTokens.RESTORE_MS+30)
        self.assertTrue(self.window.isVisible())
        self.assertEqual(self.window.managed_pids,[123])


if __name__=='__main__':
    unittest.main()
