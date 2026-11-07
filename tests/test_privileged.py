import json
import sys
import tempfile
import time
import unittest
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from zapret_control import OperationResult
from zapret_inventory import ZapretSnapshot


class SystemAdapter:
    def __init__(self):
        self.current = ZapretSnapshot()
        self.calls = []

    def snapshot(self):
        return self.current


class PrivilegedProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'requests'

    def request(self, operation='stop_all', **changes):
        from privileged import PrivilegedRequest, snapshot_digest
        values = dict(request_id=str(uuid.uuid4()), operation=operation,
                      created_at=time.time(), snapshot_digest=snapshot_digest(ZapretSnapshot()))
        values.update(changes)
        return PrivilegedRequest(**values)

    def test_unknown_operation_invalid_uuid_and_unexpected_fields_are_rejected(self):
        from privileged import PrivilegedRequest, write_request, run_request, PrivilegedAdapters
        with self.assertRaises(ValueError):
            write_request(self.root, self.request(operation='format_disk'))
        with self.assertRaises(ValueError):
            write_request(self.root, replace(self.request(), request_id='../bad'))
        request = self.request()
        path = write_request(self.root, request)
        raw = json.loads(path.read_text(encoding='utf-8'))
        raw['command'] = 'whoami'
        path.write_text(json.dumps(raw), encoding='utf-8')
        response = run_request(request.request_id, self.root, PrivilegedAdapters(SystemAdapter()))
        self.assertEqual(response.status, 'error')
        self.assertIn('schema', ' '.join(response.errors).lower())

    def test_stale_request_and_changed_snapshot_never_dispatch_mutation(self):
        from privileged import write_request, run_request, PrivilegedAdapters
        adapter = SystemAdapter()
        stale = self.request(created_at=time.time() - 1000)
        write_request(self.root, stale)
        with patch('privileged.execute_stop') as execute:
            response = run_request(stale.request_id, self.root, PrivilegedAdapters(adapter))
        self.assertEqual(response.status, 'error')
        execute.assert_not_called()

        changed = self.request(snapshot_digest='0' * 64)
        write_request(self.root, changed)
        with patch('privileged.execute_stop') as execute:
            response = run_request(changed.request_id, self.root, PrivilegedAdapters(adapter))
        self.assertEqual(response.status, 'error')
        execute.assert_not_called()

    def test_stop_operations_dispatch_only_reviewed_snapshot_and_mode(self):
        from privileged import write_request, run_request, PrivilegedAdapters
        from zapret_control import StopMode
        adapter = SystemAdapter()
        for operation, mode in (('stop_all', StopMode.TEMPORARY),
                                ('disable_restart', StopMode.DISABLE_RESTART)):
            request = self.request(operation)
            write_request(self.root, request)
            result = OperationResult('success', adapter.snapshot())
            with patch('privileged.execute_stop', return_value=result) as execute:
                response = run_request(request.request_id, self.root, PrivilegedAdapters(adapter))
            self.assertEqual(response.status, 'success')
            plan, passed = execute.call_args.args
            self.assertEqual(plan.mode, mode)
            self.assertIs(passed, adapter)

    def test_flowseal_apply_does_not_need_unrelated_process_inventory(self):
        from privileged import write_request, run_request, PrivilegedAdapters
        from flowseal_service import FlowsealResult
        adapter = SystemAdapter()
        folder = self.root.parent / 'zapret'
        context = PrivilegedAdapters(adapter, folder=folder, strategy='general.bat')
        request = self.request('flowseal_apply', strategy='general.bat',
                               snapshot_digest='0' * 64)
        write_request(self.root, request)
        with patch.object(adapter, 'snapshot', side_effect=AssertionError('No inventory scan')), \
             patch('flowseal_service.apply_flowseal_service',
                   return_value=FlowsealResult('general.bat','enabled')) as operation:
            response = run_request(request.request_id, self.root, context)
        self.assertEqual(response.status, 'success')
        operation.assert_called_once_with(folder, 'general.bat')

        legacy = self.request('transfer', strategy='general.bat')
        with self.assertRaises(ValueError):
            write_request(self.root, legacy)

    def test_restore_dispatch_through_fixed_public_operation(self):
        from privileged import write_request, run_request, PrivilegedAdapters
        adapter = SystemAdapter()
        folder = self.root.parent / 'zapret'
        context = PrivilegedAdapters(adapter, folder=folder, strategy='general.bat')
        restore = self.request('restore_autostart')
        write_request(self.root, restore)
        backup = object()
        with patch('privileged.load_backup', return_value=backup), \
             patch('privileged.restore_autostart',
                   return_value=OperationResult('success', adapter.snapshot())) as operation:
            response = run_request(restore.request_id, self.root, context)
        self.assertEqual(response.status, 'success')
        operation.assert_called_once_with(backup, adapter)

    def test_request_is_claimed_once_and_exactly_one_terminal_response_is_preserved(self):
        from privileged import write_request, run_request, read_response, PrivilegedAdapters
        adapter = SystemAdapter()
        request = self.request()
        write_request(self.root, request)
        with patch('privileged.execute_stop', return_value=OperationResult('success', adapter.snapshot())):
            first = run_request(request.request_id, self.root, PrivilegedAdapters(adapter))
        second = run_request(request.request_id, self.root, PrivilegedAdapters(adapter))
        stored = read_response(request.request_id, self.root)
        self.assertEqual(first.status, 'success')
        self.assertEqual(second, stored)
        self.assertEqual(stored.status, 'success')
        self.assertFalse((self.root / f'request-{request.request_id}.json').exists())

    def test_response_rejects_wrong_id_and_stale_payload(self):
        from privileged import write_request, run_request, read_response, PrivilegedAdapters
        adapter = SystemAdapter()
        request = self.request()
        write_request(self.root, request)
        with patch('privileged.execute_stop', return_value=OperationResult('success', adapter.snapshot())):
            run_request(request.request_id, self.root, PrivilegedAdapters(adapter))
        path = self.root / f'response-{request.request_id}.json'
        raw = json.loads(path.read_text(encoding='utf-8'))
        raw['request_id'] = str(uuid.uuid4())
        path.write_text(json.dumps(raw), encoding='utf-8')
        with self.assertRaises(ValueError):
            read_response(request.request_id, self.root)
        raw['request_id'] = request.request_id
        raw['completed_at'] = time.time() - 7200
        path.write_text(json.dumps(raw), encoding='utf-8')
        with self.assertRaises(ValueError):
            read_response(request.request_id, self.root)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows shell32 and pathlib semantics')
    def test_elevation_passes_only_fixed_entrypoint_and_uuid(self):
        from privileged import elevate_request
        request_id = str(uuid.uuid4())
        with patch('privileged.os.name', 'nt'), \
             patch('privileged.sys.frozen', False, create=True), \
             patch('privileged.ctypes.windll', create=True) as windll:
            windll.shell32.ShellExecuteW.return_value = 42
            self.assertTrue(elevate_request(request_id))
        args = windll.shell32.ShellExecuteW.call_args.args
        self.assertEqual(args[1], 'runas')
        self.assertIn('--privileged-request', args[3])
        self.assertIn(request_id, args[3])
        self.assertNotIn('{', args[3])


if __name__ == '__main__':
    unittest.main()
