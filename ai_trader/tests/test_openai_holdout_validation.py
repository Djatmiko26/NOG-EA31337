import unittest

from openai_holdout_validation import split_holdout_indices


class OpenAIHoldoutValidationTests(unittest.TestCase):
    def test_split_is_chronological(self):
        values = list(range(100))
        dev, val = split_holdout_indices(values, 0.70)
        self.assertEqual(dev, list(range(70)))
        self.assertEqual(val, list(range(70, 100)))
        self.assertLess(max(dev), min(val))

    def test_split_keeps_both_segments_non_empty(self):
        dev, val = split_holdout_indices([10, 11], 0.90)
        self.assertEqual(dev, [10])
        self.assertEqual(val, [11])

    def test_empty_input(self):
        self.assertEqual(split_holdout_indices([], 0.70), ([], []))


if __name__ == "__main__":
    unittest.main()
