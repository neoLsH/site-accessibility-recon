import unittest

import support
from reconlib.pacing import Pacing, default_delay, retry_after


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += seconds


def make(**kw):
    clock = FakeClock()
    return Pacing(clock=clock.now, sleep=clock.sleep, **kw), clock


class PacingTest(unittest.TestCase):
    def test_first_request_does_not_wait(self):
        p, c = make(delay=5)
        self.assertEqual(p.wait("a.example"), 0)
        self.assertEqual(c.slept, [])

    def test_per_host_delay(self):
        p, c = make(delay=5)
        p.wait("a.example")
        c.t += 1
        self.assertAlmostEqual(p.wait("a.example"), 4)
        self.assertEqual(p.wait("b.example"), 0)
        self.assertEqual(c.slept, [4])

    def test_retry_after_extends_next_gap(self):
        p, c = make(delay=5)
        p.wait("a.example")
        p.observe("a.example", "blocked", rate_limited=True, retry_after=10)
        c.t += 2
        self.assertAlmostEqual(p.wait("a.example"), 13)
        self.assertEqual(c.slept, [13])

    def test_circuit_breaker_aborts_after_three_blocks(self):
        p, _ = make(max_consecutive_blocked=3)
        for _ in range(2):
            self.assertFalse(p.observe("h", "blocked")["aborted"])
        state = p.observe("h", "blocked")
        self.assertTrue(state["aborted"])
        self.assertTrue(p.aborted("h"))
        self.assertIn("blocked", state["reason"])

    def test_ok_resets_breaker(self):
        p, _ = make(max_consecutive_blocked=2)
        p.observe("h", "blocked")
        p.observe("h", "ok")
        self.assertFalse(p.observe("h", "blocked")["aborted"])

    def test_noise_backs_off_exponentially_without_aborting(self):
        p, _ = make(backoff_base=2, max_backoff=5)
        self.assertEqual(p.observe("h", "noise")["backoff"], 2)
        self.assertEqual(p.observe("h", "noise")["backoff"], 4)
        state = p.observe("h", "noise")
        self.assertEqual(state["backoff"], 5)
        self.assertFalse(state["aborted"])

    def test_retry_after_is_capped(self):
        p, _ = make(max_backoff=30)
        self.assertEqual(p.observe("h", "blocked", rate_limited=True,
                                   retry_after=999)["backoff"], 30)

    def test_rate_limit_budget_opens_host_cooldown(self):
        p, _ = make(rate_limit_budget=1, rate_limit_cooldown=30)
        state = p.observe("h", "blocked", rate_limited=True, retry_after=10)
        self.assertEqual(state["rate_limit_streak"], 1)
        self.assertEqual(state["rate_limit_total"], 1)
        self.assertEqual(state["cooldown"], 30)
        self.assertAlmostEqual(p.cooldown_left("h"), 30)

    def test_cooldown_prefers_longer_retry_after(self):
        p, _ = make(rate_limit_budget=1, rate_limit_cooldown=10)
        state = p.observe("h", "blocked", rate_limited=True, retry_after=45)
        self.assertEqual(state["cooldown"], 45)

    def test_default_budget_needs_two_rate_limits(self):
        p, c = make(rate_limit_cooldown=30)
        first = p.observe("h", "blocked", rate_limited=True, retry_after=5)
        self.assertEqual(first["cooldown"], 0)
        c.t += 1
        second = p.observe("h", "blocked", rate_limited=True, retry_after=5)
        self.assertEqual(second["rate_limit_streak"], 2)
        self.assertGreater(second["cooldown"], 0)

    def test_budget_zero_disables_cooldown_but_counts(self):
        p, _ = make(rate_limit_budget=0, rate_limit_cooldown=30,
                    max_consecutive_blocked=99)
        for _ in range(5):
            state = p.observe("h", "blocked", rate_limited=True)
        self.assertEqual(state["rate_limit_total"], 5)
        self.assertEqual(state["cooldown"], 0)
        self.assertEqual(p.cooldown_left("h"), 0)

    def test_non_rate_limit_outcome_resets_streak(self):
        p, _ = make(rate_limit_budget=2, rate_limit_cooldown=30)
        p.observe("h", "blocked", rate_limited=True)
        p.observe("h", "noise")
        state = p.observe("h", "blocked", rate_limited=True)
        self.assertEqual(state["rate_limit_streak"], 1)
        self.assertEqual(state["cooldown"], 0)

    def test_wait_honors_cooldown(self):
        p, c = make(delay=5, rate_limit_budget=1, rate_limit_cooldown=30)
        p.wait("h")
        p.observe("h", "blocked", rate_limited=True, retry_after=30)
        self.assertAlmostEqual(p.wait("h"), 30)
        self.assertAlmostEqual(c.slept[0], 30)
        self.assertEqual(p.cooldown_left("h"), 0)

    def test_cooldown_is_per_host(self):
        p, _ = make(rate_limit_budget=1, rate_limit_cooldown=30)
        p.observe("a.example", "blocked", rate_limited=True)
        self.assertGreater(p.cooldown_left("a.example"), 0)
        self.assertEqual(p.cooldown_left("b.example"), 0)

    def test_cooldown_expires_with_clock(self):
        p, c = make(rate_limit_budget=1, rate_limit_cooldown=30)
        p.observe("h", "blocked", rate_limited=True)
        c.t += 31
        self.assertEqual(p.cooldown_left("h"), 0)

    def test_default_delay_for_loopback(self):
        self.assertEqual(default_delay("127.0.0.1"), 0)
        self.assertEqual(default_delay("localhost"), 0)
        self.assertEqual(default_delay("example.com"), 5)


class RetryAfterTest(unittest.TestCase):
    def test_seconds(self):
        self.assertEqual(retry_after("Retry-After: 30"), 30)
        self.assertEqual(retry_after("content-type: text/html\nretry-after: 7"), 7)

    def test_invalid_returns_none(self):
        self.assertIsNone(retry_after(""))
        self.assertIsNone(retry_after("Retry-After: soon"))
        self.assertIsNone(retry_after("Content-Type: text/html"))

    def test_negative_is_clamped_to_zero(self):
        self.assertEqual(retry_after("Retry-After: 0"), 0)


if __name__ == "__main__":
    unittest.main()
