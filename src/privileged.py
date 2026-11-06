"""Narrow, one-shot UAC protocol for verified Zapret operations.

The elevated process receives only a UUID.  It reloads a strict request from
NetPilot-owned storage, takes a fresh inventory and dispatches one fixed public
operation.  No request field is ever interpreted as a shell command.
"""
from __future__ import annotations

import ctypes
from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
import uuid

from zapret_control import (OperationResult, StopMode,
                            execute_stop, load_backup, make_stop_plan,
                            restore_autostart)
from zapret_inventory import ZapretSnapshot


ALLOWED_OPERATIONS = frozenset({'stop_all', 'disable_restart', 'restore_autostart', 'remove_managed_service', 'flowseal_apply', 'flowseal_stop'})
REQUEST_MAX_AGE = 300.0
RESPONSE_MAX_AGE = 3600.0


@dataclass(frozen=True)
class PrivilegedRequest:
    request_id: str
    operation: str
    created_at: float
    snapshot_digest: str
    strategy: str = ''
    version: int = 1


@dataclass(frozen=True)
class PrivilegedResponse:
    request_id: str
    status: str
    completed_at: float
    errors: tuple[str, ...] = ()
    completed_steps: tuple[str, ...] = ()
    version: int = 1


@dataclass(frozen=True)
class PrivilegedAdapters:
    system: object
    folder: Path | None = None
    strategy: str = ''


def _uuid(value: str) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError('invalid privileged request id') from exc
    if str(parsed) != str(value).lower():
        raise ValueError('privileged request id must be a canonical UUID')
    return str(parsed)


def _safe_root(root) -> Path:
    root = Path(root).resolve()
    if root.exists():
        attributes = getattr(os.lstat(root), 'st_file_attributes', 0)
        if root.is_symlink() or attributes & getattr(os, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
            raise ValueError('privileged request storage cannot be a link or reparse point')
    root.mkdir(parents=True, exist_ok=True)
    return root


def _request_path(root: Path, request_id: str, state='request') -> Path:
    return root / f'{state}-{_uuid(request_id)}.json'


def _validate_request(request: PrivilegedRequest) -> None:
    if not isinstance(request, PrivilegedRequest) or type(request.version) is not int or request.version != 1:
        raise ValueError('unsupported privileged request version')
    _uuid(request.request_id)
    if request.operation not in ALLOWED_OPERATIONS:
        raise ValueError('unknown privileged operation')
    if type(request.created_at) not in (int, float) or not math.isfinite(request.created_at):
        raise ValueError('invalid privileged request timestamp')
    if not isinstance(request.snapshot_digest, str) or len(request.snapshot_digest) != 64 or any(
            char not in '0123456789abcdef' for char in request.snapshot_digest):
        raise ValueError('invalid inventory digest')
    if (not isinstance(request.strategy, str) or Path(request.strategy).name != request.strategy or
            any(char in request.strategy for char in r'\/:')):
        raise ValueError('invalid strategy identity')
    if request.operation == 'flowseal_apply' and not request.strategy:
        raise ValueError('transfer requires a strategy identity')
    if request.operation != 'flowseal_apply' and request.strategy:
        raise ValueError('strategy is only allowed for transfer')


def snapshot_digest(snapshot: ZapretSnapshot) -> str:
    if not isinstance(snapshot, ZapretSnapshot):
        raise ValueError('invalid inventory snapshot')
    # User/non-elevated discovery can produce different warning *strings* than
    # the elevated process even when every identified component is unchanged.
    # Hash discovered identities and actionable state, not diagnostic prose.
    # This remains fail-closed for newly visible, changed, or missing entries.
    # Execution separately rejects an incomplete/ambiguous elevated inventory.
    identities = []
    for item in snapshot.items:
        record = asdict(item)
        if record.get('process') is not None:
            record['process'].pop('warnings', None)
        identities.append(record)
    fingerprint = {'items': identities}
    payload = json.dumps(fingerprint, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def _atomic_json(path: Path, payload: dict, *, preserve=False) -> None:
    if preserve and path.exists():
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent,
                                         prefix=path.name + '.', suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        if preserve and path.exists():
            return
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_request(root, request: PrivilegedRequest) -> Path:
    _validate_request(request)
    root = _safe_root(root)
    path = _request_path(root, request.request_id)
    if path.exists() or _request_path(root, request.request_id, 'response').exists():
        raise FileExistsError('privileged request id was already used')
    _atomic_json(path, asdict(request))
    return path


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON field')
        result[key] = value
    return result


def _load_request(path: Path) -> PrivilegedRequest:
    raw = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=_unique_object)
    expected = {'request_id', 'operation', 'created_at', 'snapshot_digest', 'strategy', 'version'}
    if not isinstance(raw, dict) or set(raw) != expected:
        raise ValueError('invalid privileged request schema')
    request = PrivilegedRequest(**raw)
    _validate_request(request)
    return request


def _response_from_result(request_id: str, result: OperationResult) -> PrivilegedResponse:
    steps = tuple(f'{step.action.value}:{step.identifier}' for step in result.completed_steps)
    return PrivilegedResponse(request_id, result.status, time.time(), tuple(result.errors), steps)


def _write_response(root: Path, response: PrivilegedResponse) -> PrivilegedResponse:
    path = _request_path(root, response.request_id, 'response')
    if path.exists():
        return read_response(response.request_id, root)
    _atomic_json(path, {**asdict(response), 'errors': list(response.errors),
                        'completed_steps': list(response.completed_steps)}, preserve=True)
    return read_response(response.request_id, root)


def read_response(request_id: str, root, *, max_age=RESPONSE_MAX_AGE) -> PrivilegedResponse:
    root = _safe_root(root)
    path = _request_path(root, request_id, 'response')
    raw = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=_unique_object)
    expected = {'request_id', 'status', 'completed_at', 'errors', 'completed_steps', 'version'}
    if not isinstance(raw, dict) or set(raw) != expected or raw['request_id'] != _uuid(request_id):
        raise ValueError('invalid privileged response schema')
    if (type(raw['version']) is not int or raw['version'] != 1 or
            type(raw['completed_at']) not in (int, float) or
            not math.isfinite(raw['completed_at']) or
            not isinstance(raw['errors'], list) or not all(isinstance(x, str) for x in raw['errors']) or
            not isinstance(raw['completed_steps'], list) or
            not all(isinstance(x, str) for x in raw['completed_steps'])):
        raise ValueError('invalid privileged response values')
    age = time.time() - raw['completed_at']
    if age < -30 or age > max_age:
        raise ValueError('stale privileged response')
    return PrivilegedResponse(raw['request_id'], str(raw['status']), raw['completed_at'],
                              tuple(raw['errors']), tuple(raw['completed_steps']), raw['version'])


