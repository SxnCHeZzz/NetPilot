"""Render and lifecycle tests for NetPilot's original vector icon system."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

try:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

if HAVE_QT:
    from ui_icons import AnimatedIcon, IconKind, IconState
    import ui_icons
    import app as ui
    import managed
    from zapret_inventory import ProcessRecord, ZapretItem, ZapretSnapshot


def alpha_signature(pixmap):
    image = pixmap.toImage()
    return tuple(image.pixelColor(x, y).alpha()
                 for y in range(image.height()) for x in range(image.width()))


def alpha_centroid_x(pixmap):
    image=pixmap.toImage();points=[]
    for y in range(image.height()):
        for x in range(image.width()):
            alpha=image.pixelColor(x,y).alpha()
            if alpha:points.extend([x]*alpha)
    return sum(points)/len(points)


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

    def test_service_identity_and_action_icons_are_distinct_vectors(self):
        required=('TELEGRAM','COMPONENTS','WARNING','ACTIVITY','LAYERS','WINDOWS')
        for name in required:
            self.assertTrue(hasattr(IconKind,name),f'missing original {name.lower()} icon')
        signatures=[]
        for name in required:
            icon=AnimatedIcon(getattr(IconKind,name),32)
            icon.set_state(IconState.ENABLED,animate=False)
            signatures.append(alpha_signature(icon.render_pixmap(32,2.0)))
        self.assertEqual(len(set(signatures)),len(required))


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

    def test_active_warp_pulse_and_hidden_widget_stops_all_motion(self):
        icon=AnimatedIcon(IconKind.CLOUD)
        icon.show();icon.set_state(IconState.ENABLED)
        for _ in range(25):
            if icon.motion_progress>0.0:
                break
            QTest.qWait(10)
        self.assertTrue(icon.is_animating())
        self.assertTrue(hasattr(icon,'motion_progress'),'public motion progress is missing')
        self.assertGreater(icon.motion_progress,0.0)
        icon.hide();self.app.processEvents()
        self.assertFalse(icon.is_animating())

    def test_premium_icon_button_animates_hover_and_press(self):
        button_type=getattr(ui_icons,'PremiumIconButton',None)
        self.assertIsNotNone(button_type,'premium icon button is missing')
        button=button_type(IconKind.BACK,'Назад')
        button.show();self.app.processEvents()
        QTest.mouseMove(button,button.rect().center());QTest.qWait(160)
        self.assertGreater(button.hover_progress,.8)
        QTest.mousePress(button,Qt.MouseButton.LeftButton,pos=button.rect().center())
        for _ in range(25):
            if button.press_progress>.1:
                break
            QTest.qWait(10)
        self.assertGreater(button.press_progress,.1)
        QTest.mouseRelease(button,Qt.MouseButton.LeftButton,pos=button.rect().center())
        button.hide();button.deleteLater()

    def test_compact_header_icon_motion_is_precise_and_bounded(self):
        back=AnimatedIcon(IconKind.BACK,16);back.set_state(IconState.DISABLED,animate=False)
        back.interaction_progress=0.0;rest=back.render_pixmap(16,1.0)
        back.interaction_progress=1.0;hover=back.render_pixmap(16,1.0)
        self.assertAlmostEqual(alpha_centroid_x(hover)-alpha_centroid_x(rest),0.0,delta=.55)
        self.assertEqual(ui_icons.GEAR_HOVER_DEGREES,20)

    def test_text_action_and_component_tab_paint_without_qfont_type_errors(self):
        action=ui_icons.IconTextButton(IconKind.COMPONENTS,'Компоненты')
        tab=ui_icons.ComponentTabButton(IconKind.TELEGRAM,'Telegram','Работает')
        action.resize(130,36);tab.resize(92,48)
        action.show();tab.show();self.app.processEvents()
        action.paintEvent(None);tab.paintEvent(None)
        action.hide();tab.hide();action.deleteLater();tab.deleteLater()


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
        premium_button=getattr(ui_icons,'PremiumIconButton',None)
        self.assertIsNotNone(premium_button)
        self.assertEqual(self.window.back_btn.size().toTuple(),(34,34))
        self.assertEqual(self.window.back_btn.visual_size,30)
        self.assertEqual(self.window.back_btn.icon_view.size().toTuple(),(16,16))
        self.assertFalse(self.window.back_btn.icon_view.idle_glow)
        self.assertEqual(self.window.header_layout.spacing(),7)
        self.assertIsInstance(self.window.back_btn,premium_button)
        self.assertEqual(self.window.back_btn.accessibleName(),'Назад')
        self.assertIsInstance(self.window.back_btn.icon_view,AnimatedIcon)
        expected={'zapret':IconKind.SHIELD,'telegram':IconKind.TELEGRAM,'warp':IconKind.CLOUD}
        for name,kind in expected.items():
            icon=self.window.detail_status_labels[name][0]
            self.assertIsInstance(icon,AnimatedIcon)
            self.assertEqual(icon.kind,kind)

    def test_compact_header_icons_render_at_windows_scaling_factors(self):
        for button in (self.window.back_btn,self.window.settings_btn,self.window.hide_btn):
            for dpr in (1.0,1.25,1.5):
                with self.subTest(kind=button.kind,dpr=dpr):
                    pixmap=button.icon_view.render_pixmap(16,dpr)
                    self.assertEqual((pixmap.width(),pixmap.height()),(round(16*dpr),round(16*dpr)))
                    self.assertFalse(pixmap.isNull())

    def test_tabs_brand_components_and_primary_actions_use_vector_system(self):
        tab_type=getattr(ui_icons,'ComponentTabButton',None)
        text_button=getattr(ui_icons,'IconTextButton',None)
        self.assertIsNotNone(tab_type);self.assertIsNotNone(text_button)
        self.assertEqual(self.window.brand_icon.kind,IconKind.BRAND)
        expected={'zapret':IconKind.SHIELD,'telegram':IconKind.TELEGRAM,'warp':IconKind.CLOUD}
        for key,kind in expected.items():
            self.assertIsInstance(self.window.detail_tabs[key],tab_type)
            self.assertEqual(self.window.detail_tabs[key].icon_view.kind,kind)
        # Maintenance is intentionally not duplicated on the main screen.
        self.assertFalse(hasattr(self.window,'install_button'))
        self.assertEqual(self.window.settings_nav[1].text(),'Компоненты')
        self.assertEqual(self.window.test_button.icon_view.kind,IconKind.STRATEGY)

    def test_component_tabs_follow_semantic_state_instead_of_looking_enabled_by_default(self):
        self.assertEqual(self.window.detail_tabs['telegram'].icon_view.state,IconState.DISABLED)
        self.assertEqual(self.window.detail_tabs['warp'].icon_view.state,IconState.DISABLED)
        item=ZapretItem('process','external','confirmed',True,('a','b'),
                        process=ProcessRecord(78,'winws.exe',r'C:\Flowseal\bin\winws.exe',
                                              ('--wf-tcp=443',),78.0))
        self.window.apply_zapret_snapshot(ZapretSnapshot((item,)))
        self.assertEqual(self.window.detail_tabs['zapret'].icon_view.state,IconState.EXTERNAL)

    def test_external_zapret_uses_external_lock_state(self):
        item=ZapretItem('process','external','confirmed',True,('a','b'),
                        process=ProcessRecord(77,'winws.exe',r'C:\Flowseal\bin\winws.exe',
                                              ('--wf-tcp=443','--dpi-desync=fake'),77.0))
        self.window.apply_zapret_snapshot(ZapretSnapshot((item,)))
        self.assertEqual(self.window.detail_status_labels['zapret'][0].state,IconState.EXTERNAL)


if __name__ == '__main__':
    unittest.main()
