"""Resolve complete, versioned HTTPS test targets from installed Flowseal hostlists.

Never replaces upstream files. Domain suffix rules are tested at their apex: such
probes do not establish that every possible subdomain is reachable.
"""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import ipaddress
import re

HOST_FILES = ('list-general.txt', 'list-general-user.txt', 'list-google.txt')
EXCLUDE_FILES = ('list-exclude.txt', 'list-exclude-user.txt')
HOST_RE = re.compile(r'(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?', re.I)


@dataclass(frozen=True)
class Catalog:
    hosts: tuple[str, ...]
    sources: dict[str, int]
    exclusions: int
    skipped: tuple[str, ...]


def normalize_domain(raw: str) -> str | None:
    value = raw.strip().lower().rstrip('.')
    if not value or value.startswith(('#', ';')):
        return None
    # Upstream hostlists support domains, not URL paths and addresses.
    if value.startswith('*.'):
        value = value[2:]
    if value.startswith('.'):
        value = value[1:]
    if '://' in value or '/' in value or ':' in value or ' ' in value or '*' in value:
        return None
    try:
        ipaddress.ip_address(value)
        return None
    except ValueError:
        pass
    try:
        ascii_host = value.encode('idna').decode('ascii')
    except UnicodeError:
        return None
    return ascii_host if HOST_RE.fullmatch(ascii_host) else None


def read_domains(path: Path) -> tuple[set[str], list[str]]:
    good, bad = set(), []
    if not path.is_file():
        return good, bad
    for line in path.read_text('utf-8-sig', errors='replace').splitlines():
        raw = line.strip()
        if not raw or raw.startswith(('#', ';')):
            continue
        value = normalize_domain(raw)
        if value:
            good.add(value)
        else:
            bad.append(raw[:150])
    return good, bad


def is_excluded(host: str, exclusions: set[str]) -> bool:
    return any(host == entry or host.endswith('.' + entry) for entry in exclusions)


def load_catalog(zapret_folder: Path) -> Catalog:
    lists = Path(zapret_folder) / 'lists'
    entries, skipped, sources = set(), [], {}
    for file in HOST_FILES:
        vals, invalid = read_domains(lists / file)
        entries.update(vals)
        skipped.extend(f'{file}: {v}' for v in invalid)
        sources[file] = len(vals)
    excluded = set()
    for file in EXCLUDE_FILES:
        vals, invalid = read_domains(lists / file)
        excluded.update(vals)
        skipped.extend(f'{file}: {v}' for v in invalid)
    # The upstream placeholder is not a real test target.
    entries.discard('domain.example.abc')
    ordered = tuple(sorted(host for host in entries if not is_excluded(host, excluded)))
    if not ordered:
        raise ValueError('Нет проверяемых доменов: загрузите Flowseal со списками lists/list-*.txt.')
    return Catalog(ordered, sources, len(entries) - len(ordered), tuple(skipped))


def bucket(host: str) -> str:
    h = host.lower()
    for suffix, name in (
        ('youtube.com', 'YouTube'), ('googlevideo.com', 'YouTube'), ('ytimg.com', 'YouTube'),
        ('discord.com', 'Discord'), ('discord.gg', 'Discord'), ('discordapp.com', 'Discord'),
        ('discordapp.net', 'Discord'), ('discordcdn.com', 'Discord'),
        ('twitch.tv', 'Twitch'), ('twitchcdn.net', 'Twitch'),
        ('cloudflare.com', 'Cloudflare'), ('one.one.one.one', 'Cloudflare'),
    ):
        if h == suffix or h.endswith('.' + suffix):
            return name
    return 'Другие'


def groups(hosts):
    return dict(Counter(bucket(h) for h in hosts))
