import io
import json
import zipfile
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import managed as m

class TestManaged(unittest.TestCase):
    def test_host_list_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            n,_=m.merge_domains(temp)
            self.assertGreater(n,30)
            p=Path(temp)/'lists'/'list-general-user.txt'
            self.assertNotIn('arcraiders.com',p.read_text())
            self.assertIn('cloudflare-ech.com',p.read_text())
            self.assertNotIn('video-edge-*.twitchcdn.net',p.read_text())
            x,_=m.merge_domains(temp)
            self.assertEqual(x,0)
            added,_=m.merge_domains(temp, include_extended=True)
            self.assertGreater(added,100)
            self.assertIn('arcraiders.com',p.read_text())
    def test_game_filter(self):
        with tempfile.TemporaryDirectory() as temp:
            m.set_game_filter(temp,'all')
            self.assertEqual(m.zapret_options(temp)['game'],'all')
            with self.assertRaises(ValueError):m.set_game_filter(temp,'../../evil')
            m.set_upstream_check(temp,True)
            self.assertTrue(m.zapret_options(temp)['update'])
    def test_zip_slip_rejected(self):
        b=io.BytesIO()
        with zipfile.ZipFile(b,'w') as f:f.writestr('../evil.txt','evil')
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):m.safe_unpack(b.getvalue(),Path(temp)/'x')
            self.assertFalse((Path(temp)/'evil.txt').exists())
    def test_verify_digest(self):
        import hashlib
        b=b'hello';s='sha256:'+hashlib.sha256(b).hexdigest()
        self.assertEqual(m.check_digest(b,s),hashlib.sha256(b).hexdigest())
        with self.assertRaises(ValueError):m.check_digest(b,'sha256:'+'0'*64)
    def test_install_update_and_rollback_preserve_custom_domains(self):
        with tempfile.TemporaryDirectory() as root:
            cfg={'components_dir':root,'installed':{}}
            archive=io.BytesIO()
            with zipfile.ZipFile(archive,'w') as f:
                f.writestr('zapret/service.bat','@echo off\n')
                f.writestr('zapret/general.bat','@echo off\n')
                f.writestr('zapret/bin/winws.exe','stub')
                f.writestr('zapret/lists/list-general-user.txt','domain.example.com\n')
            meta={'kind':'zapret','tag':'v1','url':'https://github.com/a','name':'a.zip','digest':None}
            with patch.object(m,'fetch_bytes',return_value=archive.getvalue()),patch.object(m,'write_cfg'):
                p=m.install_component(cfg,meta)
                self.assertIn('cloudflareaccess.com',(Path(p)/'lists'/'list-general-user.txt').read_text())
                (Path(p)/'lists'/'list-general-user.txt').write_text('mycustom.org\n')
                meta['tag']='v2';m.install_component(cfg,meta)
                self.assertIn('mycustom.org',(Path(p)/'lists'/'list-general-user.txt').read_text())
                m.rollback_component(cfg,'zapret')
                self.assertIn('mycustom.org',(Path(p)/'lists'/'list-general-user.txt').read_text())

if __name__=='__main__':unittest.main()
