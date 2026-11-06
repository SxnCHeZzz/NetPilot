"""Explicit, verified Zapret stop operations over an injected system adapter.

No command strings are built here. Adapter mutations must be bounded and must
address the exact service, task, or process identity supplied by the executor.
"""
from dataclasses import asdict, dataclass, field
from enum import Enum
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Protocol
import uuid

from zapret_inventory import ZapretItem, ZapretSnapshot, ServiceRecord


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
    DELETE_SERVICE = 'delete_service'
    CREATE_SERVICE = 'create_service'
    START_SERVICE = 'start_service'
    RESTORE_SERVICE = 'restore_service'
    DISABLE_SERVICE = 'disable_service'
    DISABLE_TASK = 'disable_task'
    RESTORE_TASK = 'restore_task'


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
    def get_service_backup(self, name: str) -> 'ServiceBackup | None': ...
    def validate_service_backup(self, backup: 'ServiceBackup') -> None: ...
    def delete_service(self, name: str) -> None: ...
    def create_netpilot_service(self, transfer: 'ValidatedTransfer') -> None: ...
    def restore_service(self, backup: 'ServiceBackup') -> None: ...
    def start_service(self, name: str) -> None: ...
    def start_task(self, path: str) -> None: ...


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


@dataclass(frozen=True)
class TransferRequest:
    folder: str | Path
    strategy: str
    snapshot: ZapretSnapshot
    confirmed: bool = False


@dataclass(frozen=True)
class ServiceBackup:
    """Exact SCM configuration. Adapters must include everything they need to restore.

    ``adapter_fields`` may contain account, dependencies, service type, delayed
    start, failure actions, security descriptor and ownership registry values.
    ``validate_service_backup`` must refuse accounts/configuration that the
    adapter cannot faithfully recreate (for example an unavailable password).
    Restore must restore configuration AND the original running/stopped state;
    it must update an existing custom service in place, never delete it.
    All fields must be JSON serializable; secrets must not be stored here.
    """
    name: str
    image_path: str
    start_mode: str
    state: str
    display_name: str
    description: str
    adapter_fields: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ValidatedTransfer:
    request: TransferRequest
    executable: str
    arguments: str
    image_path: str
    stop_plan: StopPlan
    services: tuple[ServiceBackup, ...]
    tasks: tuple[tuple[str, bool, bool], ...]
    matching_service: bool
    transaction_id: str


def _service_matches(item: ZapretItem, image_path: str) -> bool:
    # Exact arguments are intentional: matching executable alone is insufficient.
    from zapret_inventory import _path, _tokens
    if not item.service or item.origin != 'netpilot' or item.confidence != 'confirmed':
        return False
    actual, wanted = _tokens(item.service.command), _tokens(image_path)
    return bool(actual and wanted and _path(actual[0]) == _path(wanted[0]) and actual[1:] == wanted[1:])


def _process_matches_image(item: ZapretItem, image_path: str) -> bool:
    """Verify an observed service process against the configured executable/args."""
    from zapret_inventory import _path, _tokens
    if not item.process:
        return False
    wanted = _tokens(image_path)
    if not wanted or _path(item.process.exe) != _path(wanted[0]) or not item.process.cmdline:
        return False
    actual = tuple(item.process.cmdline)
    if actual and _path(actual[0]) in {_path(item.process.exe), _path(wanted[0])}:
        actual = actual[1:]
    return actual == wanted[1:]


def _service_runtime_matches(snapshot: ZapretSnapshot, service: ZapretItem, image_path: str) -> bool:
    if not service.active or not service.pid:
        return True
    observed = tuple(item for item in snapshot.items if item.process and item.pid == service.pid)
    return not observed or all(_process_matches_image(item, image_path) for item in observed)


def _check_service_backup(backup, adapter):
    if (not isinstance(backup, ServiceBackup) or not _valid_name(backup.name) or
            not isinstance(backup.image_path, str) or not backup.image_path.strip() or
            backup.start_mode not in _SERVICE_MODES or backup.state not in {'running', 'stopped'} or
            not isinstance(backup.display_name, str) or not isinstance(backup.description, str) or
            not isinstance(backup.adapter_fields, dict)):
        raise ValueError('incomplete recoverable service configuration')
    json.dumps(asdict(backup), allow_nan=False)
    adapter.validate_service_backup(backup)


