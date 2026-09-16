import unittest

from historical_replay import replay_decision_indices, select_evenly_spaced_indices


class HistoricalReplayTests(unittest.TestCase):
    def test_replay_indices_reserve_warmup_and_future_bars(self):
        indices = replay_decision_indices(
            total_bars=100,
            warmup_bars=20,
            max_horizon=10,
        )

        self.assertEqual(indices[0], 20)
        self.assertEqual(indices[-1], 89)
        self.assertEqual(len(indices), 70)

        for i in indices:
            self.assertGreaterEqual(i, 20)
            self.assertLessEqual(i + 10, 99)

    def test_replay_indices_empty_when_history_is_too_short(self):
        self.assertEqual(
            replay_decision_indices(
                total_bars=30,
                warmup_bars=20,
                max_horizon=10,
            ),
            [],
        )

    def test_even_sampling_spans_full_candidate_period(self):
        values = list(range(100, 200))
        selected = select_evenly_spaced_indices(values, 5)

        self.assertEqual(len(selected), 5)
        self.assertEqual(selected[0], 100)
        self.assertEqual(selected[-1], 199)
        self.assertEqual(selected, sorted(selected))
        self.assertEqual(len(set(selected)), len(selected))

    def test_sampling_never_exceeds_hard_cap(self):
        values = list(range(500))
        selected = select_evenly_spaced_indices(values, 20)

        self.assertLessEqual(len(selected), 20)
        self.assertTrue(all(value in values for value in selected))


if __name__ == "__main__":
    unittest.main()
