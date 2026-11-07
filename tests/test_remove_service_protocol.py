"""The elevated removal request only calls the ownership-protected operation."""
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from privileged import (PrivilegedRequest, PrivilegedAdapters, run_request,
                        write_request, snapshot_digest)
from zapret_inventory import ZapretSnapshot

class RefusingInventory:
    def snapshot(self):
        raise AssertionError('Service removal should not inspect unrelated processes')

class RemoveManagedServiceProtocolTests(unittest.TestCase):
    def test_only_owned_service_removal_is_dispatched(self):
        with tempfile.TemporaryDirectory() as folder:
            req=PrivilegedRequest(str(uuid.uuid4()),'remove_managed_service',time.time(),
                                  snapshot_digest(ZapretSnapshot()))
            write_request(folder,req)
            with patch('managed.remove_zapret_service',return_value=True) as remove:
                result=run_request(req.request_id,folder,PrivilegedAdapters(RefusingInventory()))
            self.assertEqual(result.status,'success')
            remove.assert_called_once_with()

    def test_foreign_service_refusal_is_returned_as_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            req=PrivilegedRequest(str(uuid.uuid4()),'remove_managed_service',time.time(),
                                  snapshot_digest(ZapretSnapshot()))
            write_request(folder,req)
            with patch('managed.remove_zapret_service',side_effect=RuntimeError('not NetPilot owned')):
                result=run_request(req.request_id,folder,PrivilegedAdapters(RefusingInventory()))
            self.assertEqual(result.status,'error')
            self.assertIn('not NetPilot owned',' '.join(result.errors))

if __name__=='__main__': unittest.main()
