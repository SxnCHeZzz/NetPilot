"""Render and lifecycle tests for NetPilot's original vector icon system."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

try:
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

if HAVE_QT:
    from ui_icons import AnimatedIcon, IconKind, IconState
    import app as ui
    import managed
    from zapret_inventory import ProcessRecord, ZapretItem, ZapretSnapshot


def alpha_signature(pixmap):
    image = pixmap.toImage()
    return tuple(image.pixelColor(x, y).alpha()
                 for y in range(image.height()) for x in range(image.width()))


@unittest.skipUnless(HAVE_QT, 'PySide6 needed for icon tests')
class IconRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_every_kind_and_state_renders_at_supported_size_and_dpr(self):
        for kind in IconKind:
            icon = AnimatedIcon(kind)
            for state in IconState:
                icon.set_state(state, animate=False)
                for size, dpr in ((24, 1.0), (28, 1.5), (32, 2.0)):
                    with self.subTest(kind=kind, state=state, size=size, dpr=dpr):
                        pixmap = icon.render_pixmap(size, dpr)
                        self.assertFalse(pixmap.isNull())
                        self.assertEqual((pixmap.width(), pixmap.height()),
                                         (round(size * dpr), round(size * dpr)))
                        self.assertAlmostEqual(pixmap.devicePixelRatio(), dpr)
                        self.assertEqual(pixmap.toImage().pixelColor(0, 0).alpha(), 0)

    def test_lock_geometry_stays_closed_until_enabled_is_confirmed(self):
        icon = AnimatedIcon(IconKind.LOCK)
        icon.set_state(IconState.DISABLED, animate=False)
        disabled = alpha_signature(icon.render_pixmap(32, 1.0))
        icon.set_state(IconState.STARTING, animate=False)
        starting = alpha_signature(icon.render_pixmap(32, 1.0))
        icon.set_state(IconState.ENABLED, animate=False)
        enabled = alpha_signature(icon.render_pixmap(32, 1.0))
        self.assertEqual(starting, disabled)
        self.assertNotEqual(enabled, disabled)


@unittest.skipUnless(HAVE_QT, 'PySide6 needed for icon tests')
class IconAnimationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, AnimatedIcon):
                widget.hide();widget.deleteLater()
        self.app.processEvents()

    def test_disabled_is_stable_and_loading_stops_when_hidden(self):
        icon = AnimatedIcon(IconKind.REFRESH)
        icon.show()
        icon.set_state(IconState.DISABLED)
        self.assertFalse(icon.is_animating())
        icon.set_state(IconState.LOADING)
        QTest.qWait(35)
        self.assertTrue(icon.is_animating())
        icon.hide();self.app.processEvents()
        self.assertFalse(icon.is_animating())

    def test_reversal_starts_from_current_activation_progress(self):
        icon = AnimatedIcon(IconKind.LOCK)
        icon.show();icon.set_state(IconState.ENABLED)
        QTest.qWait(70)
        before = icon.activation_progress
        self.assertGreater(before, 0.0);self.assertLess(before, 1.0)
        icon.set_state(IconState.DISABLED)
        self.assertAlmostEqual(icon.activation_progress, before, delta=.08)
        QTest.qWait(230)
        self.assertAlmostEqual(icon.activation_progress, 0.0, delta=.01)
        self.assertFalse(icon.is_animating())

    def test_terminal_animation_plays_once_and_rapid_changes_finish_latest(self):
        icon = AnimatedIcon(IconKind.SUCCESS)
        icon.show();icon.set_state(IconState.SUCCESS)
        self.assertTrue(icon.is_animating())
        QTest.qWait(280)
        self.assertFalse(icon.is_animating())
        self.assertEqual(icon.state, IconState.SUCCESS)
        icon.set_state(IconState.ENABLED)
        icon.set_state(IconState.ERROR)
        QTest.qWait(280)
        self.assertEqual(icon.state, IconState.ERROR)
        self.assertFalse(icon.is_animating())


@unittest.skipUnless(HAVE_QT, 'PySide6 needed for icon tests')
class IconIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        with patch.object(ui.NetPilot, 'on_first_run', lambda self: None), \
             patch.object(ui.NetPilot, 'check_updates', lambda self, *a: None), \
             patch.object(ui.NetPilot, 'refresh_zapret_inventory', lambda self: None), \
             patch.object(managed, 'service_state', return_value='not_installed'), \
             patch.object(managed, 'warp_cli', return_value=None):
            self.window=ui.NetPilot()

    def tearDown(self):
        for name in ('status_timer','daily_timer','auto_strategy_timer','message_timer'):
            getattr(self.window,name).stop()
        self.window.tray.hide();self.window.pool.shutdown(wait=False,cancel_futures=True)
        self.window.deleteLater();self.app.processEvents()

    def test_header_and_service_icons_keep_layout_and_accessibility(self):
        self.assertEqual(self.window.back_btn.size().toTuple(),(36,36))
        self.assertEqual(self.window.back_btn.accessibleName(),'Назад')
        self.assertIsInstance(self.window.back_btn.icon_view,AnimatedIcon)
        expected={'zapret':IconKind.LOCK,'telegram':IconKind.NETWORK,'warp':IconKind.CLOUD}
        for name,kind in expected.items():
            icon=self.window.detail_status_labels[name][0]
            self.assertIsInstance(icon,AnimatedIcon)
            self.assertEqual(icon.kind,kind)

    def test_external_zapret_uses_external_lock_state(self):
        item=ZapretItem('process','external','confirmed',True,('a','b'),
                        process=ProcessRecord(77,'winws.exe',r'C:\Flowseal\bin\winws.exe',
                                              ('--wf-tcp=443','--dpi-desync=fake'),77.0))
        self.window.apply_zapret_snapshot(ZapretSnapshot((item,)))
        self.assertEqual(self.window.detail_status_labels['zapret'][0].state,IconState.EXTERNAL)


if __name__ == '__main__':
    unittest.main()
