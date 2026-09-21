import unittest
import transfer_interface as transfer


class TransferHarnessTests(unittest.TestCase):
    def test_reset_and_stimulus_width(self):
        tb = transfer.make_tb({'samples': 5, 'guard_cycles': 3})
        self.assertIn('reg [8:0] words[0:599];', tb)
        self.assertLess(tb.index('repeat(4)'), tb.index('out=$fopen'))
        words = transfer.transfer_words(1.0)
        self.assertEqual(len(words), 600)
        self.assertTrue(all(0 <= word < 512 for word in words))
        self.assertTrue(all((word >> 7) & 1 for word in words[:10]))
        self.assertEqual((words[12] >> 6) & 1, 1)
        self.assertEqual((words[12] >> 7) & 1, 0)

    def test_marker_can_be_disabled(self):
        words = transfer.transfer_words(1.0, marker=False)
        self.assertTrue(all((word >> 8) == 0 for word in words))
        active = transfer.transfer_words(1.0)
        self.assertEqual(sum((word >> 8) & 1 for word in active), 1)
