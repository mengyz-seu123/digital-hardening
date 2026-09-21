import unittest
from stress import crossing, metrics, sample, stimulus
class CoreTests(unittest.TestCase):
    def test_receiver(self):
        self.assertAlmostEqual(sample([[0.,3.3],[10e-9,0.]],5),1.65)
        self.assertTrue(all((w&512)==0 for w in stimulus(2.5,[[0.,3.3],[9e-6,3.3]])))
    def test_charge_crossing(self):
        self.assertAlmostEqual(crossing([[0.,2.],[2e-6,12.]],9.15),1430.)
    def test_successful_shutdown(self):
        ref=[[i,*([0x8000]*4)] for i in range(5)]
        rows=[[i,*([0x8000 if i<3 else 0x800]*4)] for i in range(5)]
        r=metrics(rows,ref,'baseline',0,20)
        self.assertEqual(r['shutdown_delay_ns'],10)
        self.assertFalse(r['false_trip'])
        self.assertFalse(r['late_shutdown'])
    def test_false_trip_not_success(self):
        ref=[[i,*([0x8000]*4)] for i in range(5)]
        rows=[[i,*([0x8000 if i<1 else 0x800]*4)] for i in range(5)]
        r=metrics(rows,ref,'baseline',0,30)
        self.assertTrue(r['false_trip'])
        self.assertGreater(r['lost_drive_cycles'],0)
if __name__=='__main__':
    unittest.main()