def validate_transfer(request: TransferRequest, adapter: ZapretSystemAdapter) -> ValidatedTransfer:
    """Read-only preflight. The reviewed complete inventory is transfer authority."""
    from managed import validated_zapret_service_command
    if not isinstance(request, TransferRequest) or request.confirmed is not True:
        raise ValueError('ownership transfer requires explicit confirmation')
    executable, arguments = validated_zapret_service_command(request.folder, request.strategy)
    image_path = f'"{executable}" {arguments}'
    current = _snapshot(adapter)
    if current.warnings or current != request.snapshot:
        raise ValueError('inventory is incomplete or changed; rescan and confirm again')
    named = [i for i in current.items if i.service and i.service.name.casefold() == 'zapret']
    existing = adapter.get_service_backup('zapret')
    if len(named) > 1 or (existing is not None and not named):
        raise ValueError('name-conflicting zapret service is not in the confirmed inventory')
    if named and (named[0].confidence != 'confirmed' or existing is None):
        raise ValueError('name-conflicting zapret service is not confirmed')
    matching = bool(named and _service_matches(named[0], image_path))
    runtime_matches = bool(matching and _service_runtime_matches(current, named[0], image_path))
    keep_pid = named[0].pid if runtime_matches else None
    targets = tuple(i for i in current.items if not (runtime_matches and
        (i == named[0] or (i.process and keep_pid and i.pid == keep_pid))))
    plan = make_stop_plan(ZapretSnapshot(targets), StopMode.DISABLE_RESTART)
    services, tasks = [], []
    # Capture runtime/configuration for every owner before the first mutation.
    for item in current.items:
        if item.confidence != 'confirmed':
            continue
        if item.service:
            backup = adapter.get_service_backup(_identifier(item))
            _check_service_backup(backup, adapter)
            if (backup.name.casefold() != item.service.name.casefold() or
                    backup.image_path != item.service.command or
                    (backup.state == 'running') != item.active):
                raise ValueError('service configuration changed during preflight')
            if backup.name.casefold() not in {b.name.casefold() for b in services}:
                services.append(backup)
        elif item.task:
            name = _identifier(item)
            enabled = adapter.get_task_enabled(name)
            if type(enabled) is not bool:
                raise ValueError('unknown task enabled state')
            tasks.append((name, enabled, item.active))
    load_backup()  # Corrupt previous restoration data must never be overwritten.
    return ValidatedTransfer(request, executable, arguments, image_path, plan,
                             tuple(services), tuple(tasks), matching, uuid.uuid4().hex)


