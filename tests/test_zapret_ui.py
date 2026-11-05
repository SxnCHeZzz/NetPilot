"""Offscreen behavior tests for compact system-wide Zapret controls."""
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
    import managed
    from zapret_control import AutostartBackup, OperationResult, StopMode
    from zapret_inventory import ProcessRecord, ServiceRecord, ZapretItem, ZapretSnapshot
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


def process(pid, origin='netpilot'):
    root = r'C:\NetPilot\zapret' if origin == 'netpilot' else r'C:\Flowseal'
    return ZapretItem(
        'process', origin, 'confirmed', True, ('executable-name', 'distribution-layout'),
        process=ProcessRecord(pid, 'winws.exe', root + r'\bin\winws.exe',
                              ('--wf-tcp=443', '--dpi-desync=fake'), float(pid)))


def service(name='zapret', origin='external', active=True):
    return ZapretItem(
        'service', origin, 'confirmed', active, ('zapret-arguments', 'distribution-layout'),
        service=ServiceRecord(name, r'C:\Flowseal\bin\winws.exe --wf-tcp=443 --dpi-desync=fake',
                              'running' if active else 'stopped', 901 if active else None))


@unittest.skipUnless(HAVE_QT, 'PySide6 needed for UI tests')
class ZapretUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QApplication.instance() or QApplication([])
        cls.qapp.setStyleSheet(ui.CSS)

    def setUp(self):
        with patch.object(ui.NetPilot, 'on_first_run', lambda self: None), \
             patch.object(ui.NetPilot, 'check_updates', lambda self, *a: None), \
             patch.object(ui.NetPilot, 'refresh_zapret_inventory', lambda self: None, create=True), \
             patch.object(managed, 'service_state', return_value='not_installed'), \
             patch.object(managed, 'warp_cli', return_value=None), \
             patch.object(ui, 'load_backup', return_value=None, create=True):
            self.window = ui.NetPilot()
        self.qapp.processEvents()

    def tearDown(self):
        for name in ('status_timer', 'daily_timer', 'auto_strategy_timer', 'message_timer'):
            timer = getattr(self.window, name, None)
            if timer:
                timer.stop()
        self.window.tray.hide()
        self.window.hide()
        self.window.pool.shutdown(wait=False, cancel_futures=True)
        self.window.deleteLater()
        self.qapp.processEvents()

    def test_snapshot_states_never_hide_confirmed_external_activity(self):
        cases = (
            (ZapretSnapshot(), 'disabled', 'Готов', False, False),
            (ZapretSnapshot((process(101),)), 'managed', 'Работает', True, True),
            (ZapretSnapshot((process(102, 'external'),)), 'external',
             'Внешний Zapret работает', False, True),
            (ZapretSnapshot((process(103), process(104, 'external'))), 'multiple',
             'Работают несколько Zapret', True, True),
        )
        for snapshot, semantic, copy, own_checked, stop_visible in cases:
            with self.subTest(semantic=semantic):
                self.window.apply_zapret_snapshot(snapshot)
                self.assertEqual(self.window.zapret_semantic_state, semantic)
                self.assertEqual(self.window.detail_status_labels['zapret'][1].text(), copy)
                self.assertEqual(self.window.tray_actions['zapret'].isChecked(), own_checked)
                self.assertEqual(not self.window.stop_all_zapret_button.isHidden(), stop_visible)

    def test_partial_and_error_have_explicit_copy(self):
        remaining = process(220, 'external')
        snapshot = ZapretSnapshot((remaining,))
        self.window.apply_zapret_operation_result(
            OperationResult('partial', snapshot, (remaining,), ('access denied',)))
        self.assertEqual(self.window.zapret_semantic_state, 'partial')
        self.assertEqual(self.window.detail_status_labels['zapret'][1].text(),
                         'Zapret остановлен не полностью')
        self.assertFalse(self.window.stop_all_zapret_button.isHidden())

        self.window.apply_zapret_inventory_error('timeout')
        self.assertEqual(self.window.zapret_semantic_state, 'error')
        self.assertEqual(self.window.detail_status_labels['zapret'][1].text(),
                         'Не удалось проверить Zapret')

    def test_stop_actions_request_uac_only_after_consent(self):
        self.window.apply_zapret_snapshot(ZapretSnapshot((process(301), service('CustomZapret'))))
        with patch.object(self.window, 'inline_confirm', return_value=False), \
             patch.object(self.window, 'begin_privileged_operation') as begin:
            self.window.stop_all_zapret(StopMode.TEMPORARY)
            begin.assert_not_called()
        with patch.object(self.window, 'inline_confirm', return_value=True), \
             patch.object(self.window, 'begin_privileged_operation') as begin:
            self.window.stop_all_zapret(StopMode.DISABLE_RESTART)
            begin.assert_called_once_with('disable_restart')

    def test_transfer_remains_available_while_own_and_external_zapret_run(self):
        self.window.apply_zapret_snapshot(
            ZapretSnapshot((process(401), process(402, 'external'), service())))
        self.window.managed_pids = [401]
        with patch.object(self.window, 'inline_confirm', return_value=True), \
             patch.object(self.window, 'begin_privileged_operation') as begin:
            self.window.request_zapret_transfer('general (ALT4).bat')
            begin.assert_called_once_with('transfer', 'general (ALT4).bat')

    def test_restore_action_is_conditional_on_pending_backup(self):
        button = self.window.restore_autostart_button
        with patch.object(ui, 'load_backup', return_value=None):
            self.window.refresh_restore_autostart_action()
        self.assertTrue(button.isHidden())
        backup = AutostartBackup({'CustomZapret': 'automatic'}, {})
        with patch.object(ui, 'load_backup', return_value=backup):
            self.window.refresh_restore_autostart_action()
        self.assertFalse(button.isHidden())

    def test_manual_success_offers_service_once_but_automatic_never_does(self):
        result = {'best': {'strategy': 'general (ALT4).bat', 'passed': 8, 'total': 8},
                  'active_pids': [501], 'results': []}
        with patch.object(self.window, 'offer_strategy_autostart') as offer, \
             patch.object(ui.m, 'write_cfg'):
            self.window._automatic_test = False
            self.window.completed('test', result, '')
            offer.assert_called_once_with('general (ALT4).bat')
            offer.reset_mock()
            self.window._automatic_test = True
            self.window.completed('test', result, '')
            offer.assert_not_called()

    def test_strategy_offer_decline_is_remembered_for_session(self):
        with patch.object(self.window, 'request_zapret_transfer') as transfer:
            self.window.offer_strategy_autostart('general (ALT7).bat')
            self.assertFalse(self.window.strategy_offer_widget.isHidden())
            self.window.strategy_offer_decline.click()
            self.window.offer_strategy_autostart('general (ALT7).bat')
        self.assertTrue(self.window.strategy_offer_widget.isHidden())
        transfer.assert_not_called()


if __name__ == '__main__':
    unittest.main()
