"""Explicit, verified Zapret stop operations over an injected system adapter.

No command strings are built here. Adapter mutations must be bounded and must
address the exact service, task, or process identity supplied by the executor.
"""
from dataclasses import dataclass, field
from enum import Enum
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Protocol

from zapret_inventory import ZapretItem, ZapretSnapshot


AUTOSTART_DATA_DIR = Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'NetPilot'
_BACKUP_FILENAME = 'zapret-autostart-v1.json'
_SERVICE_MODES = frozenset({'automatic', 'automatic_delayed', 'manual', 'disabled', 'boot', 'system'})


@dataclass(frozen=True)
class AutostartBackup:
    """Original configuration only; no commands or executable payloads."""
    services: dict[str, str] = field(default_factory=dict)
    tasks: dict[str, bool] = field(default_factory=dict)
    version: int = 1
    _storage_path: Path | None = field(default=None, repr=False, compare=False)


def _backup_path(path=None) -> Path:
    path = Path(path) if path is not None else AUTOSTART_DATA_DIR / _BACKUP_FILENAME
    resolved = path.resolve()
    if resolved.parent != AUTOSTART_DATA_DIR.resolve() or resolved.name != _BACKUP_FILENAME:
        raise ValueError('autostart backup must use NetPilot data storage')
    return resolved


def _valid_task_name(name: str) -> bool:
    return (isinstance(name, str) and name.startswith('\\') and len(name) <= 1024 and
            all(_valid_name(part) for part in name[1:].split('\\')))


def _validate_backup(backup: AutostartBackup) -> None:
    if not isinstance(backup, AutostartBackup) or type(backup.version) is not int or backup.version != 1:
        raise ValueError('unsupported autostart backup version; expected version 1')
    for entries, kind in ((backup.services, 'service'), (backup.tasks, 'task')):
        if not isinstance(entries, dict):
            raise ValueError(f'invalid {kind} backup records')
        seen = set()
        for name, value in entries.items():
            valid = _valid_name(name) if kind == 'service' else _valid_task_name(name)
            if not valid or name.casefold() in seen:
                raise ValueError(f'invalid or duplicate {kind} identifier')
            seen.add(name.casefold())
            if kind == 'service' and (not isinstance(value, str) or value not in _SERVICE_MODES):
                raise ValueError(f'unsupported original service start mode: {name}')
            if kind == 'task' and type(value) is not bool:
                raise ValueError(f'invalid original task enabled state: {name}')


def load_backup(path=None) -> AutostartBackup | None:
    """Load a validated NetPilot version-one backup, without any age limit.

    Missing storage is normal. Corruption and unsupported schemas raise rather
    than silently discarding original configuration on the next operation.
    """
    path = _backup_path(path)
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate autostart backup field')
            result[key] = value
        return result
    try:
        raw = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique_object)
    except FileNotFoundError:
        return None
    if (not isinstance(raw, dict) or set(raw) != {'owner', 'version', 'services', 'tasks'} or
            raw['owner'] != 'NetPilot'):
        raise ValueError('invalid NetPilot autostart backup schema')
    backup = AutostartBackup(raw['services'], raw['tasks'], raw['version'], path)
    _validate_backup(backup)
    return backup


