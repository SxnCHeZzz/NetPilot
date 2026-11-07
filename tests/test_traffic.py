"""Traffic accounting is passive, bounded and never makes internet requests."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from traffic import TrafficSampler,format_rate


class TrafficSamplerTests(unittest.TestCase):
    def test_computes_bytes_per_second_without_network_probes(self):
        counter=iter([(1000,800),(4072,1824),(5096,2048)])
        time=iter((10.0,12.0,14.0))
        sampler=TrafficSampler(lambda:SimpleNamespace(bytes_recv=(p:=next(counter))[0],bytes_sent=p[1]),lambda:next(time))
        self.assertIsNone(sampler.sample())
        reading=sampler.sample()
        self.assertEqual(reading.download_bps,1536)
        self.assertEqual(reading.upload_bps,512)
        self.assertEqual(reading.received_bytes,4072)

    def test_counter_resets_do_not_create_negative_speed_spikes(self):
        vals=iter([(1000,1000),(10,25)])
        times=iter((1.,2.))
        def fetch():
            recv,sent=next(vals)
            return SimpleNamespace(bytes_recv=recv,bytes_sent=sent)
        sampler=TrafficSampler(fetch,lambda:next(times))
        self.assertIsNone(sampler.sample())
        reading=sampler.sample()
        self.assertEqual((reading.download_bps,reading.upload_bps),(0,0))

    def test_units_and_reset(self):
        self.assertEqual(format_rate(2048),'2.0 КБ/с')
        self.assertEqual(format_rate(-15),'0.0 Б/с')
        a=TrafficSampler(lambda:SimpleNamespace(bytes_recv=0,bytes_sent=0))
        self.assertIsNone(a.sample());a.reset();self.assertIsNone(a.sample())


if __name__=='__main__': unittest.main()
