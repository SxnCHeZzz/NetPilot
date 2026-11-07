"""Focused regression checks for compact R8 layout and conservative Flowseal discovery."""
from pathlib import Path
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from zapret_inventory import ServiceRecord, build_snapshot, ProcessRecord


class FakeInventory:
    def __init__(self, services):self.services=services
    def iter_processes(self):return iter(())
    def iter_services(self):return iter(self.services)
    def iter_tasks(self):return iter(())


class FlowsealDiscoveryTests(unittest.TestCase):
    def test_flowseal_named_service_with_real_signature_is_confirmed(self):
        command=(r'"C:\Flowseal\bin\winws.exe" '
                 '--wf-tcp=80,443 --dpi-desync=fake')
        snapshot=build_snapshot(FakeInventory([ServiceRecord('zapret',command,'running',111)]), ())
        self.assertEqual(snapshot.active_count,1)
        self.assertEqual(snapshot.active[0].origin,'external')
        self.assertIn('zapret-service-signature',snapshot.active[0].evidence)

    def test_name_only_is_not_confirmed(self):
        snapshot=build_snapshot(FakeInventory([ServiceRecord('zapret',r'C:\Other\winws.exe','running',111)]), ())
        self.assertEqual(len(snapshot.active),0)
        self.assertEqual(len(snapshot.ambiguous),1)

    def test_other_named_service_with_same_flags_is_not_automatically_trusted(self):
        command=r'"C:\Other\bin\winws.exe" --wf-tcp=443 --dpi-desync=fake'
        snapshot=build_snapshot(FakeInventory([ServiceRecord('random',command,'running',111)]), ())
        self.assertEqual(len(snapshot.active),0)
        self.assertEqual(len(snapshot.ambiguous),1)

    def test_non_winws_named_zapret_service_is_not_classified(self):
        snapshot=build_snapshot(FakeInventory([ServiceRecord('zapret',r'C:\Other\random.exe --wf-tcp=443 --dpi-desync=fake','running')]), ())
        self.assertFalse(snapshot.items)


try:
    from PySide6.QtWidgets import QApplication
    import app as ui
    HAS_QT=True
except ImportError:
    HAS_QT=False


@unittest.skipUnless(HAS_QT,'Qt required')
class CompactUIR8Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt=QApplication.instance() or QApplication([])
        cls.qt.setStyleSheet(ui.CSS)

    def setUp(self):
        with patch.object(ui.NetPilot, 'refresh_zapret_inventory', lambda self:None), \
             patch.object(ui.NetPilot, 'on_first_run', lambda *args:None):
            self.window=ui.NetPilot(smoke_mode=True)
        self.window.window_transition.reduced_motion=True
        self.window.show();self.qt.processEvents()

    def tearDown(self):
        for key in ('status_timer','daily_timer','auto_strategy_timer','message_timer','traffic_timer'):
            getattr(self.window,key).stop()
        self.window.window_transition.finish_immediately()
        self.window.tray.hide();self.window.hide()
        self.window.pool.shutdown(wait=False,cancel_futures=True)
        self.window.deleteLater();self.qt.processEvents()

    def test_same_compact_geometry_for_all_components(self):
        for tab in range(3):
            self.window.select_detail(tab)
            self.qt.processEvents()
            self.assertEqual((self.window.width(),self.window.height()),(400,354))
            self.assertEqual(self.window.detail_stack.height(),184)

    def test_stop_button_is_only_in_settings_components(self):
        self.assertFalse(self.window.stop_all_zapret_button.isVisible())
        self.window.show_components()
        self.assertEqual(self.window.settings_sections.currentIndex(),1)
        self.assertTrue(any(button.text()=='Остановить весь Zapret'
                            for button in self.window.findChildren(ui.QPushButton)))

    def test_sidebar_does_not_fill_settings_height(self):
        self.window.show_components()
        self.qt.processEvents()
        sidebar=next(w for w in self.window.findChildren(ui.QFrame) if w.objectName()=='settingsSidebar')
        self.assertLess(sidebar.height(),self.window.height()/2)

if __name__=='__main__':unittest.main()
