import unittest

import support
from reconlib.policy import Bandit

ARMS = ("native", "cffi:chrome")


class ThompsonTest(unittest.TestCase):
    def test_same_seed_same_sequence(self):
        def run():
            b = Bandit(ARMS, algorithm="thompson", seed=7, min_pulls=0)
            b.update("h", "native", True)
            b.update("h", "cffi:chrome", False)
            return [b.select("h") for _ in range(10)]

        self.assertEqual(run(), run())

    def test_exploits_better_arm(self):
        b = Bandit(ARMS, algorithm="thompson", seed=3, min_pulls=0)
        for _ in range(18):
            b.update("h", "native", True)
        for _ in range(2):
            b.update("h", "native", False)
        for _ in range(2):
            b.update("h", "cffi:chrome", True)
        for _ in range(18):
            b.update("h", "cffi:chrome", False)
        picks = [b.select("h") for _ in range(60)]
        self.assertGreater(picks.count("native"), picks.count("cffi:chrome"))


class WarmupTest(unittest.TestCase):
    def test_each_arm_pulled_before_exploiting(self):
        b = Bandit(("a", "b", "c"), algorithm="thompson", seed=1, min_pulls=2)
        picks = []
        for _ in range(6):
            arm = b.select("h")
            picks.append(arm)
            b.update("h", arm, arm == "a")
        for arm in ("a", "b", "c"):
            self.assertGreaterEqual(picks.count(arm), 2)


class UcbTest(unittest.TestCase):
    def test_unpulled_arm_goes_first(self):
        b = Bandit(ARMS, algorithm="ucb1", min_pulls=1)
        self.assertEqual(b.select("h"), "native")
        b.update("h", "native", True)
        self.assertEqual(b.select("h"), "cffi:chrome")

    def test_ucb_prefers_success(self):
        b = Bandit(ARMS, algorithm="ucb1", seed=0, min_pulls=0)
        for _ in range(10):
            b.update("h", "native", True)
        for _ in range(10):
            b.update("h", "cffi:chrome", False)
        self.assertEqual(b.select("h"), "native")


class SimplePolicyTest(unittest.TestCase):
    def test_fixed_and_round_robin(self):
        fixed = Bandit(ARMS, algorithm="fixed")
        self.assertEqual({fixed.select("h") for _ in range(5)}, {"native"})
        rr = Bandit(ARMS, algorithm="round_robin")
        self.assertEqual([rr.select("h") for _ in range(4)],
                         ["native", "cffi:chrome", "native", "cffi:chrome"])

    def test_unknown_algorithm_is_usage_error(self):
        with self.assertRaises(ValueError):
            Bandit(ARMS, algorithm="magic")


class UpdateRuleTest(unittest.TestCase):
    def test_only_attributable_outcomes_update(self):
        b = Bandit(ARMS, min_pulls=0)
        self.assertFalse(b.update_from_outcome("h", "native", "noise"))
        self.assertFalse(b.update_from_outcome("h", "native", "auth"))
        self.assertFalse(b.update_from_outcome("h", "native", "missing"))
        self.assertFalse(b.update_from_outcome("h", "native", "blocked", rate_limited=True))
        self.assertTrue(b.update_from_outcome("h", "native", "ok"))
        self.assertTrue(b.update_from_outcome("h", "native", "degraded"))
        self.assertTrue(b.update_from_outcome("h", "native", "blocked"))
        arms = {a["arm"]: a for a in b.snapshot("h")["arms"]}
        self.assertEqual(arms["native"]["pulls"], 3)
        self.assertEqual(arms["native"]["successes"], 1)
        self.assertEqual(arms["native"]["failures"], 2)

    def test_state_is_per_host(self):
        b = Bandit(ARMS, min_pulls=0)
        b.update("a", "native", True)
        b.update("b", "cffi:chrome", False)
        a = {x["arm"]: x for x in b.snapshot("a")["arms"]}
        c = {x["arm"]: x for x in b.snapshot("b")["arms"]}
        self.assertEqual(a["native"]["pulls"], 1)
        self.assertEqual(c["native"]["pulls"], 0)
        self.assertEqual(c["cffi:chrome"]["pulls"], 1)

    def test_unknown_arm_raises(self):
        with self.assertRaises(ValueError):
            Bandit(ARMS).update("h", "browser", True)


if __name__ == "__main__":
    unittest.main()
