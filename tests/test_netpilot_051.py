"""Pure regression tests: no Windows networking, GUI or UAC required."""
from __future__ import annotations
import tempfile
import threading
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import tray_position as t
import telegram_headless as tg
import managed as m
import benchmark as b


class Geometry(unittest.TestCase):
    def test_center_over_overflow_not_cursor(self):
        self.assertEqual(t.centered(t.Rect(250, 500, 450, 560), 300, 200,
                                    t.Rect(0, 0, 900, 1000)), (200, 300))

    def test_clamped_but_no_magic_offset(self):
        x, y = t.centered(t.Rect(700, 870, 900, 930), 300, 230,
                          t.Rect(0, 0, 920, 950))
        self.assertEqual(x, 620)
        self.assertEqual(y, 640)

    def test_fallback_when_below_screen(self):
        self.assertEqual(t.centered(t.Rect(30, 20, 220, 70), 180, 180,
                                    t.Rect(0, 0, 500, 350)), (35, 70))


class Telegram(unittest.TestCase):
    def test_stable_hex_secret(self):
        data = {}
        secret = tg.get_secret(data)
        self.assertEqual(len(secret), 32)
        self.assertEqual(tg.get_secret(data), secret)
        self.assertIn(secret, tg.connect_url(secret))

    def test_stop_only_owned_process(self):
        class Process:
            killed = False
            stopped = False
            def poll(self): return 0 if self.stopped else None
            def terminate(self): self.stopped = True
            def wait(self, timeout): return 0
            def kill(self): self.killed = True
        proc = Process()
        self.assertTrue(tg.stop(proc))
        self.assertTrue(proc.stopped)
        self.assertFalse(proc.killed)

    def test_no_stop_for_already_exited(self):
        class Process:
            def poll(self): return 0
            def terminate(self): raise AssertionError('must not terminate')
        self.assertTrue(tg.stop(Process()))


class Hosts(unittest.TestCase):
    def test_cloudflare_deduplicated_and_merged(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            (base/'lists').mkdir()
            original = (base/'lists'/'list-general-user.txt')
            original.write_text('cloudflare.com\ndiscord.com\n', encoding='utf-8')
            cloud = base/'cloudflare-domains.txt'
            cloud.write_text('cloudflare.com\ncloudflareaccess.com\n', encoding='utf-8')
            custom = base/'domains.txt'
            custom.write_text('discord.com\nyoutube.com\n', encoding='utf-8')
            with patch.object(m, 'CLOUDFLARE_FILE', cloud), patch.object(m, 'DOMAINS_FILE', custom):
                count, bad = m.merge_domains(base)
                self.assertEqual((count, bad), (1, []))
                self.assertEqual(m.merge_domains(base), (0, []))
            self.assertEqual(original.read_text().splitlines(),
                             ['cloudflare.com','discord.com','cloudflareaccess.com'])


class Strategy(unittest.TestCase):
    def test_benchmark_has_no_baseline_scan(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / 'general.bat'
            script.write_text('@echo off',encoding='utf-8')
            from catalog import Catalog
            cat = Catalog(('example.net',), {}, 0, ())
            calls = []
            def scanner(hosts, cancelled, progress=None):
                calls.append(hosts)
                if progress:progress(1, 1)
                return [{'host':'example.net', 'ok':True, 'ms':20}]
            with patch.object(b,'preflight',return_value=[str(script)]),\
                 patch.object(b,'load_catalog',return_value=cat),\
                 patch.object(b,'launch_hidden',return_value=[]),\
                 patch.object(b,'scan_hosts',side_effect=scanner),\
                 patch.object(b,'stop_ours'):\
                result = b.benchmark(root, lambda pct,text:None, threading.Event(),keep_best=False)
            self.assertEqual(len(calls),1)
            self.assertEqual(result['baseline'],None)
            self.assertEqual(result['best']['passed'],1)


if __name__ == '__main__':
    unittest.main()
