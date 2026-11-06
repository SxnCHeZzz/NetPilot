"""Passive, network-free throughput sampling for the whole computer.

This is NOT a speed test and NOT per-Zapret/Telegram/WARP accounting.
Only the operating system's existing byte counters are read. No probes are
sent, and no internet connection is opened by this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from time import monotonic


@dataclass(frozen=True)
class TrafficReading:
    download_bps: float
    upload_bps: float
    received_bytes: int
    sent_bytes: int


def format_rate(bytes_per_second: float) -> str:
    rate = max(0.0, float(bytes_per_second))
    for unit in ('Б/с', 'КБ/с', 'МБ/с', 'ГБ/с'):
        if rate < 1024 or unit == 'ГБ/с':
            return f'{rate:.0f} {unit}' if rate >= 100 else f'{rate:.1f} {unit}'
        rate /= 1024.0
    return '0 Б/с'


class TrafficSampler:
    """Delta-based sampler; caller chooses when to poll it, typically ~1.2 s."""
    def __init__(self, counters=None, clock=None):
        self._counters = counters
        self._clock = clock or monotonic
        self._previous = None

    def reset(self):
        self._previous = None

    def sample(self):
        if self._counters is None:
            try:
                import psutil
                self._counters = psutil.net_io_counters
            except ImportError:
                return None
        current = self._counters()
        if current is None:
            return None
        now = self._clock()
        recv, sent = int(current.bytes_recv), int(current.bytes_sent)
        last = self._previous
        self._previous = (now, recv, sent)
        if last is None:
            return None
        duration = now - last[0]
        if duration <= 0:
            return None
        # A counter reset / network-adapter change must not produce spikes.
        down = max(0, recv-last[1]) / duration if recv >= last[1] else 0
        up = max(0, sent-last[2]) / duration if sent >= last[2] else 0
        return TrafficReading(down, up, recv, sent)
