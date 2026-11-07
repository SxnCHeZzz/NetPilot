"""Non-Windows regression tests for the new Flowseal-compatible SCM adapter."""
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from dataclasses import replace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from flowseal_service import (FlowsealServiceManager, FlowsealServiceError,
                              identify_flowseal_service, benchmark_with_service_restored)
from zapret_control import ServiceBackup


class FakeSCM:
    def __init__(self, backup=None):
        self.backup=backup
        self.calls=[]
        self.fail_start=False
    def get_service_backup(self, name):
        self.calls.append(('read',name))
        return self.backup
    def validate_service_backup(self, backup):
        self.calls.append(('validate',backup.name))
    def _service_state(self, name):
        return self.backup.state if self.backup else None
    def _run(self, args, **kwargs):
        self.calls.append(tuple(args))
        if args[1] == 'create':
            self.backup=ServiceBackup('zapret',args[4],'automatic','stopped','zapret','',{})
        elif args[1] == 'config':
            self.backup=replace(self.backup,image_path=args[4],start_mode='automatic')
    def _write_service_values(self,name,values):
        self.calls.append(('write', dict(values)))
        if self.backup:
            self.backup=replace(self.backup, adapter_fields={
                'strategy': values['zapret-discord-youtube'], 'owned': False})
    def start_service(self,name):
        self.calls.append(('start',name))
        if self.fail_start:raise RuntimeError('service start failed')
        self.backup=replace(self.backup,state='running')
    def stop_service(self,name):
        self.calls.append(('stop',name))
        self.backup=replace(self.backup,state='stopped')
    def delete_service(self,name):
        self.calls.append(('delete',name))
        self.backup=None
    def set_service_start_mode(self,name,mode):
        self.calls.append(('start_mode',mode))
        self.backup=replace(self.backup,start_mode=mode)
    def restore_service(self,backup):
        self.calls.append(('restore',backup))
        self.backup=backup


class FlowsealTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/'service.bat').write_text('@echo off\n')
        (self.root/'general (ALT11).bat').write_text('winws')
        self.exe=r'C:\Flowseal\bin\winws.exe'
        self.image=f'"{self.exe}" --wf-tcp=443'
        self.validator=lambda folder, strategy:(self.exe, '--wf-tcp=443')
    def get_manager(self, backup=None):
        system=FakeSCM(backup)
        return FlowsealServiceManager(system,validate=self.validator),system
    def old(self, *, strategy='general (ALT1)',image=None,active='running'):
        return ServiceBackup('zapret',image or self.image,'automatic',active,
                             'zapret','original',{'strategy':strategy})
    def test_creates_one_upstream_auto_service_and_marker(self):
        manager,system=self.get_manager()
        result=manager.apply(self.root,'general (ALT11).bat')
        self.assertEqual(result.action,'enabled')
        self.assertEqual(system.backup.start_mode,'automatic')
        self.assertEqual(system.backup.state,'running')
        self.assertTrue(any(c[0]=='write' and c[1]['zapret-discord-youtube']=='general (ALT11)'
                            for c in system.calls))
        self.assertFalse(any(c[0]=='delete' for c in system.calls))
    def test_reapplying_same_upstream_strategy_is_noop(self):
        old=self.old(strategy='general (ALT11)')
        manager,system=self.get_manager(old)
        result=manager.apply(self.root,'general (ALT11).bat')
        self.assertEqual(result.action,'enabled')
        self.assertFalse(any(c[0] in {'stop','start','delete','sc.exe','write'}
                             for c in system.calls))
        self.assertEqual(system.backup,old)

    def test_reconfigure_identified_service_in_place(self):
        manager,system=self.get_manager(self.old())
        manager.apply(self.root,'general (ALT11).bat')
        self.assertIn(('stop','zapret'),system.calls)
        self.assertTrue(any(c[0]=='sc.exe' and c[1]=='config' for c in system.calls))
        self.assertNotIn(('delete','zapret'),system.calls)
    def test_refuses_ambiguous_service_without_mutation(self):
        unknown=self.old(strategy='',image='"C:\\Unknown\\other.exe" --x')
        manager,system=self.get_manager(unknown)
        with self.assertRaises(FlowsealServiceError):
            manager.apply(self.root,'general (ALT11).bat')
        self.assertFalse(any(c[0] in ('stop','delete','write','sc.exe') for c in system.calls))
    def test_failed_activation_restores_previous_service(self):
        old=self.old()
        manager,system=self.get_manager(old)
        system.fail_start=True
        with self.assertRaisesRegex(FlowsealServiceError,'Не удалось включить'):
            manager.apply(self.root,'general (ALT11).bat')
        self.assertEqual(system.backup,old)
        self.assertTrue(any(c[0]=='restore' for c in system.calls))
    def test_stop_disables_autostart_without_deleting_files(self):
        manager,system=self.get_manager(self.old())
        manager.stop(self.root)
        self.assertEqual(system.backup.state,'stopped')
        self.assertEqual(system.backup.start_mode,'disabled')
        self.assertFalse(any(c[0]=='delete' for c in system.calls))
    def test_benchmark_restores_previous_service_even_on_failure(self):
        manager,system=self.get_manager(self.old())
        with patch('windows_zapret.WindowsZapretAdapter',return_value=system), \
             patch('benchmark.benchmark',side_effect=RuntimeError('cancelled')):
            with self.assertRaisesRegex(RuntimeError,'cancelled'):
                benchmark_with_service_restored(self.root,lambda *_:None,None)
        self.assertEqual(system.backup.state,'running')
        self.assertIn(('stop','zapret'),system.calls)
        self.assertIn(('start','zapret'),system.calls)


    def test_benchmark_explicitly_allows_verified_paused_service(self):
        manager,system=self.get_manager(self.old())
        with patch('windows_zapret.WindowsZapretAdapter',return_value=system), \
             patch('benchmark.benchmark',return_value={'best':None}) as mock_benchmark:
            benchmark_with_service_restored(self.root,lambda *_:None,None)
        self.assertTrue(mock_benchmark.call_args.kwargs['service_paused'])
        self.assertEqual(system.backup.state,'running')

if __name__=='__main__':unittest.main()
