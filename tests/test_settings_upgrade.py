import sys
import tempfile
import json
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import managed

class UserSettingsTests(unittest.TestCase):
    def test_preferences_survive_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with patch.object(managed,'ROOT',root), patch.object(managed,'SETTINGS',root/'settings.json'):
                obj=managed.read_cfg()
                obj['auto_apply']=False
                obj['auto_upgrade']=True
                obj['start_hidden']=True
                managed.write_cfg(obj)
                second=managed.read_cfg()
                self.assertIs(second['auto_apply'], False)
                self.assertIs(second['auto_upgrade'], True)
                self.assertIs(second['start_hidden'], True)

    def test_default_is_opt_in_to_component_auto_upgrades(self):
        self.assertFalse(managed.DEFAULT['auto_upgrade'])

    def test_no_unsafe_windows_archive_paths(self):
        import io, zipfile
        for name in ('folder/../../escape.txt', 'folder/file:stream', 'CON.txt', 'dir/NUL'):
            data=io.BytesIO()
            with zipfile.ZipFile(data,'w') as z:z.writestr(name,'x')
            with tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(ValueError):managed.safe_unpack(data.getvalue(),Path(folder))

if __name__=='__main__':unittest.main()