def _write_backup(path: Path, backup: AutostartBackup) -> AutostartBackup:
    _validate_backup(backup)
    path = _backup_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=path.name + '.', suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            json.dump({'owner': 'NetPilot', 'version': 1, 'services': backup.services,
                       'tasks': backup.tasks}, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return AutostartBackup(dict(backup.services), dict(backup.tasks), _storage_path=path)


def save_backup(path, backup: AutostartBackup) -> AutostartBackup:
    """Atomically merge; the earliest unrestored value wins, ignoring case."""
    _validate_backup(backup)
    path = _backup_path(path)
    previous = load_backup(path) or AutostartBackup()
    merged = []
    for old, new in ((previous.services, backup.services), (previous.tasks, backup.tasks)):
        values = dict(old)
        seen = {name.casefold() for name in values}
        for name, value in new.items():
            if name.casefold() not in seen:
                values[name] = value
                seen.add(name.casefold())
        merged.append(values)
    return _write_backup(path, AutostartBackup(*merged))


class StopMode(str, Enum):
    TEMPORARY = 'temporary'
    DISABLE_RESTART = 'disable_restart'


class StopAction(str, Enum):
    STOP_SERVICE = 'stop_service'
    STOP_TASK = 'stop_task'
    STOP_PROCESS = 'stop_process'


@dataclass(frozen=True)
class OperationStep:
    action: StopAction
    identifier: str | int
    source: ZapretItem

    @property
    def evidence(self) -> tuple[str, ...]:
        return self.source.evidence


@dataclass(frozen=True)
class StopPlan:
    mode: StopMode
    snapshot: ZapretSnapshot
    steps: tuple[OperationStep, ...]


@dataclass(frozen=True)
class OperationResult:
    status: str
    final_snapshot: ZapretSnapshot | None
    remaining: tuple[ZapretItem, ...] = ()
    errors: tuple[str, ...] = ()
    completed_steps: tuple[OperationStep, ...] = ()
    backup: AutostartBackup | None = None

    @property
    def success(self) -> bool:
        return self.status == 'success'


class ZapretSystemAdapter(Protocol):
    """System boundary; all operations must have bounded completion times.

    Process methods must check create_time against the OS process identity at
    mutation/wait time, not merely open an arbitrary process with the same PID.
    A snapshot check alone cannot close that race in a concrete adapter.
    """
    def snapshot(self) -> ZapretSnapshot: ...
    def stop_service(self, name: str) -> None: ...
    def stop_task(self, path: str) -> None: ...
    def terminate_process(self, pid: int, *, create_time: float) -> None: ...
    def wait_process(self, pid: int, *, create_time: float, timeout: float) -> bool: ...
    def kill_process(self, pid: int, *, create_time: float) -> None: ...
    def get_service_start_mode(self, name: str) -> str: ...
    def set_service_start_mode(self, name: str, mode: str) -> None: ...
    def get_task_enabled(self, path: str) -> bool: ...
    def set_task_enabled(self, path: str, enabled: bool) -> None: ...


def _valid_name(name: str) -> bool:
    return (isinstance(name, str) and 0 < len(name) <= 256 and
            name == name.strip() and name not in {'.', '..'} and
            not name.startswith('-') and re.fullmatch(r'[\w .(){}+\-]+', name) is not None)


def _identifier(item: ZapretItem) -> str | int:
    records = (item.process, item.service, item.task)
    if sum(record is not None for record in records) != 1:
        raise ValueError('stop target must contain exactly one source record')
    if item.kind == 'process' and item.process is not None:
        record = item.process
        if type(record.pid) is not int or not 0 < record.pid <= 0xFFFFFFFF:
            raise ValueError('invalid process PID')
        if (type(record.create_time) not in (int, float) or
                not math.isfinite(record.create_time) or record.create_time <= 0):
            raise ValueError('process creation time is required for a safe stop')
        return record.pid
    if item.kind == 'service' and item.service is not None:
        if not _valid_name(item.service.name):
            raise ValueError('invalid service name')
        return item.service.name
    if item.kind == 'task' and item.task is not None:
        name = item.task.name
        if not _valid_task_name(name):
            raise ValueError('invalid absolute task path')
        return name
    raise ValueError('stop target kind does not match its source record')


def make_stop_plan(snapshot: ZapretSnapshot, mode: StopMode) -> StopPlan:
    """Plan confirmed targets; owners precede standalone active processes.

    DISABLE_RESTART also includes inactive service/task owners so a stopped
    owner cannot restart later. Planning never changes startup settings.
    """
    mode = StopMode(mode)
    active = tuple(item for item in snapshot.items if item.confidence == 'confirmed' and
                   (item.active or (mode == StopMode.DISABLE_RESTART and item.kind in {'service', 'task'})))
    identifiers = [(item, _identifier(item)) for item in active]
    owned_pids = {item.service.pid for item in active if item.active and item.service and item.service.pid}
    steps, seen = [], set()
    for kind, action in (('service', StopAction.STOP_SERVICE), ('task', StopAction.STOP_TASK),
                         ('process', StopAction.STOP_PROCESS)):
        for item, identifier in identifiers:
            if item.kind != kind or (kind == 'process' and identifier in owned_pids):
                continue
            key = (kind, identifier.casefold() if isinstance(identifier, str) else identifier)
            if key not in seen:
                seen.add(key)
                steps.append(OperationStep(action, identifier, item))
    return StopPlan(mode, snapshot, tuple(steps))


def _snapshot(adapter: ZapretSystemAdapter) -> ZapretSnapshot:
    snapshot = adapter.snapshot()
    if not isinstance(snapshot, ZapretSnapshot):
        raise ValueError('adapter returned an invalid inventory snapshot')
    return snapshot


def _current(step: OperationStep, adapter: ZapretSystemAdapter, *, include_inactive=False) -> bool:
    """Revalidate the planned identity immediately before an operation."""
    source = step.source
    for item in _snapshot(adapter).items:
        if item.kind != source.kind:
            continue
        record = item.process or item.service or item.task
        if record is None:
            continue
        identity = record.pid if item.kind == 'process' else record.name.casefold()
        expected = step.identifier if item.kind == 'process' else step.identifier.casefold()
        if identity != expected:
            continue
        if not item.active and not include_inactive:
            continue
        if item.confidence != 'confirmed':
            raise ValueError('target is no longer confirmed')
        if source.process:
            old, new = source.process, item.process
            same = new is not None and (old.create_time, old.exe, old.cmdline) == (
                new.create_time, new.exe, new.cmdline)
        else:
            old, new = source.service or source.task, item.service or item.task
            same = old.command == new.command and old.pid == new.pid
        if not same:
            raise ValueError('stale stop target: identity or configuration changed')
        return True
    return False


def _disable_autostart(step: OperationStep, adapter: ZapretSystemAdapter, path: Path) -> AutostartBackup:
    if not _current(step, adapter, include_inactive=True):
        raise ValueError('autostart target is missing; rescan and retry')
    name = step.identifier
    if step.source.service:
        original = adapter.get_service_start_mode(name)
        pending = AutostartBackup(services={name: original})
    else:
        original = adapter.get_task_enabled(name)
        pending = AutostartBackup(tasks={name: original})
    backup = save_backup(path, pending)
    if not _current(step, adapter, include_inactive=True):
        raise ValueError('autostart target disappeared after backup; rescan and retry')
    if step.source.service:
        adapter.set_service_start_mode(name, 'disabled')
        verified = adapter.get_service_start_mode(name) == 'disabled'
    else:
        adapter.set_task_enabled(name, False)
        verified = adapter.get_task_enabled(name) is False
    if not verified:
        raise RuntimeError('could not verify disabled autostart; original state retained for restore')
    return backup


def execute_stop(plan: StopPlan, adapter: ZapretSystemAdapter, *, backup_path=None) -> OperationResult:
    """Execute reviewed targets and always derive the result from a final scan.

    Errors on one owner do not prevent independent safe steps. Service-owned
    processes stay deduplicated even if stopping that service fails; the final
    snapshot then exposes them as unresolved rather than silently broadening
    authority. An incomplete final inventory can never report success.
    """
    errors, completed = [], []
    rejected = False
    configuration_failed = False
    backup, path = None, None
    try:
        if plan != make_stop_plan(plan.snapshot, plan.mode):
            raise ValueError('stop plan differs from its validated source snapshot')
        if plan.mode == StopMode.DISABLE_RESTART:
            path = _backup_path(backup_path)
            backup = load_backup(path)
    except Exception as exc:
        rejected = True
        errors.append(f'plan rejected: {type(exc).__name__}: {exc}')
    if not rejected:
        for step in plan.steps:
            try:
                if plan.mode == StopMode.DISABLE_RESTART and step.source.kind in {'service', 'task'}:
                    try:
                        backup = _disable_autostart(step, adapter, path)
                    except Exception:
                        configuration_failed = True
                        # A write may have succeeded before a system/readback failure.
                        backup = load_backup(path)
                        raise
                if not _current(step, adapter):
                    continue
                if step.action == StopAction.STOP_SERVICE:
                    adapter.stop_service(step.identifier)
                elif step.action == StopAction.STOP_TASK:
                    adapter.stop_task(step.identifier)
                else:
                    pid, birth = step.identifier, step.source.process.create_time
                    adapter.terminate_process(pid, create_time=birth)
                    exited = adapter.wait_process(pid, create_time=birth, timeout=3.0)
                    if not exited and _current(step, adapter):
                        adapter.kill_process(pid, create_time=birth)
                        adapter.wait_process(pid, create_time=birth, timeout=3.0)
                completed.append(step)
            except Exception as exc:
                errors.append(f'{step.action.value} {step.identifier}: {type(exc).__name__}: {exc}')
    try:
        final = _snapshot(adapter)
    except Exception as exc:
        errors.append(f'final inventory failed: {type(exc).__name__}: {exc}')
        return OperationResult('error', None, errors=tuple(errors), completed_steps=tuple(completed), backup=backup)
    remaining = final.active
    errors.extend(final.warnings)
    status = ('partial' if remaining or configuration_failed else
              'error' if rejected or final.warnings else 'success')
    return OperationResult(status, final, remaining, tuple(errors), tuple(completed), backup)


def restore_autostart(backup: AutostartBackup, adapter: ZapretSystemAdapter) -> OperationResult:
    """Restore only the current validated backup, retaining every unverified entry.

    A caller-created object is not restoration authority: it must agree with the
    current backup in NetPilot storage. Missing or ambiguous owners are retained
    for a later explicit retry. Runtime processes are never started or stopped.
    """
    errors, rejected = [], False
    pending = None
    try:
        _validate_backup(backup)
        if backup._storage_path is None:
            raise ValueError('restore requires a backup saved or loaded by NetPilot')
        path = _backup_path(backup._storage_path)
        pending = load_backup(path)
        if pending is None or pending != backup:
            raise ValueError('backup changed or is missing; reload it before restoring')
    except Exception as exc:
        rejected = True
        errors.append(f'restore rejected: {type(exc).__name__}: {exc}')
    if not rejected:
        entries = [('service', name, value) for name, value in pending.services.items()]
        entries += [('task', name, value) for name, value in pending.tasks.items()]
        for kind, name, value in entries:
            try:
                # Validate again at the mutation boundary, including current ownership.
                matches = [item for item in _snapshot(adapter).items if item.kind == kind and
                           item.confidence == 'confirmed' and _identifier(item).casefold() == name.casefold()]
                if not matches:
                    raise ValueError('target is missing or not confirmed; rescan and retry')
                if kind == 'service':
                    adapter.set_service_start_mode(name, value)
                    verified = adapter.get_service_start_mode(name) == value
                else:
                    adapter.set_task_enabled(name, value)
                    verified = adapter.get_task_enabled(name) is value
                if not verified:
                    raise RuntimeError('could not verify restored configuration; retry restore')
                services, tasks = dict(pending.services), dict(pending.tasks)
                (services if kind == 'service' else tasks).pop(name)
                pending = _write_backup(path, AutostartBackup(services, tasks))
            except Exception as exc:
                errors.append(f'restore {kind} {name}: {type(exc).__name__}: {exc}')
    try:
        final = _snapshot(adapter)
    except Exception as exc:
        errors.append(f'final inventory failed: {type(exc).__name__}: {exc}')
        return OperationResult('error', None, errors=tuple(errors), backup=pending)
    errors.extend(final.warnings)
    status = ('error' if rejected or final.warnings else
              'partial' if errors or pending.services or pending.tasks else 'success')
    return OperationResult(status, final, final.active, tuple(errors), backup=pending)
