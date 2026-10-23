"""Flowseal's official *console* proxy, run as a NetPilot-owned child.

The upstream Windows tray EXE is intentionally not used: it always creates its
own icon.  Dependency is pinned to a specific upstream source commit at build
 time. No child is detached and no unrelated system process is terminated.
"""
from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
from pathlib import Path


PORT = 1443


def get_secret(config: dict) -> str:
    value = config.get('telegram_secret', '')
    if not isinstance(value, str) or len(value) != 32 or any(c not in '0123456789abcdef' for c in value):
        value = secrets.token_hex(16)
        config['telegram_secret'] = value
    return value


def connect_url(secret: str) -> str:
    return f'tg://proxy?server=127.0.0.1&port={PORT}&secret={secret}'


def port_in_use(port: int = PORT) -> bool:
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=0.2):
            return True
    except (ConnectionRefusedError, TimeoutError, OSError):
        return False


def spawn(config: dict, *, frozen: bool | None = None, executable: str | None = None, script: str | None = None):
    """Start bundled Flowseal CLI with a secret that persists in NetPilot config."""
    if port_in_use():
        raise RuntimeError(f'Порт {PORT} уже занят. Закройте старый Telegram WS Proxy через его трей.')
    secret = get_secret(config)
    if frozen is None:
        frozen = bool(getattr(sys, 'frozen', False))
    exe = executable or sys.executable
    if frozen:
        command = [exe, '--tg-headless']
    else:
        command = [exe, script or str(Path(__file__).with_name('app.py')), '--tg-headless']
    command += ['--host', '127.0.0.1', '--port', str(PORT)]
    env = os.environ.copy()
    env['NETPILOT_TG_SECRET'] = secret  # not exposed in the process command line
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    startupinfo = None
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= getattr(subprocess, 'STARTF_USESHOWWINDOW', 1)
        startupinfo.wShowWindow = getattr(subprocess, 'SW_HIDE', 0)
    return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, creationflags=flags, startupinfo=startupinfo, env=env)


def stop(proc, timeout=4.0):
    """Stop only this owned direct child; no taskkill /IM and no other tray EXEs."""
    if proc is None or proc.poll() is not None:
        return True
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)
    return proc.poll() is not None


def worker_main(argv):
    """Run the official upstream non-tray command, not its Windows GUI entry point."""
    from proxy.tg_ws_proxy import main
    secret = os.environ.pop('NETPILOT_TG_SECRET', '')
    if len(secret) != 32 or any(c not in '0123456789abcdef' for c in secret):
        raise RuntimeError('Missing or invalid NetPilot Telegram secret')
    sys.argv = ['tg-ws-proxy'] + list(argv) + ['--secret', secret]
    main()
