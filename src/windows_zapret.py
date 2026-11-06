"""Concrete, bounded Windows boundary for privileged Zapret operations."""
from __future__ import annotations

import base64
import os
import re
from pathlib import Path
import subprocess
import time
import xml.etree.ElementTree as ET

from zapret_control import ServiceBackup
from zapret_inventory import PsutilWindowsInventoryAdapter, build_snapshot


_START_TO_NAME = {0: 'boot', 1: 'system', 2: 'automatic', 3: 'manual', 4: 'disabled'}
_NAME_TO_SC = {'boot': 'boot', 'system': 'system', 'automatic': 'auto',
               'automatic_delayed': 'delayed-auto', 'manual': 'demand', 'disabled': 'disabled'}
_TRANSACTION_VALUE = 'NetPilotTransaction'


class WindowsZapretAdapter:
    def __init__(self, known_roots=(), timeout=12.0):
        self.known_roots = tuple(str(Path(root).resolve()) for root in known_roots)
        self.timeout = float(timeout)
        if self.timeout <= 0:
            raise ValueError('timeout must be positive')

    def _run(self, argv, *, allow=(0,), timeout=None):
        result = subprocess.run(list(argv), capture_output=True, text=True,
                                encoding='utf-8', errors='replace',
                                timeout=timeout or self.timeout,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode not in allow:
            message = (result.stderr or result.stdout or f'code {result.returncode}').strip()
            raise RuntimeError(message[-1000:])
        return result

    def snapshot(self):
        return build_snapshot(PsutilWindowsInventoryAdapter(min(self.timeout, 8.0)), self.known_roots)

    def _process(self, pid, create_time):
        import psutil
        process = psutil.Process(int(pid))
        if abs(float(process.create_time()) - float(create_time)) > 0.01:
            raise ValueError('process identity changed; refusing mutation')
        return process

    def terminate_process(self, pid, *, create_time):
        self._process(pid, create_time).terminate()

    def kill_process(self, pid, *, create_time):
        self._process(pid, create_time).kill()

    def wait_process(self, pid, *, create_time, timeout):
        import psutil
        try:
            self._process(pid, create_time).wait(timeout=float(timeout))
            return True
        except psutil.NoSuchProcess:
            return True
        except psutil.TimeoutExpired:
            return False

    def _service_state(self, name):
        result = self._run(['sc.exe', 'query', name], allow=(0, 1060))
        if result.returncode == 1060:
            return None
        text = (result.stdout + result.stderr).upper()
        if 'RUNNING' in text:
            return 'running'
        if 'STOPPED' in text:
            return 'stopped'
        # Windows localizes state labels; SCM numeric states stay stable.
        numeric = re.search(r'(?m)^\s*(?:STATE|СОСТОЯНИЕ)\s*:\s*([1-7])\b',
                            result.stdout, re.I)
        if numeric and numeric.group(1) == '4':
            return 'running'
        if numeric and numeric.group(1) == '1':
            return 'stopped'
        raise RuntimeError('service is in a transitional or unknown state')

    def get_service_pid(self, name):
        """Read SCM PID without altering the service; unknown is not trusted."""
        if str(name).casefold() != 'zapret':
            raise ValueError('unexpected service identity')
        output = self._run(['sc.exe', 'queryex', name]).stdout
        match = re.search(r'(?im)^\s*PID\s*:\s*(\d+)\s*$', output)
        value = int(match.group(1)) if match else 0
        return value if value > 0 else None

    def _wait_service_state(self, name, expected):
        """SCM start/stop returns while the service is still pending."""
        deadline = time.monotonic() + min(self.timeout, 8.0)
        while time.monotonic() < deadline:
            try:
                state = self._service_state(name)
            except RuntimeError:  # START_PENDING / STOP_PENDING
                state = 'pending'
            if state == expected or (expected == 'stopped' and state is None):
                return
            time.sleep(0.2)
        raise RuntimeError(f'service did not reach {expected} state before timeout')

    def stop_service(self, name):
        self._run(['sc.exe', 'stop', name], allow=(0, 1062))
        self._wait_service_state(name, 'stopped')

    def start_service(self, name):
        self._run(['sc.exe', 'start', name])
        self._wait_service_state(name, 'running')

    def delete_service(self, name):
        self._run(['sc.exe', 'delete', name])
        deadline = time.monotonic() + min(self.timeout, 8.0)
        while time.monotonic() < deadline:
            if self._service_state(name) is None:
                return
            time.sleep(0.2)
        raise RuntimeError('service deletion did not complete')

    @staticmethod
    def _query_value(key, name, default=None):
        import winreg
        try:
            return winreg.QueryValueEx(key, name)[0]
        except FileNotFoundError:
            return default

    def _service_key(self, name, access):
        import winreg
        return winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                              rf'SYSTEM\CurrentControlSet\Services\{name}', 0, access)

    def get_service_backup(self, name):
        if os.name != 'nt':
            raise RuntimeError('Windows service management is only available on Windows')
        import winreg
        try:
            with self._service_key(name, winreg.KEY_READ) as key:
                start = int(self._query_value(key, 'Start'))
                delayed = bool(self._query_value(key, 'DelayedAutoStart', 0))
                mode = 'automatic_delayed' if start == 2 and delayed else _START_TO_NAME.get(start)
                if mode is None:
                    raise ValueError('unsupported service start mode')
                failure = self._query_value(key, 'FailureActions', b'')
                fields = {
                    'account': str(self._query_value(key, 'ObjectName', 'LocalSystem')),
                    'dependencies': list(self._query_value(key, 'DependOnService', []) or []),
                    'service_type': int(self._query_value(key, 'Type', 16)),
                    'error_control': int(self._query_value(key, 'ErrorControl', 1)),
                    'delayed': delayed,
                    'owned': str(self._query_value(key, 'NetPilotManaged', '')) == '1',
                    'strategy': str(self._query_value(key, 'zapret-discord-youtube', '') or ''),
                    'transaction_id': str(self._query_value(key, _TRANSACTION_VALUE, '') or ''),
                    'failure_actions': base64.b64encode(bytes(failure)).decode('ascii') if failure else '',
                }
                image = str(self._query_value(key, 'ImagePath', '') or '')
                display = str(self._query_value(key, 'DisplayName', name) or name)
                description = str(self._query_value(key, 'Description', '') or '')
        except FileNotFoundError:
            return None
        sd = self._run(['sc.exe', 'sdshow', name], allow=(0,)).stdout.strip()
        fields['security'] = sd.splitlines()[-1].strip() if sd else ''
        return ServiceBackup(name, image, mode, self._service_state(name), display, description, fields)

    def validate_service_backup(self, backup):
        if not isinstance(backup, ServiceBackup):
            raise ValueError('invalid service backup')
        fields = backup.adapter_fields
        required = {'account', 'dependencies', 'service_type', 'error_control', 'delayed',
                    'owned', 'strategy', 'transaction_id', 'security', 'failure_actions'}
        if not required <= set(fields):
            raise ValueError('incomplete service restoration metadata')
        account = str(fields['account']).casefold().replace(' ', '')
        if account not in {'localsystem', r'ntauthority\system'}:
            raise ValueError('service account credentials cannot be restored safely')
        if fields['service_type'] != 16 or fields['error_control'] not in {0, 1}:
            raise ValueError('unsupported service type or error control')
        if (not isinstance(fields['dependencies'], list) or
                not all(isinstance(value, str) for value in fields['dependencies'])):
            raise ValueError('invalid service dependencies')
        try:
            base64.b64decode(fields['failure_actions'], validate=True)
        except Exception as exc:
            raise ValueError('invalid service failure actions') from exc

    def _write_service_values(self, name, values):
        import winreg
        with self._service_key(name, winreg.KEY_SET_VALUE) as key:
            for value_name, value in values.items():
                if isinstance(value, bytes):
                    kind = winreg.REG_BINARY
                elif isinstance(value, int):
                    kind = winreg.REG_DWORD
                elif isinstance(value, list):
                    kind = winreg.REG_MULTI_SZ
                else:
                    kind = winreg.REG_SZ
                winreg.SetValueEx(key, value_name, 0, kind, value)

    def get_service_start_mode(self, name):
        backup = self.get_service_backup(name)
        if backup is None:
            raise ValueError('service is missing')
        return backup.start_mode

    def set_service_start_mode(self, name, mode):
        if mode not in _NAME_TO_SC:
            raise ValueError('unsupported service start mode')
        self._run(['sc.exe', 'config', name, 'start=', _NAME_TO_SC[mode]])

    def create_netpilot_service(self, transfer):
        self._run(['sc.exe', 'create', 'zapret', 'binPath=', transfer.image_path,
                   'DisplayName=', 'zapret', 'start=', 'auto'])
        try:
            self._run(['sc.exe', 'description', 'zapret', 'NetPilot managed Zapret service'])
            self._write_service_values('zapret', {
                'NetPilotManaged': '1',
                'zapret-discord-youtube': Path(transfer.request.strategy).stem,
                _TRANSACTION_VALUE: transfer.transaction_id,
            })
        except Exception:
            # The transaction layer re-identifies the marker before any cleanup.
            raise

    def restore_service(self, backup):
        self.validate_service_backup(backup)
        fields = backup.adapter_fields
        existing = self._service_state(backup.name)
        command = ['sc.exe', 'create' if existing is None else 'config', backup.name,
                   'binPath=', backup.image_path, 'DisplayName=', backup.display_name,
                   'start=', _NAME_TO_SC[backup.start_mode], 'obj=', 'LocalSystem']
        dependencies = '/'.join(fields['dependencies'])
        if dependencies:
            command += ['depend=', dependencies]
        self._run(command)
        self._run(['sc.exe', 'description', backup.name, backup.description])
        values = {
            'Type': fields['service_type'], 'ErrorControl': fields['error_control'],
            'DelayedAutoStart': int(bool(fields['delayed'])),
            'NetPilotManaged': '1' if fields['owned'] else '0',
            'zapret-discord-youtube': fields['strategy'],
            _TRANSACTION_VALUE: fields['transaction_id'],
        }
        if fields['failure_actions']:
            values['FailureActions'] = base64.b64decode(fields['failure_actions'])
        self._write_service_values(backup.name, values)
        if fields['security']:
            self._run(['sc.exe', 'sdset', backup.name, fields['security']])
        current = self._service_state(backup.name)
        if backup.state == 'running' and current != 'running':
            self.start_service(backup.name)
        elif backup.state == 'stopped' and current == 'running':
            self.stop_service(backup.name)

    def _task_xml(self, name):
        return self._run(['schtasks.exe', '/Query', '/TN', name, '/XML']).stdout

    def get_task_enabled(self, name):
        root = ET.fromstring(self._task_xml(name))
        for element in root.iter():
            if element.tag.rsplit('}', 1)[-1] == 'Enabled':
                return (element.text or '').strip().casefold() == 'true'
        raise ValueError('scheduled task has no Enabled setting')

    def set_task_enabled(self, name, enabled):
        self._run(['schtasks.exe', '/Change', '/TN', name, '/Enable' if enabled else '/Disable'])

    def start_task(self, name):
        self._run(['schtasks.exe', '/Run', '/TN', name])

    def stop_task(self, name):
        self._run(['schtasks.exe', '/End', '/TN', name], allow=(0, 1))
