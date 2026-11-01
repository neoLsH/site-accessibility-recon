import unittest

import support
from reconlib.policy import ContextualBandit

ARMS = ("native", "cffi:chrome")


class PriorTest(unittest.TestCase):
    def test_sibling_context_pulls_cold_start(self):
        b = ContextualBandit(ARMS, algorithm="ucb1", min_pulls=1,
                             prior_strength=4.0)
        for _ in range(6):
            b.update("h", "list|content", "native", "blocked", cost_ms=10)
            b.update("h", "list|content", "cffi:chrome", "ok", cost_ms=10)
        b.update("h", "article|content", "native", "ok", cost_ms=10)
        b.update("h", "article|content", "cffi:chrome", "blocked", cost_ms=10)
        self.assertEqual(b.select("h", "article|content", ARMS), "cffi:chrome")

    def test_prior_strength_zero_keeps_local_evidence(self):
        b = ContextualBandit(ARMS, algorithm="ucb1", min_pulls=1,
                             prior_strength=0)
        for _ in range(6):
            b.update("h", "list|content", "native", "blocked", cost_ms=10)
            b.update("h", "list|content", "cffi:chrome", "ok", cost_ms=10)
        b.update("h", "article|content", "native", "ok", cost_ms=10)
        b.update("h", "article|content", "cffi:chrome", "blocked", cost_ms=10)
        self.assertEqual(b.select("h", "article|content", ARMS), "native")

    def test_prior_is_visible_without_local_pulls(self):
        b = ContextualBandit(ARMS, algorithm="ucb1", min_pulls=1,
                             prior_strength=4.0)
        for _ in range(6):
            b.update("h", "list|content", "native", "blocked", cost_ms=10)
            b.update("h", "list|content", "cffi:chrome", "ok", cost_ms=10)
        self.assertEqual(b.select("h", "article|content", ARMS), "cffi:chrome")
        row = b.snapshot("h", "article|content")["arms"][0]
        self.assertEqual(row["pulls"], 0)
        self.assertGreater(row["n_eff"], 0)


class ForgettingTest(unittest.TestCase):
    def test_half_life_weights_recent_outcomes(self):
        fresh = ContextualBandit(("native",), algorithm="fixed", min_pulls=0,
                                 half_life=10)
        stale = ContextualBandit(("native",), algorithm="fixed", min_pulls=0)
        for bandit in (fresh, stale):
            for _ in range(20):
                bandit.update("h", "article|content", "native", "blocked",
                              cost_ms=10)
            for _ in range(20):
                bandit.update("h", "article|content", "native", "ok",
                              cost_ms=10)
        self.assertGreater(fresh.snapshot("h", "article|content")["arms"][0]["mean"],
                           0.6)
        self.assertAlmostEqual(
            stale.snapshot("h", "article|content")["arms"][0]["mean"], 0.5)

    def test_half_life_zero_keeps_all_history(self):
        b = ContextualBandit(("native",), algorithm="fixed", min_pulls=0,
                             half_life=0)
        b.update("h", "article|content", "native", "ok", cost_ms=10)
        row = b.snapshot("h", "article|content")["arms"][0]
        self.assertEqual(row["pulls"], 1)
        self.assertEqual(row["mean"], 1.0)


class DiagnosticsTest(unittest.TestCase):
    def test_snapshot_is_deterministic(self):
        b = ContextualBandit(ARMS, algorithm="thompson", seed=3, min_pulls=0)
        for _ in range(4):
            b.update("h", "article|content", "native", "ok", cost_ms=10)
            b.update("h", "article|content", "cffi:chrome", "blocked",
                     cost_ms=10)
        first = b.snapshot("h", "article|content")
        second = b.snapshot("h", "article|content")
        self.assertEqual(first, second)
        self.assertEqual(first["best_arm"], "native")
        self.assertGreater(first["confidence"], 0.8)
        self.assertEqual(first["decision"], "exploit")

    def test_uncertain_split_recommends_explore(self):
        b = ContextualBandit(ARMS, algorithm="ucb1", min_pulls=0,
                             prior_strength=0)
        b.update("h", "article|content", "native", "ok", cost_ms=10)
        b.update("h", "article|content", "cffi:chrome", "blocked", cost_ms=10)
        snap = b.snapshot("h", "article|content")
        self.assertEqual(snap["decision"], "explore")
        best = max(snap["arms"], key=lambda r: r["utility"])
        self.assertLess(best["n_eff"], snap["min_effective_pulls"])
        for row in snap["arms"]:
            self.assertLessEqual(row["ci95"][0], row["ci95"][1])
            self.assertTrue(0.0 <= row["p_best"] <= 1.0)

    def test_cost_penalty_enters_utility(self):
        b = ContextualBandit(ARMS, algorithm="ucb1", min_pulls=0,
                             prior_strength=0, cost_weight=1.0)
        for _ in range(4):
            b.update("h", "article|content", "native", "ok", cost_ms=50)
            b.update("h", "article|content", "cffi:chrome", "ok", cost_ms=5000)
        rows = {r["arm"]: r for r in b.snapshot("h", "article|content")["arms"]}
        self.assertGreater(rows["native"]["utility"], rows["cffi:chrome"]["utility"])


class StateTest(unittest.TestCase):
    def test_export_import_roundtrip(self):
        b = ContextualBandit(ARMS, algorithm="ucb1", min_pulls=0,
                             prior_strength=2)
        b.update("h", "article|content", "native", "ok", cost_ms=12)
        b.update("h", "article|content", "cffi:chrome", "blocked", cost_ms=80)
        state = b.export_state()
        other = ContextualBandit(ARMS, algorithm="ucb1", min_pulls=0,
                                 prior_strength=2)
        other.import_state(state)
        self.assertEqual(other.snapshot("h", "article|content")["arms"],
                         b.snapshot("h", "article|content")["arms"])

    def test_import_rejects_bad_state(self):
        b = ContextualBandit(ARMS)
        for bad in ({"h|article": {"arms": {}}},
                    {"h|article|content": {"arms": {"native": [1, 2]}}},
                    {"h|article|content": {"arms": {"native": [1, -1, 0, 0, 0]}}}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                b.import_state(bad)


if __name__ == "__main__":
    unittest.main()
