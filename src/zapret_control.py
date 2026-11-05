"""Explicit, verified Zapret stop operations over an injected system adapter.

No command strings are built here. Adapter mutations must be bounded and must
address the exact service, task, or process identity supplied by the executor.
"""
from dataclasses import dataclass
from enum import Enum
import math
import re
from typing import Protocol

from zapret_inventory import ZapretItem, ZapretSnapshot


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
        if (not isinstance(name, str) or not name.startswith('\\') or
                len(name) > 1024 or not all(_valid_name(part) for part in name[1:].split('\\'))):
            raise ValueError('invalid absolute task path')
        return name
    raise ValueError('stop target kind does not match its source record')


def make_stop_plan(snapshot: ZapretSnapshot, mode: StopMode) -> StopPlan:
    """Plan only confirmed active targets; owners precede standalone processes.

    DISABLE_RESTART records intent for the reversible configuration layer. This
    planner itself never changes or deletes startup settings.
    """
    mode = StopMode(mode)
    active = snapshot.active
    identifiers = [(item, _identifier(item)) for item in active]
    owned_pids = {item.service.pid for item in active if item.service and item.service.pid}
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


def _current(step: OperationStep, adapter: ZapretSystemAdapter) -> bool:
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
        if not item.active:
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


def execute_stop(plan: StopPlan, adapter: ZapretSystemAdapter) -> OperationResult:
    """Execute reviewed targets and always derive the result from a final scan.

    Errors on one owner do not prevent independent safe steps. Service-owned
    processes stay deduplicated even if stopping that service fails; the final
    snapshot then exposes them as unresolved rather than silently broadening
    authority. An incomplete final inventory can never report success.
    """
    errors, completed = [], []
    rejected = False
    try:
        if plan != make_stop_plan(plan.snapshot, plan.mode):
            raise ValueError('stop plan differs from its validated source snapshot')
        if plan.mode != StopMode.TEMPORARY:
            raise ValueError('disable-restart requires the reversible configuration layer')
    except Exception as exc:
        rejected = True
        errors.append(f'plan rejected: {type(exc).__name__}: {exc}')
    if not rejected:
        for step in plan.steps:
            try:
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
        return OperationResult('error', None, errors=tuple(errors), completed_steps=tuple(completed))
    remaining = final.active
    errors.extend(final.warnings)
    status = 'partial' if remaining else 'error' if rejected or final.warnings else 'success'
    return OperationResult(status, final, remaining, tuple(errors), tuple(completed))