def _save_transfer_backup(transfer: ValidatedTransfer) -> None:
    """Durable recovery journal, written before even stopping an external owner."""
    directory = AUTOSTART_DATA_DIR.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / 'zapret-transfer-v1.json'
    history = []
    if destination.exists():
        previous = json.loads(destination.read_text(encoding='utf-8'))
        if (not isinstance(previous, dict) or previous.get('owner') != 'NetPilot' or
                previous.get('version') != 1 or not isinstance(previous.get('services'), dict) or
                not isinstance(previous.get('tasks'), dict) or not isinstance(previous.get('history', []), list)):
            raise ValueError('invalid prior service recovery journal; preserve it for manual recovery')
        history = previous.pop('history', []) + [previous]
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory,
                                         prefix=destination.name + '.', suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            json.dump({'owner': 'NetPilot', 'version': 1,
                       'services': {b.name: asdict(b) for b in transfer.services},
                       'tasks': {name: {'enabled': enabled, 'active': active}
                                 for name, enabled, active in transfer.tasks}, 'history': history},
                      handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _transfer_remaining(snapshot, transfer):
    target = next((i for i in snapshot.items if i.service and
                   i.service.name.casefold() == 'zapret' and _service_matches(i, transfer.image_path)), None)
    conflicts = tuple(i for i in snapshot.active if i != target and not (target and target.pid and
                      i.process and i.pid == target.pid and _process_matches_image(i, transfer.image_path)))
    return target, conflicts


def _created_by_transfer(backup: ServiceBackup | None, transfer: ValidatedTransfer) -> bool:
    return bool(backup and backup.image_path == transfer.image_path and
                backup.adapter_fields.get('transaction_id') == transfer.transaction_id)


def _snapshot_with_owned_service(adapter: ZapretSystemAdapter, transfer: ValidatedTransfer) -> ZapretSnapshot:
    """Recover a just-created *own* service hidden by a lagging CIM query.

    Only used AFTER our creation has been verified against the exact command
    and a unique per-transaction registry marker. Never use this fallback to
    authorize stopping or replacing an external service. Other discoveries
    and warnings are retained; conflicts are not filtered out.
    """
    snapshot = _snapshot(adapter)
    if any(item.service and item.service.name.casefold() == 'zapret' for item in snapshot.items):
        return snapshot
    owned = adapter.get_service_backup('zapret')
    if not _created_by_transfer(owned, transfer):
        return snapshot
    if owned.state not in {'running', 'stopped'} or owned.start_mode not in {'automatic', 'automatic_delayed'}:
        return snapshot
    # A matching SCM PID is needed if a service-owned winws process also
    # appears independently in the inventory, otherwise it stays a conflict.
    read_pid = getattr(adapter, 'get_service_pid', None)
    pid = read_pid('zapret') if callable(read_pid) else None
    record = ServiceRecord(owned.name, owned.image_path, owned.state, pid)
    verified = ZapretItem('service', 'netpilot', 'confirmed', owned.state == 'running',
                          ('exact-transaction-marker', 'matching-service-command'), service=record)
    return ZapretSnapshot(snapshot.items + (verified,), snapshot.warnings)


def _reconcile_rollback_backup(path: Path, before: AutostartBackup,
                               services: set[str], tasks: set[str]) -> AutostartBackup:
    """Drop only entries introduced and already restored by this transaction."""
    current = load_backup(path) or AutostartBackup()
    original_services = {name.casefold() for name in before.services}
    original_tasks = {name.casefold() for name in before.tasks}
    remaining_services = {name: value for name, value in current.services.items()
                          if name.casefold() not in {entry.casefold() for entry in services} or
                          name.casefold() in original_services}
    remaining_tasks = {name: value for name, value in current.tasks.items()
                       if name.casefold() not in {entry.casefold() for entry in tasks} or
                       name.casefold() in original_tasks}
    return _write_backup(path, AutostartBackup(remaining_services, remaining_tasks))


def transfer_to_netpilot(request: TransferRequest, adapter: ZapretSystemAdapter) -> OperationResult:
    """Transfer confirmed owners, compensating even for mutations that raise afterward.

    Only the explicitly confirmed, name-conflicting ``zapret`` can be deleted.
    Other owners use the Task 3 durable autostart backup. Rollback never deletes
    a resource unless the installed service carries the current transaction's
    unique marker and exact command, verified immediately before deletion.
    """
    errors, completed = [], []
    transfer, creation_attempted, mutated = None, False, False
    backup = None
    backup_before = AutostartBackup()
    backup_reconciled = True
    try:
        transfer = validate_transfer(request, adapter)
        _save_transfer_backup(transfer)
        backup_before = load_backup(_backup_path()) or AutostartBackup()
        # Revalidate after durable I/O and immediately before entering mutation.
        if _snapshot(adapter) != request.snapshot:
            raise ValueError('inventory changed after backup; rescan and confirm again')
        for step in transfer.stop_plan.steps:
            if step.source.kind in {'service', 'task'}:
                if not (step.source.service and step.identifier.casefold() == 'zapret'):
                    mutated = True
                    backup = _disable_autostart(step, adapter, _backup_path())
                    action = StopAction.DISABLE_SERVICE if step.source.service else StopAction.DISABLE_TASK
                    completed.append(OperationStep(action, step.identifier, step.source))
            if not _current(step, adapter):
                continue
            mutated = True
            if step.source.service:
                adapter.stop_service(step.identifier)
            elif step.source.task:
                adapter.stop_task(step.identifier)
            else:
                birth = step.source.process.create_time
                adapter.terminate_process(step.identifier, create_time=birth)
                if not adapter.wait_process(step.identifier, create_time=birth, timeout=3.0) and _current(step, adapter):
                    adapter.kill_process(step.identifier, create_time=birth)
                    adapter.wait_process(step.identifier, create_time=birth, timeout=3.0)
            completed.append(step)
        # No creation/deletion until all reviewed conflicts have actually stopped.
        before = _snapshot(adapter)
        _, remaining = _transfer_remaining(before, transfer)
        if before.warnings or remaining:
            raise RuntimeError('confirmed conflicts remain active or inventory is incomplete')
        original = next((b for b in transfer.services if b.name.casefold() == 'zapret'), None)
        if not transfer.matching_service:
            if original is not None:
                present = adapter.get_service_backup(original.name)
                if present is None or asdict(present) != dict(asdict(original), state='stopped'):
                    raise ValueError('name-conflicting service changed before deletion')
                source = next(i for i in request.snapshot.items if i.service and i.service.name.casefold() == 'zapret')
                mutated = True
                adapter.delete_service(original.name)
                completed.append(OperationStep(StopAction.DELETE_SERVICE, original.name, source))
            elif adapter.get_service_backup('zapret') is not None:
                raise ValueError('a new name-conflicting service appeared')
            creation_attempted = mutated = True
            adapter.create_netpilot_service(transfer)
            if not _created_by_transfer(adapter.get_service_backup('zapret'), transfer):
                raise RuntimeError('created service ownership could not be verified')
            new = next(i for i in _snapshot_with_owned_service(adapter, transfer).items if i.service and i.service.name.casefold() == 'zapret')
            completed.append(OperationStep(StopAction.CREATE_SERVICE, 'zapret', new))
        current = (_snapshot_with_owned_service(adapter, transfer) if creation_attempted
                   else _snapshot(adapter))
        target, remaining = _transfer_remaining(current, transfer)
        if target is None or remaining or current.warnings:
            raise RuntimeError('could not verify selected NetPilot service configuration')
        if not target.active:
            mutated = True
            adapter.start_service(target.service.name)
            completed.append(OperationStep(StopAction.START_SERVICE, target.service.name, target))
        final = (_snapshot_with_owned_service(adapter, transfer) if creation_attempted
                 else _snapshot(adapter))
        target, remaining = _transfer_remaining(final, transfer)
        if final.warnings or target is None or not target.active or remaining:
            raise RuntimeError('final service path, arguments, running state or conflicts failed verification')
        return OperationResult('success', final, remaining, completed_steps=tuple(completed), backup=backup)
    except Exception as exc:
        errors.append(f'transfer: {type(exc).__name__}: {exc}')

    if transfer is not None and mutated:
        # Each recovery is independent; failure to clean up must not suppress the
        # recovery of other owners. Re-read rather than trusting the call log.
        if creation_attempted:
            try:
                owned = adapter.get_service_backup('zapret')
                if _created_by_transfer(owned, transfer):
                    # The broad CIM inventory can lag behind SCM / fail to
                    # enumerate a just-created service. The exact registry
                    # backup above *already proves* this transaction's unique
                    # ownership marker AND command. Do not require that the
                    # failing inventory can enumerate the new service again
                    # merely to roll back our own resource.
                    if owned.state == 'running':
                        adapter.stop_service(owned.name)
                        stopped = adapter.get_service_backup(owned.name)
                        if not _created_by_transfer(stopped, transfer):
                            raise RuntimeError('transaction ownership changed while stopping service')
                        if stopped.state != 'stopped':
                            raise RuntimeError('transaction service failed to stop')
                    # Recheck the transaction marker at the mutation boundary;
                    # never delete a service belonging to another installer.
                    current_owned = adapter.get_service_backup(owned.name)
                    if not _created_by_transfer(current_owned, transfer):
                        raise RuntimeError('transaction service identity changed; cleanup refused')
                    adapter.delete_service(owned.name)
                elif owned is not None:
                    raise RuntimeError('service belongs to another transaction; cleanup refused')
            except Exception as exc:
                errors.append(f'cleanup NetPilot service: {type(exc).__name__}: {exc}')
        restored_services, restored_tasks = set(), set()
        for original in transfer.services:
            try:
                current = adapter.get_service_backup(original.name)
                if current != original:
                    # Never overwrite an unrelated replacement that appeared in flight.
                    if current is not None and (current.image_path != original.image_path or
                            current.adapter_fields != original.adapter_fields):
                        raise RuntimeError('service identity changed; refusing to overwrite it')
                    adapter.restore_service(original)
                if adapter.get_service_backup(original.name) != original:
                    raise RuntimeError('restored service differs from the exact saved configuration/state')
                if current != original:
                    source = next(i for i in request.snapshot.items if i.service and
                                  i.service.name.casefold() == original.name.casefold())
                    completed.append(OperationStep(StopAction.RESTORE_SERVICE, original.name, source))
                if original.name.casefold() != 'zapret':
                    restored_services.add(original.name)
            except Exception as exc:
                errors.append(f'restore service {original.name}: {type(exc).__name__}: {exc}')
        for name, enabled, active in transfer.tasks:
            try:
                matches = [i for i in _snapshot(adapter).items if i.task and i.task.name == name and
                           i.confidence == 'confirmed']
                previous = next(i for i in request.snapshot.items if i.task and i.task.name == name)
                if not matches or matches[0].task.command != previous.task.command:
                    raise RuntimeError('task identity changed; refusing to restore it')
                if adapter.get_task_enabled(name) != enabled:
                    adapter.set_task_enabled(name, enabled)
                if active and not matches[0].active:
                    adapter.start_task(name)
                restored = next((i for i in _snapshot(adapter).items if i.task and i.task.name == name), None)
                if (adapter.get_task_enabled(name) != enabled or restored is None or
                        restored.active != active or restored.task.command != previous.task.command):
                    raise RuntimeError('task configuration/state was not restored')
                completed.append(OperationStep(StopAction.RESTORE_TASK, name, previous))
                restored_tasks.add(name)
            except Exception as exc:
                errors.append(f'restore task {name}: {type(exc).__name__}: {exc}')
        if backup is not None and (restored_services or restored_tasks):
            try:
                backup = _reconcile_rollback_backup(_backup_path(), backup_before,
                                                     restored_services, restored_tasks)
            except Exception as exc:
                backup_reconciled = False
                errors.append(f'reconcile autostart backup: {type(exc).__name__}: {exc}')
    try:
        final = _snapshot(adapter)
        restored = transfer is not None and not final.warnings and backup_reconciled
        if transfer is not None:
            restored = restored and all(adapter.get_service_backup(b.name) == b for b in transfer.services)
            restored = restored and all(adapter.get_task_enabled(name) == enabled for name, enabled, _ in transfer.tasks)
            # Ignore replacement PIDs for restarted owners, but standalone process
            # identities cannot be reconstructed. Missing processes require recovery.
            def state(snapshot):
                owned_pids = {i.pid for i in snapshot.items if i.service and i.active and
                              i.confidence == 'confirmed' and i.pid}
                return sorted((i.kind, str(_identifier(i)), i.origin, i.confidence, i.active,
                               i.service.command if i.service else i.task.command if i.task else
                               repr(i.process)) for i in snapshot.items if i.confidence == 'confirmed' and
                              not (i.process and i.pid in owned_pids))
            restored = restored and state(final) == state(request.snapshot)
        status = 'rolled_back' if restored else 'manual_recovery_required' if mutated else 'error'
        if final.warnings:
            errors.extend(final.warnings)
        return OperationResult(status, final, final.active, tuple(errors), tuple(completed), backup)
    except Exception as exc:
        errors.append(f'rollback verification: {type(exc).__name__}: {exc}')
        return OperationResult('manual_recovery_required' if mutated else 'error', None,
                               errors=tuple(errors), completed_steps=tuple(completed), backup=backup)
