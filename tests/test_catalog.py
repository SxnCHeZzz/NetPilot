"""Pure unit tests for complete upstream hostlist loading. No network calls."""
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import catalog
import benchmark

class HostlistTests(unittest.TestCase):
    def test_deduplicate_exclude_and_cover_all_sources(self):
        with tempfile.TemporaryDirectory() as td:
            target=Path(td)/'lists';target.mkdir()
            (target/'list-general.txt').write_text('youtube.com\ndiscord.com\nvideo.example.org\n',encoding='utf-8')
            (target/'list-general-user.txt').write_text('youtube.com\nmy.example.org\ndomain.example.abc\n',encoding='utf-8')
            (target/'list-google.txt').write_text('googlevideo.com\n',encoding='utf-8')
            (target/'list-exclude.txt').write_text('example.org\n',encoding='utf-8')
            scan=catalog.load_catalog(td)
            self.assertEqual(scan.hosts,('discord.com','googlevideo.com','youtube.com'))
            self.assertEqual(scan.sources['list-general.txt'],3)
            self.assertEqual(scan.exclusions,2)

    def test_rejects_url_ip_and_wildcard_middle(self):
        self.assertIsNone(catalog.normalize_domain('https://youtube.com'))
        self.assertIsNone(catalog.normalize_domain('8.8.8.8'))
        self.assertIsNone(catalog.normalize_domain('video-edge-*.twitchcdn.net'))
        self.assertEqual(catalog.normalize_domain('*.discord.com'),'discord.com')

    def test_all_hosts_scanned_and_progress(self):
        values=['site-one.com','site-two.com','site-three.com','site-four.com']
        done=[]
        with patch.object(benchmark,'probe_host',lambda name:dict(host=name,ok=True,ms=11)):
            results=benchmark.scan_hosts(values,threading.Event(),lambda d,t:done.append((d,t)),max_workers=2)
        self.assertEqual(len(results),4)
        self.assertEqual(done[-1],(4,4))
        self.assertEqual(benchmark.score(results),(4,11))

    def test_cancel_before_start(self):
        cancel=threading.Event();cancel.set()
        with self.assertRaises(benchmark.BenchmarkError):
            benchmark.scan_hosts(['example.com'],cancel)

if __name__=='__main__':unittest.main()
