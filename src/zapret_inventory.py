"""Read-only, conservative inventory of local Zapret installations.

Discovery is deliberately separate from classification. A candidate name never
authorizes an action: consumers must use confirmed items in ``snapshot.active``.
Windows paths are compared with ntpath, including when tests run on another OS.
"""
from dataclasses import dataclass
import json
import ntpath
from pathlib import Path
import re
import subprocess
from typing import Iterable, Protocol


def _path(value: str) -> str:
    return ntpath.normcase(ntpath.normpath(value.strip('"'))) if value else ''


def _tokens(command: str) -> tuple[str, ...]:
    return tuple(part.strip('"') for part in re.findall(r'[^\s"]*"[^"]*"[^\s"]*|[^\s"]+', command))


@dataclass(frozen=True)
class ProcessRecord:
    pid: int
    name: str = ''
    exe: str = ''
    cmdline: tuple[str, ...] = ()
    create_time: float | None = None
    root_entries: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, 'exe', _path(self.exe))
        object.__setattr__(self, 'cmdline', _tokens(self.cmdline) if isinstance(self.cmdline, str) else tuple(self.cmdline))
        object.__setattr__(self, 'root_entries', tuple(str(x).casefold() for x in self.root_entries))
        object.__setattr__(self, 'warnings', tuple(self.warnings))


@dataclass(frozen=True)
class ServiceRecord:
    name: str
    command: str
    state: str = 'unknown'
    pid: int | None = None


@dataclass(frozen=True)
class TaskRecord:
    name: str
    command: str
    state: str = 'unknown'
    pid: int | None = None


@dataclass(frozen=True)
class ZapretItem:
    kind: str
    origin: str
    confidence: str
    active: bool
    evidence: tuple[str, ...]
    process: ProcessRecord | None = None
    service: ServiceRecord | None = None
    task: TaskRecord | None = None

    @property
    def pid(self) -> int | None:
        record = self.process or self.service or self.task
        return record.pid if record else None


