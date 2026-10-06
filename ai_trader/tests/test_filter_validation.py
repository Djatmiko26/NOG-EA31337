import unittest

from filter_validation import chronological_split, summarize


class FilterValidationTests(unittest.TestCase):
    def test_chronological_split_preserves_order(self):
        indices = list(range(100, 200))
        development, validation = chronological_split(indices, 0.70)

        self.assertEqual(len(development), 70)
        self.assertEqual(len(validation), 30)
        self.assertEqual(development[0], 100)
        self.assertEqual(development[-1], 169)
        self.assertEqual(validation[0], 170)
        self.assertEqual(validation[-1], 199)
        self.assertLess(development[-1], validation[0])

    def test_chronological_split_empty(self):
        self.assertEqual(chronological_split([], 0.70), ([], []))

    def test_summary_uses_directional_values(self):
        result = summarize([1.0, -0.5, 0.5, 0.0])

        self.assertEqual(result["n"], 4)
        self.assertAlmostEqual(result["mean"], 0.25)
        self.assertAlmostEqual(result["win_rate"], 50.0)


if __name__ == "__main__":
    unittest.main()
