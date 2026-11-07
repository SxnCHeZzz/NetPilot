import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from operation_policy import has_foreground_work, humanize_zapret_error


class OperationPolicyTests(unittest.TestCase):
    def test_background_jobs_do_not_block_manual_selection(self):
        self.assertFalse(has_foreground_work({'updates','warpstatus','autohealth', 'zapret_inventory:29'}))

    def test_mutating_jobs_remain_blocking(self):
        for job in ('install','test','activate','stop','service','warp','future_unknown'):
            with self.subTest(job=job):
                self.assertTrue(has_foreground_work({'updates',job}))

    def test_conflicting_service_is_not_silently_replaced(self):
        original='Operation Zapret: transfer: ValueError: name-conflicting zapret service is not in the confirmed inventory'
        value=humanize_zapret_error(original)
        self.assertIn('не изменён',value)
        self.assertIn('Диагностика',value)
        self.assertNotIn('ValueError',value)

    def test_unknown_errors_preserve_information(self):
        self.assertEqual(humanize_zapret_error('new error'), 'new error')

if __name__=='__main__': unittest.main()