@dataclass(frozen=True)
class ZapretSnapshot:
    items: tuple[ZapretItem, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def active(self) -> tuple[ZapretItem, ...]:
        return tuple(item for item in self.items if item.active and item.confidence == 'confirmed')

    @property
    def ambiguous(self) -> tuple[ZapretItem, ...]:
        return tuple(item for item in self.items if item.confidence == 'ambiguous')

    @property
    def active_count(self) -> int:
        # A service and its observed process describe one running instance.
        return len({('pid', item.pid) if item.pid else (item.kind, _item_name(item))
                    for item in self.active})

    @property
    def state(self) -> str:
        if self.active_count > 1:
            return 'multiple'
        if self.active:
            return 'managed' if self.active[0].origin == 'netpilot' else 'external'
        return 'ambiguous' if self.ambiguous else 'inactive'


def _item_name(item: ZapretItem) -> str:
    record = item.process or item.service or item.task
    return str(record.name).casefold()


class WindowsInventoryAdapter(Protocol):
    def iter_processes(self) -> Iterable[ProcessRecord]: ...
    def iter_services(self) -> Iterable[ServiceRecord]: ...
    def iter_tasks(self) -> Iterable[TaskRecord]: ...


def classify_process(record: ProcessRecord, known_roots: Iterable[str]) -> ZapretItem | None:
    executable = record.exe
    name = ntpath.basename(executable or record.name).casefold()
    if name not in {'winws.exe', 'winws2.exe'}:
        return None
    evidence = ['executable-name']
    managed = bool(executable) and any(
        executable.startswith(_path(root).rstrip('\\') + '\\') for root in known_roots if root)
    if managed:
        evidence.append('netpilot-root')
        return ZapretItem('process', 'netpilot', 'confirmed', True, tuple(evidence), process=record)
    arguments = tuple(arg.casefold() for arg in record.cmdline)
    has_filter = any(arg.startswith(('--wf-tcp=', '--wf-udp=')) for arg in arguments)
    has_desync = any(arg.startswith('--dpi-desync=') for arg in arguments)
    if has_filter and has_desync:
        evidence.append('zapret-arguments')
    root = ntpath.dirname(ntpath.dirname(executable)) if ntpath.basename(ntpath.dirname(executable)) == 'bin' else ''
    entries = set(record.root_entries)
    structure = {'bin', 'lists'}.issubset(entries) and bool({'utils', 'general.bat', 'service.bat'} & entries)
    # A fully qualified list under the executable's sibling lists directory is
    # a distinct layout signal, also available for service/task action records.
    list_layout = bool(root) and any(
        arg.startswith(('--hostlist=', '--ipset=')) and
        _path(arg.split('=', 1)[1]).startswith(root + '\\lists\\') for arg in arguments)
    if structure or list_layout:
        evidence.append('distribution-layout')
    confirmed = 'zapret-arguments' in evidence and 'distribution-layout' in evidence
    return ZapretItem('process', 'external', 'confirmed' if confirmed else 'ambiguous',
                      True, tuple(evidence), process=record)


def classify_service(record: ServiceRecord, known_roots: Iterable[str]) -> ZapretItem | None:
    """Classify the well-known Flowseal service using its executable and flags.

    A service named ``zapret`` alone is never sufficient evidence. Some
    Flowseal distributions do not expose a recognisable folder structure to
    the Windows SCM query, even though ImagePath points to winws with real
    Zapret arguments. This is a *read-only* classification; all mutations
    still require an explicit confirmation and a fresh service backup.
    """
    parts = _tokens(record.command)
    if not parts:
        return None
    process = ProcessRecord(record.pid or 0, exe=parts[0], cmdline=parts[1:])
    result = classify_process(process, known_roots)
    if result is None:
        return None
    evidence = list(result.evidence)
    confidence = result.confidence
    flags = tuple(str(flag).casefold() for flag in parts[1:])
    has_filter = any(flag.startswith(('--wf-tcp=', '--wf-udp=')) for flag in flags)
    has_desync = any(flag.startswith('--dpi-desync=') for flag in flags)
    if (confidence != 'confirmed' and record.name.casefold() == 'zapret' and
            ntpath.isabs(parts[0]) and has_filter and has_desync):
        # Three independent indicators: service identity, a full winws path,
        # and Zapret-specific filter/desynchronization arguments.
        confidence = 'confirmed'
        evidence.append('zapret-service-signature')
    return ZapretItem('service', result.origin, confidence,
                      record.state.casefold() == 'running', tuple(evidence), service=record)


def build_snapshot(adapter: WindowsInventoryAdapter, known_roots: Iterable[str]) -> ZapretSnapshot:
    roots = tuple(known_roots)
    items, warnings, seen = [], [], set()
    for kind, method in (('process', 'iter_processes'), ('service', 'iter_services'), ('task', 'iter_tasks')):
        try:
            for record in getattr(adapter, method)():
                identity = (kind, record.pid if kind == 'process' else record.name.casefold(),
                            record.command if kind == 'task' else '')
                if identity in seen:
                    continue
                seen.add(identity)
                if kind == 'process':
                    warnings.extend(record.warnings)
                    item = classify_process(record, roots)
                elif kind == 'service':
                    item = classify_service(record, roots)
                else:
                    parts = _tokens(record.command)
                    process = ProcessRecord(record.pid or 0, exe=parts[0] if parts else '', cmdline=parts[1:])
                    classified = classify_process(process, roots)
                    item = None if classified is None else ZapretItem(
                        kind, classified.origin, classified.confidence,
                        record.state.casefold() == 'running', classified.evidence,
                        task=record)
                if item is not None:
                    items.append(item)
        except Exception as exc:
            warnings.append(f'{kind} discovery incomplete: {type(exc).__name__}: {exc}')
    warnings.extend(getattr(adapter, 'warnings', ()))
    return ZapretSnapshot(tuple(items), tuple(dict.fromkeys(warnings)))


class PsutilWindowsInventoryAdapter:
    """Normalize psutil and bounded PowerShell read-only discovery results.

    Each query has its own timeout. Errors are surfaced by ``build_snapshot``;
    records yielded before an error and other sources remain available.
    """

    def __init__(self, timeout: float = 4.0):
        if timeout <= 0:
            raise ValueError('timeout must be positive')
        self.timeout = timeout

    def _cim_process_details(self, pid: int) -> dict:
        """Bounded read-only fallback for a named winws process.

        Some elevated Windows sessions still deny psutil.cmdline(). CIM may
        expose the identity without requiring any mutation of the process.
        We require PID AND executable name to match before accepting its data.
        """
        script = (
            '$p=Get-CimInstance Win32_Process -Filter "ProcessId = %d" '
            '-ErrorAction Stop; if($p) {$p | Select-Object ProcessId,Name,ExecutablePath,CommandLine '
            '| ConvertTo-Json -Compress}' % int(pid)
        )
        try:
            result = subprocess.run(
                ['powershell.exe','-NoProfile','-NonInteractive','-Command',script],
                capture_output=True,text=True,encoding='utf-8',errors='replace',
                timeout=min(self.timeout, 3.0),
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode != 0 or not result.stdout.strip():
                return {}
            value = json.loads(result.stdout.strip())
            if (not isinstance(value,dict) or int(value.get('ProcessId') or -1) != int(pid)
                    or str(value.get('Name') or '').casefold() not in {'winws.exe','winws2.exe'}):
                return {}
            return value
        except (ValueError, OSError, subprocess.SubprocessError):
            return {}

    def iter_processes(self) -> Iterable[ProcessRecord]:
        import psutil
        for process in psutil.process_iter():
            warnings = []

            def detail(name, default):
                try:
                    return getattr(process, name)() or default
                except (psutil.Error, OSError) as exc:
                    warnings.append(f'process {process.pid} {name}: {type(exc).__name__}')
                    return default

            name = detail('name', '')
            # Only Zapret candidates belong in this inventory. On Windows,
            # reading csrss/System/protected processes can fail with
            # AccessDenied; trying cmdline() for those unrelated processes
            # would incorrectly block EVERY Zapret service transfer.
            # A name-less process is not a confirmed Zapret candidate.
            if not name or name.casefold() not in {'winws.exe', 'winws2.exe'}:
                continue
            exe = detail('exe', '')
            cmdline = detail('cmdline', ())
            create_time = detail('create_time', None)
            if not exe or not cmdline:
                recovered = self._cim_process_details(process.pid)
                if not exe and recovered.get('ExecutablePath'):
                    exe = str(recovered['ExecutablePath'])
                    warnings = [w for w in warnings if ' exe:' not in w]
                if not cmdline and recovered.get('CommandLine'):
                    cmdline = _tokens(str(recovered['CommandLine']))
                    warnings = [w for w in warnings if ' cmdline:' not in w]
            entries = ()
            if exe and ntpath.basename(ntpath.dirname(exe)).casefold() == 'bin':
                root = Path(exe).parent.parent
                try:
                    # Only inspect fixed distribution markers, never recurse.
                    entries = tuple(marker for marker in ('bin', 'lists', 'utils', 'general.bat', 'service.bat')
                                    if (root / marker).exists())
                except OSError as exc:
                    warnings.append(f'process {process.pid} layout: {type(exc).__name__}')
            yield ProcessRecord(process.pid, name, exe, tuple(cmdline), create_time, entries, tuple(warnings))

    def _query(self, script: str) -> tuple[list[dict], tuple[str, ...]]:
        # Collect rows as they arrive so one inaccessible object (or even a
        # terminating error after earlier rows) cannot erase useful discovery.
        prefix = (
            '[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); '
            '$ErrorActionPreference="Continue"; '
            '$rows=[System.Collections.Generic.List[object]]::new(); '
            '$discoveryErrors=[System.Collections.Generic.List[string]]::new(); '
            'try { & { ')
        suffix = (
            ' } 2>&1 | ForEach-Object { '
            'if ($_ -is [System.Management.Automation.ErrorRecord]) '
            '{ $discoveryErrors.Add($_.ToString()) } else { $rows.Add($_) } } '
            '} catch { $discoveryErrors.Add($_.ToString()) }; '
            '[pscustomobject]@{Records=@($rows.ToArray()); Warnings=@($discoveryErrors.ToArray())} '
            '| ConvertTo-Json -Depth 4 -Compress')
        result = subprocess.run(
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', prefix + script + suffix],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=self.timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        data = json.loads(result.stdout.strip() or '[]')
        warnings = []
        if isinstance(data, dict) and 'Records' in data:
            rows = data['Records'] or []
            errors = data.get('Warnings') or []
            warnings.extend([errors] if isinstance(errors, str) else errors)
        else:
            rows = data
        if result.stderr.strip():
            warnings.append(result.stderr.strip())
        elif result.returncode:
            warnings.append(f'query exited {result.returncode}')
        return ([rows] if isinstance(rows, dict) else rows or []), tuple(warnings)

    def iter_services(self) -> Iterable[ServiceRecord]:
        rows, warnings = self._query('Get-CimInstance Win32_Service | Select-Object Name,PathName,State,ProcessId')
        for row in rows:
            yield ServiceRecord(str(row['Name']), str(row.get('PathName') or ''),
                                str(row.get('State') or 'unknown').casefold(), int(row.get('ProcessId') or 0) or None)
        if warnings:
            raise RuntimeError('; '.join(warnings))

    def iter_tasks(self) -> Iterable[TaskRecord]:
        rows, warnings = self._query(
            'Get-ScheduledTask | ForEach-Object { $task=$_; foreach($action in $task.Actions) {'
            ' [pscustomobject]@{Name=($task.TaskPath+$task.TaskName); State=[string]$task.State;'
            ' Command=(\'"\'+$action.Execute+\'" \'+$action.Arguments)} } }')
        for row in rows:
            yield TaskRecord(str(row['Name']), str(row.get('Command') or ''), str(row.get('State') or 'unknown').casefold())
        if warnings:
            raise RuntimeError('; '.join(warnings))
