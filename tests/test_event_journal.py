import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from event_journal import EventJournal, safe_text


class EventJournalTests(unittest.TestCase):
    def test_persists_and_caps_history(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'history.json'
            journal = EventJournal(path, limit=3)
            for i in range(5):
                journal.add('NetPilot',f'Событие {i}',error=(i==4))
            self.assertEqual(len(journal.entries),3)
            loaded = EventJournal(path,limit=3)
            self.assertEqual(len(loaded.entries),3)
            self.assertIn('Ошибка',loaded.recent_text())
            self.assertIn('Событие 4',loaded.recent_text())
            self.assertNotIn('Событие 0',loaded.recent_text())

    def test_redacts_secrets_before_writing(self):
        text='token=helloworld secret:abcdef123 tg://proxy?server=127.0.0.1&secret=123abc'
        out=safe_text(text)
        self.assertNotIn('helloworld',out)
        self.assertNotIn('abcdef123',out)
        self.assertNotIn('123abc',out)

    def test_read_only_location_does_not_block(self):
        with tempfile.TemporaryDirectory() as folder:
            journal=EventJournal(Path(folder) / 'no-such-directory' / 'events.json')
            journal.add('NetPilot','Обычное событие')
            self.assertEqual(len(journal.entries),1)

if __name__=='__main__':unittest.main()
