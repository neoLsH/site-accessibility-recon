import json
import unittest

import support
from reconlib import simulate


class ParseRatesTest(unittest.TestCase):
    def test_parse(self):
        rates = simulate.parse_rates(" native=0.4 , cffi:chrome=0.75 ")
        self.assertEqual(rates, {"native": 0.4, "cffi:chrome": 0.75})

    def test_parse_rejects(self):
        for bad in ("native", "native=abc", "native=1.5", "native=-0.1", "=0.5", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                simulate.parse_rates(bad)


class RatesFromTrialsTest(unittest.TestCase):
    def test_counts_only_attributable(self):
        rows = [
            {"kind": "trial", "arm": "native", "trial_outcome": "ok"},
            {"kind": "trial", "arm": "native", "trial_outcome": "degraded"},
            {"kind": "trial", "arm": "native", "trial_outcome": "blocked"},
            {"kind": "trial", "arm": "native", "trial_outcome": "ok", "rate_limited": True},
            {"kind": "trial", "arm": "native", "trial_outcome": "noise"},
            {"kind": "trial", "arm": "native", "trial_outcome": "auth"},
            {"kind": "trial", "arm": "cffi:chrome", "trial_outcome": "ok"},
            {"route": "/a", "outcome": "ok"},
        ]
        rates = simulate.rates_from_trials(rows)
        self.assertAlmostEqual(rates["native"], 1 / 3)
        self.assertEqual(rates["cffi:chrome"], 1.0)

    def test_empty(self):
        self.assertEqual(simulate.rates_from_trials([]), {})


class SimulateTest(unittest.TestCase):
    def test_same_seed_reproducible(self):
        rates = {"native": 0.3, "cffi:chrome": 0.7}
        a = simulate.simulate("thompson", rates, pages=60, seed=7)
        b = simulate.simulate("thompson", rates, pages=60, seed=7)
        self.assertEqual(a, b)

    def test_thompson_favours_better_arm(self):
        out = simulate.simulate("thompson", {"native": 0.2, "cffi:chrome": 0.9},
                                pages=200, seed=7)
        self.assertGreater(out["pulls"]["cffi:chrome"], out["pulls"]["native"])
        self.assertGreater(out["access_rate"], 0.5)

    def test_fixed_tries_in_order(self):
        out = simulate.simulate("fixed", {"native": 0.5, "cffi:chrome": 1.0},
                                pages=40, seed=7)
        self.assertEqual(out["pulls"]["native"], 40)
        self.assertLessEqual(out["pulls"]["cffi:chrome"], 40)

    def test_certain_arm_needs_one_request_per_page(self):
        out = simulate.simulate("thompson", {"native": 1.0}, pages=25, seed=7)
        self.assertEqual(out["requests"], 25)
        self.assertEqual(out["successes"], 25)
        self.assertEqual(out["access_rate"], 1.0)

    def test_requests_bounded_by_pages_times_arms(self):
        out = simulate.simulate("ucb1", {"native": 0.1, "cffi:chrome": 0.2},
                                pages=30, seed=7)
        self.assertLessEqual(out["requests"], 30 * 2)
        self.assertEqual(sum(out["pulls"].values()), out["requests"])

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            simulate.simulate("thompson", {}, pages=10)
        with self.assertRaises(ValueError):
            simulate.simulate("thompson", {"native": 0.5}, pages=0)

    def test_best_single_is_expectation(self):
        out = simulate.best_single({"native": 0.2, "cffi:chrome": 0.8}, pages=50)
        self.assertEqual(out["arm"], "cffi:chrome")
        self.assertEqual(out["requests"], 50)
        self.assertEqual(out["expected_access_rate"], 0.8)
        self.assertIsNone(out["successes"])


if __name__ == "__main__":
    unittest.main()
