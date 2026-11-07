"""Regression for an SCM-owned new service omitted from a lagging CIM inventory.

No Windows services are touched: these tests use the in-memory adapter.
"""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_zapret_control as fixtures
TransferAdapter = fixtures.TransferAdapter
from zapret_control import transfer_to_netpilot
from zapret_inventory import ZapretSnapshot
from dataclasses import replace


class InventoryLagTests(unittest.TestCase):
    setUp = fixtures.TransferTests.setUp
    config = fixtures.TransferTests.config
    request = fixtures.TransferTests.request
    def test_created_owned_service_can_be_rolled_back_if_inventory_omits_it(self):
        class LaggingAdapter(TransferAdapter):
            def snapshot(self):
                snap = super().snapshot()
                # The registry reports a service carrying THIS transaction's
                # marker, but CIM discovery does not list it (Windows race).
                return ZapretSnapshot(tuple(item for item in snap.items if not (
                    item.service and item.service.name == 'zapret' and
                    self.configs['zapret'].adapter_fields.get('transaction_id'))), snap.warnings)

        original = self.config(state='running')
        adapter = LaggingAdapter([original])
        result = transfer_to_netpilot(self.request(adapter), adapter)
        self.assertTrue(result.success, result.errors)
        self.assertNotEqual(adapter.configs['zapret'], original)
        self.assertTrue(adapter.configs['zapret'].adapter_fields.get('transaction_id'))
        self.assertEqual(adapter.configs['zapret'].state, 'running')
        self.assertNotIn(('restore', 'zapret'), adapter.calls)

    def test_running_owned_service_is_stopped_before_rollback_delete(self):
        class LaggingRunningAdapter(TransferAdapter):
            def create_netpilot_service(self, transfer):
                super().create_netpilot_service(transfer)
                self.configs['zapret'] = replace(self.configs['zapret'], state='running')
                raise RuntimeError('injected failure immediately after creation')
            def snapshot(self):
                snap = super().snapshot()
                return ZapretSnapshot(tuple(item for item in snap.items if not (
                    item.service and item.service.name == 'zapret' and
                    self.configs['zapret'].adapter_fields.get('transaction_id'))), snap.warnings)

        original = self.config(state='running')
        adapter = LaggingRunningAdapter([original])
        result = transfer_to_netpilot(self.request(adapter), adapter)
        self.assertFalse(result.success)
        self.assertEqual(adapter.configs['zapret'], original)
        # Stop the newly created instance, not the original one after restore.
        last_stop = max(i for i, call in enumerate(adapter.calls) if call == ('stop','zapret'))
        last_delete = max(i for i, call in enumerate(adapter.calls) if call == ('delete','zapret'))
        self.assertLess(last_stop,last_delete)
        self.assertFalse(any('cleanup NetPilot service' in error for error in result.errors), result.errors)


if __name__ == '__main__':
    unittest.main()
