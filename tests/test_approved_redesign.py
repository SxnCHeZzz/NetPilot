"""Non-Windows regression coverage for the approved forest-green UX work."""
import os
import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))

from privileged import snapshot_digest
from zapret_inventory import ProcessRecord, ZapretItem, ZapretSnapshot


class InventoryFingerprintTests(unittest.TestCase):
    def test_privilege_warning_text_change_does_not_trigger_false_staleness(self):
        original = ZapretSnapshot(warnings=('process 322 cmdline: AccessDenied',))
        elevated = ZapretSnapshot(warnings=())
        self.assertEqual(snapshot_digest(original),snapshot_digest(elevated))

    def test_same_confirmed_process_with_different_access_diagnostics_is_stable(self):
        p = ProcessRecord(355,'winws.exe',r'C:\Zapret\bin\winws.exe',('--dpi-desync=fake',),10.0,
                          warnings=('process 355 cmdline: AccessDenied',))
        a = ZapretItem('process','netpilot','confirmed',True,('netpilot-root',),process=p)
        b = replace(a,process=replace(p,warnings=()))
        self.assertEqual(snapshot_digest(ZapretSnapshot((a,),('warning',))),
                         snapshot_digest(ZapretSnapshot((b,),())))

    def test_confirmed_process_identity_change_invalidates_request(self):
        a = ZapretItem('process','netpilot','confirmed',True,('netpilot-root',),
                       process=ProcessRecord(355,'winws.exe',r'C:\\Zapret\\bin\\winws.exe',('--dpi-desync=fake',),10.0))
        b = replace(a,process=replace(a.process,pid=356))
        self.assertNotEqual(snapshot_digest(ZapretSnapshot((a,))),
                            snapshot_digest(ZapretSnapshot((b,))))

    def test_command_line_change_invalidates_request(self):
        a = ZapretItem('process','netpilot','confirmed',True,('netpilot-root',),
                       process=ProcessRecord(355,'winws.exe',r'C:\\Zapret\\bin\\winws.exe',('--wf-tcp=80',),10.0))
        b = replace(a,process=replace(a.process,cmdline=('--wf-tcp=443',)))
        self.assertNotEqual(snapshot_digest(ZapretSnapshot((a,))),
                            snapshot_digest(ZapretSnapshot((b,))))


if __name__=='__main__': unittest.main()