def run_request(request_id: str, root, adapters: PrivilegedAdapters) -> PrivilegedResponse:
    request_id = _uuid(request_id)
    root = _safe_root(root)
    request_path = _request_path(root, request_id)
    response_path = _request_path(root, request_id, 'response')
    if not request_path.exists() and response_path.exists():
        return read_response(request_id, root)
    running = _request_path(root, request_id, 'running')
    try:
        os.replace(request_path, running)
    except FileNotFoundError:
        if response_path.exists():
            return read_response(request_id, root)
        raise
    try:
        try:
            request = _load_request(running)
            age = time.time() - request.created_at
            if age < -30 or age > REQUEST_MAX_AGE:
                raise ValueError('privileged request is stale')
            if request.operation == 'remove_managed_service':
                # Service removal is independently scoped to NetPilot's own
                # registry ownership marker. It never removes external SCM
                # entries and does not depend on readable foreign processes.
                from managed import remove_zapret_service
                remove_zapret_service()
                response = PrivilegedResponse(request_id, 'success', time.time())
                return _write_response(root, response)
            if request.operation in {'flowseal_apply', 'flowseal_stop'}:
                # Do NOT require a full process inventory: Windows often denies
                # access to unrelated process cmdlines.  The elevated helper
                # instead verifies the exact `zapret` SCM entry, its executable
                # and upstream marker immediately before touching the service.
                if adapters.folder is None:
                    raise ValueError('Flowseal installation directory is missing')
                from flowseal_service import apply_flowseal_service, stop_flowseal_service
                if request.operation == 'flowseal_apply':
                    outcome = apply_flowseal_service(adapters.folder, request.strategy)
                else:
                    outcome = stop_flowseal_service(adapters.folder)
                return _write_response(root, PrivilegedResponse(
                    request_id, 'success', time.time(),
                    completed_steps=(f'flowseal:{outcome.action}:{outcome.strategy}',)))
            snapshot = adapters.system.snapshot()
            if snapshot_digest(snapshot) != request.snapshot_digest:
                raise ValueError('inventory changed; rescan and confirm again')
            if request.operation in {'stop_all', 'disable_restart'}:
                mode = StopMode.TEMPORARY if request.operation == 'stop_all' else StopMode.DISABLE_RESTART
                result = execute_stop(make_stop_plan(snapshot, mode), adapters.system)
            else:
                backup = load_backup()
                if backup is None:
                    raise ValueError('there is no external autostart backup to restore')
                result = restore_autostart(backup, adapters.system)
            response = _response_from_result(request_id, result)
        except Exception as exc:
            response = PrivilegedResponse(request_id, 'error', time.time(),
                                          (f'{type(exc).__name__}: {exc}',))
        return _write_response(root, response)
    finally:
        running.unlink(missing_ok=True)


def elevate_request(request_id: str) -> bool:
    request_id = _uuid(request_id)
    if os.name != 'nt':
        return False
    flag = f'--privileged-request {request_id}'
    if getattr(sys, 'frozen', False):
        executable, arguments = sys.executable, flag
    else:
        executable = sys.executable
        arguments = f'"{Path(__file__).with_name("app.py").resolve()}" {flag}'
    result = ctypes.windll.shell32.ShellExecuteW(None, 'runas', executable, arguments, None, 0)
    return result > 32


def privileged_cli_main(request_id: str) -> int:
    """Production entry point; the concrete Windows adapter is imported lazily."""
    from managed import ROOT, read_cfg, sub_dir
    from windows_zapret import WindowsZapretAdapter
    cfg = read_cfg()
    folder = sub_dir(cfg, 'zapret')
    strategy = cfg.get('strategy', 'general.bat')
    adapter = WindowsZapretAdapter((folder,))
    response = run_request(request_id, ROOT / 'privileged',
                           PrivilegedAdapters(adapter, folder, strategy))
    return 0 if response.status in {'success', 'rolled_back'} else 1
