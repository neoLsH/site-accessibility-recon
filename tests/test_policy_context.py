import unittest

import support
from reconlib.policy import ContextualBandit

ARMS = ("native", "cffi:chrome")


class RewardTest(unittest.TestCase):
    def test_degraded_is_half_reward(self):
        b = ContextualBandit(ARMS, algorithm="fixed", min_pulls=0)
        b.update("h", "article|content", "native", "degraded", cost_ms=100)
        arm = b.snapshot("h", "article|content")["arms"][0]
        self.assertEqual(arm["pulls"], 1)
        self.assertEqual(arm["successes"], 0.5)
        self.assertEqual(arm["failures"], 0.5)


class CostTest(unittest.TestCase):
    def test_cost_penalty_prefers_fast_arm(self):
        b = ContextualBandit(ARMS, algorithm="ucb1", min_pulls=0, cost_weight=1.0)
        for _ in range(4):
            b.update("h", "article|content", "native", "ok", cost_ms=50)
            b.update("h", "article|content", "cffi:chrome", "ok", cost_ms=5000)
        self.assertEqual(b.select("h", "article|content", ARMS), "native")

    def test_cost_weight_zero_disables_penalty(self):
        b = ContextualBandit(ARMS, algorithm="fixed", min_pulls=0, cost_weight=0)
        b.update("h", "article|content", "cffi:chrome", "ok", cost_ms=5000)
        self.assertEqual(b.select("h", "article|content", ARMS), "native")


class FallbackTest(unittest.TestCase):
    def test_unknown_falls_back_to_route_level(self):
        b = ContextualBandit(ARMS, algorithm="ucb1", min_pulls=1)
        for _ in range(2):
            b.update("h", "article|content", "cffi:chrome", "ok", cost_ms=10)
            b.update("h", "article|content", "native", "blocked", cost_ms=10)
        self.assertEqual(b.select("h", "article|unknown", ARMS), "cffi:chrome")
        self.assertEqual(b.last_level("h", "article|unknown"), "h|article|*")

    def test_global_fallback_when_no_context_samples(self):
        b = ContextualBandit(ARMS, algorithm="ucb1", min_pulls=0)
        b.update("h", "list|content", "native", "ok", cost_ms=10)
        self.assertEqual(b.select("h", "article|unknown", ARMS), "native")

    def test_same_seed_same_sequence(self):
        def run():
            b = ContextualBandit(ARMS, algorithm="thompson", seed=5, min_pulls=0)
            b.update("h", "article|content", "native", "ok", cost_ms=10)
            b.update("h", "article|content", "cffi:chrome", "blocked", cost_ms=10)
            return [b.select("h", "article|unknown", ARMS) for _ in range(5)]

        self.assertEqual(run(), run())


if __name__ == "__main__":
    unittest.main()
