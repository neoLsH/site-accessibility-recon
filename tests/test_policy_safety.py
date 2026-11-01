import unittest

import support  # noqa: F401  (adds scripts/ to sys.path)
from reconlib import policy

ARMS = ("native", "cffi:chrome")
CTX = "article|challenge"


def bandit(block_budget=2, algorithm="ucb1"):
    return policy.ContextualBandit(ARMS, algorithm=algorithm, seed=3,
                                   min_pulls=1, prior_strength=0.0,
                                   block_budget=block_budget)


def row_of(snap, arm):
    return next(r for r in snap["arms"] if r["arm"] == arm)


class SuspensionTest(unittest.TestCase):
    def test_consecutive_blocks_suspend_the_arm(self):
        b = bandit()
        for _ in range(2):
            b.update("h.example", CTX, "native", "blocked")
        snap = b.snapshot("h.example", CTX)
        self.assertTrue(row_of(snap, "native")["suspended"])
        self.assertEqual(row_of(snap, "native")["block_streak"], 2)
        self.assertEqual(snap["suspended_arms"], ["native"])
        self.assertNotEqual(snap["decision"], "halt")
        for _ in range(20):
            self.assertEqual(b.select("h.example", CTX), "cffi:chrome")

    def test_success_resets_the_streak(self):
        b = bandit()
        b.update("h.example", CTX, "native", "blocked")
        b.update("h.example", CTX, "native", "ok")
        b.update("h.example", CTX, "native", "blocked")
        snap = b.snapshot("h.example", CTX)
        self.assertFalse(row_of(snap, "native")["suspended"])
        self.assertEqual(row_of(snap, "native")["block_streak"], 1)

    def test_non_attributable_outcomes_do_not_count(self):
        b = bandit()
        b.update("h.example", CTX, "native", "blocked", rate_limited=True)
        b.update("h.example", CTX, "native", "auth")
        b.update("h.example", CTX, "native", "missing")
        b.update("h.example", CTX, "native", "noise")
        snap = b.snapshot("h.example", CTX)
        self.assertFalse(row_of(snap, "native")["suspended"])
        self.assertEqual(row_of(snap, "native")["block_streak"], 0)

    def test_all_arms_suspended_returns_none(self):
        b = bandit()
        for arm in ARMS:
            for _ in range(2):
                b.update("h.example", CTX, arm, "blocked")
        self.assertIsNone(b.select("h.example", CTX))
        snap = b.snapshot("h.example", CTX)
        self.assertEqual(snap["decision"], "halt")
        self.assertIsNone(snap["best_arm"])
        self.assertEqual(snap["suspended_arms"], list(ARMS))
        self.assertTrue(all(r["suspended"] for r in snap["arms"]))

    def test_suspension_is_per_route(self):
        b = bandit()
        for _ in range(2):
            b.update("h.example", CTX, "native", "blocked")
        b.update("h.example", "list|content", "native", "ok")
        snap = b.snapshot("h.example", "article|content")
        self.assertTrue(row_of(snap, "native")["suspended"])
        self.assertFalse(row_of(b.snapshot("h.example", "list|content"),
                                "native")["suspended"])
        self.assertNotIn("native", b.suspended_arms("h.example", "list|content"))

    def test_suspension_is_per_host(self):
        b = bandit()
        for _ in range(2):
            b.update("a.example", CTX, "native", "blocked")
        b.update("b.example", CTX, "native", "ok")
        self.assertFalse(row_of(b.snapshot("b.example", CTX), "native")["suspended"])
        self.assertIn("native", b.suspended_arms("a.example", CTX))
        self.assertNotIn("native", b.suspended_arms("b.example", CTX))

    def test_fixed_and_round_robin_ignore_suspension(self):
        for algo in ("fixed", "round_robin"):
            b = bandit(algorithm=algo)
            for _ in range(2):
                b.update("h.example", CTX, "native", "blocked")
            picks = [b.select("h.example", CTX) for _ in range(2)]
            self.assertIn("native", picks)
            self.assertFalse(any(r["suspended"] for r in
                                 b.snapshot("h.example", CTX)["arms"]))

    def test_ucb_respects_suspension(self):
        b = bandit(algorithm="ucb1")
        for _ in range(2):
            b.update("h.example", CTX, "native", "blocked")
        for _ in range(4):
            self.assertEqual(b.select("h.example", CTX), "cffi:chrome")

    def test_budget_zero_disables_suspension(self):
        b = bandit(block_budget=0)
        for _ in range(10):
            b.update("h.example", CTX, "native", "blocked")
        snap = b.snapshot("h.example", CTX)
        self.assertFalse(row_of(snap, "native")["suspended"])
        self.assertEqual(snap["suspended_arms"], [])
        self.assertIsNotNone(b.select("h.example", CTX))

    def test_suspension_is_not_persisted(self):
        b = bandit()
        for _ in range(2):
            b.update("h.example", CTX, "native", "blocked")
        state = b.export_state()
        stat = state[f"h.example|{CTX}"]["arms"]["native"]
        self.assertEqual(len(stat), policy.STAT_LEN)
        again = bandit()
        again.import_state(state)
        snap = again.snapshot("h.example", CTX)
        self.assertFalse(row_of(snap, "native")["suspended"])
        self.assertEqual(row_of(snap, "native")["pulls"], 2)
        self.assertEqual(snap["suspended_arms"], [])


if __name__ == "__main__":
    unittest.main()
