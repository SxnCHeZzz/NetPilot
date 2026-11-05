"""Offscreen regression tests for the RC5 setup and settings UI."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
try:
    from PySide6.QtWidgets import QApplication, QCheckBox, QPushButton
    import app as ui
    import managed
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, 'PySide6 needed for visual interaction tests')
class SetupAndPreferences(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QApplication.instance() or QApplication([])
        cls.qapp.setStyleSheet(ui.CSS)

    def setUp(self):
        with patch.object(ui.NetPilot, 'on_first_run', lambda self: None), \
             patch.object(ui.NetPilot, 'check_updates', lambda self, *a: None), \
             patch.object(ui.NetPilot, 'refresh_zapret_inventory', lambda self: None), \
             patch.object(managed, 'service_state', return_value='not_installed'), \
             patch.object(managed, 'warp_cli', return_value=None):
            self.window = ui.NetPilot()
        self.qapp.processEvents()

    def tearDown(self):
        self.window.status_timer.stop()
        self.window.daily_timer.stop()
        self.window.auto_strategy_timer.stop()
        if hasattr(self.window, 'message_timer'):
            self.window.message_timer.stop()
        self.window.tray.hide()
        self.window.hide()
        self.window.pool.shutdown(wait=False, cancel_futures=True)
        self.window.deleteLater()
        self.qapp.processEvents()

    def test_setup_offers_warp_not_telegram(self):
        with patch.object(managed, 'warp_cli', return_value=None):
            wizard = ui.SetupDialog(self.window, self.window.cfg)
        labels = [widget.text() for widget in wizard.findChildren(QCheckBox)]
        self.assertTrue(any('WARP' in label for label in labels))
        self.assertFalse(any('Telegram' in label for label in labels))
        self.assertTrue(wizard.w.isChecked())
        wizard.deleteLater()

    def test_warp_installed_unticked_by_default(self):
        with patch.object(managed, 'warp_cli', return_value=r'C:\\Program Files\\Cloudflare\\warp-cli.exe'):
            wizard = ui.SetupDialog(self.window, self.window.cfg)
        self.assertFalse(wizard.w.isChecked())
        wizard.deleteLater()

    def test_no_obsolete_settings_switches(self):
        labels = [w.text().lower() for w in self.window.findChildren(__import__('PySide6.QtWidgets',fromlist=['QLabel']).QLabel)]
        self.assertFalse(any('тишина во время игр' in x for x in labels))
        self.assertFalse(any('автоматически применять лидера' in x for x in labels))

    def test_settings_separate_app_and_zapret_startup(self):
        labels = [w.text() for w in self.window.findChildren(__import__('PySide6.QtWidgets',fromlist=['QLabel']).QLabel)]
        self.assertIn('NetPilot вместе с Windows',labels)
        self.assertIn('Zapret вместе с Windows',labels)
        self.assertIn('ВЫКЛ',labels)
        self.assertIn('#66E3A4', ui.CSS)
        for active in range(4):
            self.assertFalse(ui.status_icon(active_count=active).isNull())
        self.assertFalse(ui.status_icon(busy=True).isNull())
        self.assertFalse(ui.status_icon(error=True).isNull())
        self.window._component_states = {
            'zapret': (True, 'Работает'),
            'telegram': (False, 'Готов'),
            'warp': (True, 'Подключён'),
        }
        self.window._update_tray()
        self.assertEqual(self.window._tray_active_count, 2)
        self.assertEqual(self.window.tray_summary.text(), 'Активно 2 из 3')
        self.assertTrue(self.window.tray_actions['zapret'].isChecked())
        self.assertFalse(self.window.tray_actions['telegram'].isChecked())
        self.assertTrue(self.window.tray_actions['warp'].isChecked())
        self.assertIn('Активно 2 из 3', self.window.tray.toolTip())

    def test_compact_details_switches_one_panel(self):
        self.assertEqual((self.window.width(), self.window.height()), (324, 414))
        self.window.open_details()
        self.assertEqual(self.window.pages.currentIndex(), 1)
        self.window.select_detail(1)
        self.assertEqual(self.window.detail_stack.currentIndex(), 1)
        self.assertTrue(self.window.detail_tabs['telegram'].isChecked())
        self.assertFalse(self.window.detail_tabs['zapret'].isChecked())
        self.assertEqual(self.window.detail_stack.count(), 3)
        self.assertNotIn('встроен', self.window.detail_tabs['telegram'].text().lower())
        failed={'passed':1,'total':8,'latency_ms':None,'probes':[]}
        with patch.object(self.window,'start_test') as start, \
             patch.object(ui.m,'service_state',return_value='not_installed'), \
             patch.object(ui.b,'is_admin',return_value=True):
            self.window.managed_pids=[123]
            self.window.completed('autohealth',failed,'')
            self.assertFalse(start.called)
            self.window.managed_pids=[123]
            self.window.completed('autohealth',failed,'')
            self.qapp.processEvents()
            start.assert_called_once_with(True)

    def test_detail_cards_expose_only_distinct_actions(self):
        actions=[button.text() for button in self.window.findChildren(QPushButton)]
        self.assertIn('Подобрать лучшую стратегию  ›',actions)
        self.assertFalse(any('Telegram Desktop' in text for text in actions))
        self.assertFalse(any('статус WARP' in text for text in actions))
        self.assertTrue(self.window.test_details.isHidden())

    def test_background_status_is_silent_and_notices_expire(self):
        self.window.completed('warpstatus','Status update: Disconnected','')
        self.assertTrue(self.window.message.isHidden())
        self.window.note('Telegram Proxy включён')
        self.assertFalse(self.window.message.isHidden())
        self.assertTrue(self.window.message_timer.isActive())

    def test_notice_replaces_details_footer_instead_of_shrinking_card(self):
        self.window.open_details()
        self.window.note('Все компоненты актуальны')
        self.assertTrue(self.window.details_footer.isHidden())
        self.window._hide_message()
        self.assertTrue(self.window.message.isHidden())
        self.assertFalse(self.window.details_footer.isHidden())

    def test_failed_background_warp_poll_is_silent(self):
        self.window.completed('warpstatus',None,'daemon unavailable')
        self.assertTrue(self.window.message.isHidden())
        self.assertEqual(self.window.detail_facts['warp']['Подключение'].text(),'Недоступно')

    def test_foreign_flowseal_service_can_be_explicitly_transferred(self):
        with patch.object(managed,'service_state',return_value='RUNNING'), \
             patch.object(managed,'zapret_service_owned',return_value=False):
            self.window.refresh_service_setting()
        self.assertTrue(self.window.zapret_service_toggle.isChecked())
        self.assertTrue(self.window.zapret_service_toggle.isEnabled())
        self.assertEqual(self.window.service_badge.text(),'СЛУЖБА FLOWSEAL')
        self.assertIn('передать',self.window.zapret_service_toggle.toolTip().lower())

    def test_wizard_starts_both_installs_when_selected(self):
        class DummySetup:
            class path:
                @staticmethod
                def text(): return r'C:\\NetPilot'
            class z:
                @staticmethod
                def isChecked(): return True
            class w:
                @staticmethod
                def isChecked(): return True
            def __init__(self, *_): pass
            def exec(self): return ui.QDialog.DialogCode.Accepted
        submitted=[]
        with patch.object(ui, 'SetupDialog', DummySetup), \
             patch.object(ui.m, 'write_cfg'), \
             patch.object(ui.m, 'release', return_value={'kind':'zapret','tag':'1.0'}), \
             patch.object(ui.m, 'install_component'), \
             patch.object(ui.m, 'warp_cli', return_value=None), \
             patch.object(ui.m, 'install_warp_silently', return_value='ok'), \
             patch.object(self.window, 'job', side_effect=lambda key,fn:submitted.append((key,fn))):
            self.window.install_dialog()
            self.assertEqual(submitted[0][0],'install')
            result=submitted[0][1]()
            self.assertEqual(len(result['installed']),2)
            self.assertEqual(result['failures'],[])

if __name__ == '__main__': unittest.main()
