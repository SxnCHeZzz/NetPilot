"""Flowseal-compatible Windows service management for NetPilot.

The upstream Flowseal ``service.bat`` installs service ``zapret`` with an
AUTO_START winws.exe command and writes ``zapret-discord-youtube`` to the
service registry key.  This non-interactive adapter uses the same service
contract; it does not automate the fragile menu or install another service.

Only identified Flowseal/legacy NetPilot services may be reconfigured.  The
original SCM configuration is kept for rollback, and WinDivert is untouched.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PureWindowsPath
import re

from managed import list_strategies, validated_zapret_service_command

SERVICE = 'zapret'
UPSTREAM_MARKER = 'zapret-discord-youtube'


class FlowsealServiceError(RuntimeError):
    """A service could not be safely identified, installed, or verified."""


@dataclass(frozen=True)
class FlowsealResult:
    strategy: str
    action: str


def _service_executable(image_path: str) -> str:
    """Read the executable path from an SCM ImagePath (never execute it)."""
    value = str(image_path).strip()
    if value.startswith('"'):
        end = value.find('"', 1)
        return value[1:end] if end > 1 else ''
    match = re.match(r'(?i)^(.+?winws2?\.exe)(?:\s|$)', value)
    return match.group(1).strip() if match else ''


def identify_flowseal_service(backup, root: Path) -> bool:
    """Prevent replacing an unrelated service that happens to be named zapret.

    A Flowseal installation must use a winws binary, and have either its
    official registry strategy marker, the upstream service.bat next to its
    bin directory, or the legacy NetPilot marker tied to our selected root.
    """
    if backup is None or str(backup.name).casefold() != SERVICE:
        return False
    exe = _service_executable(backup.image_path)
    if PureWindowsPath(exe).name.casefold() not in {'winws.exe', 'winws2.exe'}:
        return False
    fields = backup.adapter_fields or {}
    if fields.get('strategy'):
        return True
    folder = Path(exe).parent.parent
    try:
        if (folder / 'service.bat').is_file():
            return True
        if fields.get('owned') and folder.resolve() == Path(root).resolve():
            return True
    except (OSError, ValueError):
        return False
    return False


def _normalized_command(value: str) -> str:
    return re.sub(r'\s+', ' ', str(value).strip()).casefold()


class FlowsealServiceManager:
    """Narrow SCM implementation, parameterized for deterministic tests."""

    def __init__(self, system, *, validate=validated_zapret_service_command):
        self.system = system
        self.validate = validate

    def _existing(self, root):
        backup = self.system.get_service_backup(SERVICE)
        if backup is not None:
            if not identify_flowseal_service(backup, root):
                raise FlowsealServiceError(
                    'Служба zapret не подтверждена как установка Flowseal. '
                    'Проверьте её путь и конфигурацию в диагностике; она не изменена.')
            self.system.validate_service_backup(backup)
        return backup

    def _assert_running(self, image_path):
        live = self.system.get_service_backup(SERVICE)
        if (live is None or self.system._service_state(SERVICE) != 'running' or
                live.start_mode != 'automatic' or
                _normalized_command(live.image_path) != _normalized_command(image_path)):
            raise FlowsealServiceError(
                'Windows не подтвердила путь, стратегию, автозапуск и работу службы Zapret.')

    def apply(self, root: str | Path, strategy: str) -> FlowsealResult:
        folder = Path(root).resolve()
        if not (folder / 'service.bat').is_file():
            raise FlowsealServiceError('Не найден оригинальный service.bat Flowseal в папке Zapret.')
        if strategy not in list_strategies(folder):
            raise FlowsealServiceError('Выбранная стратегия отсутствует в установке Flowseal.')
        exe, args = self.validate(folder, strategy)
        image_path = f'"{exe}" {args}'
        old = self._existing(folder)
        # Reapplying an already active Flowseal strategy is a no-op: avoid a
        # network interruption, service replacement and extra driver churn.
        if (old is not None and old.state == 'running' and
                old.start_mode == 'automatic' and
                _normalized_command(old.image_path) == _normalized_command(image_path) and
                (old.adapter_fields or {}).get('strategy') == Path(strategy).stem):
            return FlowsealResult(strategy, 'enabled')
        created = False
        modified = False
        try:
            # Update an existing, verified Flowseal service in place instead
            # of deleting it.  A removed-and-recreated service is vulnerable to
            # races and loses security/dependency settings.
            if old is not None:
                if old.state == 'running':
                    self.system.stop_service(SERVICE)
                modified = True
                self.system._run(['sc.exe', 'config', SERVICE, 'binPath=', image_path,
                                  'start=', 'auto'])
            else:
                self.system._run(['sc.exe', 'create', SERVICE, 'binPath=', image_path,
                                  'DisplayName=', SERVICE, 'start=', 'auto'])
                created = True
                self.system._run(['sc.exe', 'description', SERVICE,
                                  'Zapret DPI bypass software'])
            # Flowseal's own marker, not a new proprietary service identity.
            self.system._write_service_values(SERVICE, {
                UPSTREAM_MARKER: Path(strategy).stem,
                'NetPilotManaged': '0',
                'NetPilotTransaction': '',
            })
            self.system.start_service(SERVICE)
            self._assert_running(image_path)
            return FlowsealResult(strategy, 'enabled')
        except Exception as error:
            rollback_error = None
            try:
                if old is not None and modified:
                    if self.system._service_state(SERVICE) == 'running':
                        self.system.stop_service(SERVICE)
                    self.system.restore_service(old)
                elif created:
                    current = self.system.get_service_backup(SERVICE)
                    if (current is not None and
                            _normalized_command(current.image_path) == _normalized_command(image_path)):
                        if self.system._service_state(SERVICE) == 'running':
                            self.system.stop_service(SERVICE)
                        self.system.delete_service(SERVICE)
            except Exception as recovery_error:
                rollback_error = recovery_error
            message = f'Не удалось включить стратегию через Flowseal: {error}'
            if rollback_error is not None:
                message += f'; восстановление службы не завершено: {rollback_error}'
            raise FlowsealServiceError(message) from error

    def stop(self, root: str | Path) -> FlowsealResult:
        backup = self._existing(Path(root).resolve())
        if backup is None:
            return FlowsealResult('', 'already_stopped')
        try:
            if backup.state == 'running':
                self.system.stop_service(SERVICE)
            self.system.set_service_start_mode(SERVICE, 'disabled')
            live = self.system.get_service_backup(SERVICE)
            if (self.system._service_state(SERVICE) != 'stopped' or live is None or
                    live.start_mode != 'disabled'):
                raise FlowsealServiceError('Остановка или отключение автозапуска не подтверждены Windows.')
        except Exception as error:
            try:
                self.system.restore_service(backup)
            except Exception as recovery_error:
                raise FlowsealServiceError(f'{error}; откат не удался: {recovery_error}') from error
            raise FlowsealServiceError(str(error)) from error
        return FlowsealResult('', 'stopped')


def apply_flowseal_service(folder: Path, strategy: str) -> FlowsealResult:
    if os.name != 'nt':
        raise RuntimeError('Служба Flowseal поддерживается только Windows.')
    from windows_zapret import WindowsZapretAdapter
    return FlowsealServiceManager(WindowsZapretAdapter((folder,), timeout=25)).apply(folder, strategy)


def stop_flowseal_service(folder: Path) -> FlowsealResult:
    if os.name != 'nt':
        raise RuntimeError('Служба Flowseal поддерживается только Windows.')
    from windows_zapret import WindowsZapretAdapter
    return FlowsealServiceManager(WindowsZapretAdapter((folder,), timeout=25)).stop(folder)


def benchmark_with_service_restored(folder, notify, cancel, **kwargs):
    """Temporarily stop a confirmed Flowseal service for a *manual* benchmark.

    The service's SCM configuration and autostart are unchanged.  Whether the
    benchmark succeeds, fails or is cancelled, attempt to restore its previous
    running state.  No strategy is applied until the user confirms separately.
    """
    import benchmark
    from windows_zapret import WindowsZapretAdapter
    adapter = WindowsZapretAdapter((folder,), timeout=25)
    manager = FlowsealServiceManager(adapter)
    backup = manager._existing(Path(folder).resolve())
    was_running = bool(backup is not None and backup.state == 'running')
    if was_running:
        notify(0, 'Временно останавливаем службу Flowseal для сравнения стратегий…')
        adapter.stop_service(SERVICE)
    problem = None
    result = None
    try:
        result = benchmark.benchmark(
            folder, notify, cancel, keep_best=False,
            service_paused=backup is not None, **kwargs)
    except Exception as error:
        problem = error
    finally:
        if was_running:
            try:
                notify(99, 'Восстанавливаем прежнюю службу Flowseal…')
                adapter.start_service(SERVICE)
                if adapter._service_state(SERVICE) != 'running':
                    raise FlowsealServiceError('После теста служба не восстановила состояние RUNNING')
            except Exception as restore_error:
                if problem is not None:
                    raise FlowsealServiceError(
                        f'Подбор прервался: {problem}; восстановление службы не удалось: {restore_error}') from restore_error
                raise FlowsealServiceError(
                    f'Подбор завершён, но служба Flowseal не восстановилась: {restore_error}') from restore_error
    if problem is not None:
        raise problem
    return result
