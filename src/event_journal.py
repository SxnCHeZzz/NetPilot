"""Small, bounded local history of NetPilot user-visible notifications.

The journal is independent of Qt and never sends messages to any service.
Entries are stored in the user's application-data folder and shortened/redacted
before writing.  Notification display is a separate, best-effort UI concern.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

MAX_EVENTS = 60
MAX_TEXT = 260
_SECRET = re.compile(r'(?i)(\b(?:token|password|secret|apikey|api_key)\b\s*[:=]\s*)[^\s,;]+')
_PROXY = re.compile(r'(?i)(tg://proxy\?[^\s]*?secret=)[^\s&]+')


def safe_text(value, limit=MAX_TEXT):
    text = ' '.join(str(value).split())
    text = _SECRET.sub(r'\1[hidden]', text)
    text = _PROXY.sub(r'\1[hidden]', text)
    return text[:limit]


class EventJournal:
    def __init__(self, path: Path | None = None, limit: int = MAX_EVENTS):
        self.path = Path(path) if path is not None else None
        self.limit = max(1, limit)
        self.entries: list[dict] = []
        if self.path is not None:
            try:
                data = json.loads(self.path.read_text(encoding='utf-8'))
                if isinstance(data, list):
                    self.entries = [row for row in data if isinstance(row, dict)][-self.limit:]
            except (OSError, ValueError, TypeError):
                pass

    def add(self, title, message, error=False):
        item = {
            'time': datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S'),
            'level': 'error' if error else 'info',
            'title': safe_text(title, 90),
            'message': safe_text(message),
        }
        self.entries.append(item)
        self.entries = self.entries[-self.limit:]
        if self.path is not None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix('.tmp')
                tmp.write_text(json.dumps(self.entries, ensure_ascii=False, indent=2), encoding='utf-8')
                tmp.replace(self.path)
            except OSError:
                # A read-only profile must not stop NetPilot from operating.
                pass
        return item

    def recent_text(self, count=5):
        rows = self.entries[-max(1, count):]
        if not rows:
            return 'Событий пока нет.'
        return '\n'.join(
            f"{row.get('time','')[-8:]} · {'Ошибка' if row.get('level')=='error' else 'Событие'}: "
            f"{row.get('message','')}" for row in reversed(rows)
        )
