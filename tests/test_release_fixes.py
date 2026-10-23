"""Focused regressions for NetPilot 0.5.2 prerelease."""
import sys, unittest, threading, tempfile, subprocess
from pathlib import Path
from unittest.mock import patch, Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import benchmark as b
from catalog import Catalog

class CurlTests(unittest.TestCase):
    def test_parse_upstream_targets(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); (root/'utils').mkdir()
            (root/'utils'/'targets.txt').write_text('Discord_Main = "https://discord.com"\n' +
                'DiscordGateway = "https://gateway.discord.gg"\n' +
                'CloudflareDNS = "PING:1.1.1.1"\ninvalid line\n',encoding='utf-8')
            self.assertEqual([t[1] for t in b.parse_flowseal_targets(root)],
                             ['discord.com','gateway.discord.gg'])
    def test_head_probe_counts_http_denial_as_reachable(self):
        def run(args,**kw):
            self.assertIn('--head',args)
            self.assertIn('--http1.1',args)
            self.assertNotIn('--noproxy',args)
            return subprocess.CompletedProcess(args,0,'403 0.252','')
        with patch.object(b.shutil,'which',return_value='curl.exe'),patch.object(b.subprocess,'run',side_effect=run):
            result=b.curl_probe('discord.com')
        self.assertTrue(result['ok'])
        self.assertEqual(result['status'],403)
    def test_upstream_tls_variants_and_full_catalog(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'utils').mkdir()
            (root/'utils'/'targets.txt').write_text('Discord="https://discord.com"',encoding='utf-8')
            seen=[];done=[]
            def fake_probe(host,*,url=None,tls='http1',timeout=5.0):
                seen.append((host,tls))
                return {'host':host,'ok':True,'ms':12}
            with patch.object(b,'curl_probe',side_effect=fake_probe):
                output=b.probe_flowseal_set(root,['a.example','b.example'],threading.Event(),lambda x,y:done.append((x,y)),)
            self.assertEqual(len(output),5)
            self.assertEqual({mode for _,mode in seen},{'http1','tls12','tls13'})
            self.assertEqual(done[-1],(5,5))
    def test_benchmark_restores_managed_strategy_on_failed_tests(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);s=root/'general.bat';s.touch()
            calls=[]
            class FakeProcess:
                def __init__(self,pid):self.pid=pid
            def launch(folder,script):
                calls.append(script.name)
                return [FakeProcess(3000+len(calls))]
            with patch.object(b,'stop_managed_pids') as stop, \
                 patch.object(b,'preflight',return_value=[str(s)]), \
                 patch.object(b,'load_catalog',return_value=Catalog(('example.com',),{},0,())), \
                 patch.object(b,'launch_hidden',side_effect=launch), \
                 patch.object(b,'stop_ours'), \
                 patch.object(b,'probe_flowseal_set',return_value=[{'ok':False,'ms':None}]):
                out=b.benchmark(root,lambda a,c:None,threading.Event(),owned_pids=[1234],previous_strategy='general.bat',keep_best=True)
            stop.assert_called_once()
            self.assertEqual(len(calls),2)
            self.assertEqual(out['active_pids'],[3002])
            self.assertIsNone(out['best'])

if __name__=='__main__':unittest.main()
