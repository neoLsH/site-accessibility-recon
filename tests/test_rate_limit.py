"""限频联合决策：host 冷却、跳过请求、选臂统计隔离。"""

import unittest

import support
from reconlib.session import ArmSession, host_of

BASE = ("--selector", "#content", "--arms", "native", "--delay", "0",
        "--timeout", "10", "--json")


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += seconds


def wall(handler, seconds=30):
    support.response(handler, 429, "slow down",
                     headers=[("Retry-After", str(seconds))])


def session_for(clock, **kw):
    kw.setdefault("policy", "fixed")
    kw.setdefault("delay", 0)
    kw.setdefault("permission", True)
    kw.setdefault("cookies", False)
    return ArmSession(["native"], clock=clock.now, sleep=clock.sleep, **kw)


class CooldownSessionTest(unittest.TestCase):
    def test_rate_limit_budget_skips_next_probe_without_request(self):
        hits = {"n": 0}

        def counted(h):
            hits["n"] += 1
            wall(h)

        clock = FakeClock()
        with support.serve({"/rl": counted}) as base:
            session = session_for(clock, rate_limit_budget=1,
                                  rate_limit_cooldown=30)
            try:
                first = session.probe(base + "/rl", selector="#content")
                self.assertEqual(first["evidence"]["outcome"], "blocked")
                self.assertTrue(first["tries"][0]["rate_limited"])
                self.assertIsNone(first["halt"])
                second = session.probe(base + "/rl", selector="#content")
            finally:
                session.close()
        self.assertEqual(hits["n"], 1)
        self.assertEqual(second["halt"], "rate-limit-cooldown")
        self.assertEqual(second["stopped"], "rate-limit-cooldown")
        self.assertTrue(second["skipped"].startswith("rate-limit-cooldown"))
        self.assertEqual(second["requests"], 0)
        self.assertEqual(second["tries"], [])
        self.assertEqual(second["robots"], "skipped")
        self.assertGreater(second["cooldown_left"], 0)

    def test_cooldown_expires_with_clock(self):
        hits = {"n": 0}

        def counted(h):
            hits["n"] += 1
            wall(h)

        clock = FakeClock()
        with support.serve({"/rl": counted}) as base:
            session = session_for(clock, rate_limit_budget=1,
                                  rate_limit_cooldown=30)
            try:
                session.probe(base + "/rl", selector="#content")
                clock.t += 31
                page = session.probe(base + "/rl", selector="#content")
            finally:
                session.close()
        self.assertEqual(hits["n"], 2)
        self.assertIsNone(page["halt"])
        self.assertEqual(page["evidence"]["outcome"], "blocked")

    def test_default_budget_allows_first_rate_limit(self):
        hits = {"n": 0}

        def counted(h):
            hits["n"] += 1
            wall(h)

        clock = FakeClock()
        with support.serve({"/rl": counted}) as base:
            session = session_for(clock, rate_limit_cooldown=30)
            try:
                first = session.probe(base + "/rl", selector="#content")
                self.assertEqual(
                    session.pace.cooldown_left(host_of(base + "/rl")), 0)
                second = session.probe(base + "/rl", selector="#content")
            finally:
                session.close()
        self.assertEqual(hits["n"], 2)
        self.assertIsNone(first["halt"])
        self.assertIsNone(second["halt"])

    def test_rate_limited_attempt_does_not_pull_arm(self):
        clock = FakeClock()
        with support.serve({"/rl": wall}) as base:
            session = session_for(clock, rate_limit_budget=5)
            try:
                session.probe(base + "/rl", selector="#content")
                rows = session.snapshot(host_of(base + "/rl"))["arms"]
            finally:
                session.close()
        self.assertEqual(sum(row["pulls"] for row in rows), 0)

    def test_cooldown_is_scoped_to_one_host(self):
        clock = FakeClock()
        with support.serve({"/rl": wall}) as wall_base, \
                support.serve({"/ok": lambda h: support.response(
                    h, 200, support.long_html())}) as ok_base:
            session = session_for(clock, rate_limit_budget=1,
                                  rate_limit_cooldown=30)
            try:
                session.probe(wall_base + "/rl", selector="#content")
                page = session.probe(ok_base + "/ok", selector="#content")
            finally:
                session.close()
        self.assertEqual(page["evidence"]["outcome"], "ok")


class CooldownCliTest(unittest.TestCase):
    def test_crawl_reports_cooldown_and_stops_early(self):
        hits = {"n": 0}

        def counted(h):
            hits["n"] += 1
            wall(h)

        with support.serve({"/rl": counted}) as base:
            r, data = support.run_json(
                "crawl", base + "/rl", base + "/rl", *BASE,
                "--rate-limit-budget", "1", "--rate-limit-cooldown", "30")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(hits["n"], 1)
        pages = data["pages"]
        self.assertEqual(pages[0]["evidence"]["outcome"], "blocked")
        self.assertEqual(pages[1]["halt"], "rate-limit-cooldown")
        summary = data["summary"]
        self.assertEqual(summary["requests"], 1)
        self.assertEqual(summary["cooldown_pages"], 1)
        self.assertEqual(summary["rate_limited_pages"], 1)
        self.assertEqual(summary["rate_limit_budget"], 1)
        self.assertIn("rate-limit-cooldown", summary["stopped"])

    def test_crawl_default_budget_keeps_crawling(self):
        with support.serve({"/rl": lambda h: wall(h, seconds=1)}) as base:
            r, data = support.run_json(
                "crawl", base + "/rl", base + "/rl", *BASE,
                "--rate-limit-cooldown", "30")
        self.assertEqual(r.returncode, 0, r.stderr)
        summary = data["summary"]
        self.assertEqual(summary["requests"], 2)
        self.assertEqual(summary["cooldown_pages"], 0)
        self.assertEqual(summary["rate_limited_pages"], 2)

    def test_fetch_marks_cooldown_verdict(self):
        with support.serve({"/rl": wall}) as base:
            r, data = support.run_json(
                "fetch", base + "/rl", base + "/rl", *BASE,
                "--rate-limit-budget", "1", "--rate-limit-cooldown", "30")
        self.assertEqual(r.returncode, 1)
        records = data["records"]
        self.assertEqual(records[0]["verdict"], "未拿到内容")
        self.assertEqual(records[1]["verdict"], "限频冷却")
        self.assertTrue(records[1]["evidence"][0].startswith("rate-limit-cooldown"))
        self.assertEqual(records[1]["rungs"], [])

    def test_quick_marks_cooldown_as_pending(self):
        with support.serve({"/rl": wall}) as base:
            r, data = support.run_json(
                "quick", base + "/rl", base + "/rl", *BASE,
                "--rate-limit-budget", "1", "--rate-limit-cooldown", "30")
        self.assertEqual(r.returncode, 1)
        rows = data["rows"]
        self.assertEqual(rows[0]["verdict"], "疑似拦截")
        self.assertEqual(rows[1]["verdict"], "待定")
        self.assertIn("rate-limit-cooldown", rows[1]["reason"])

    def test_matrix_stops_on_cooldown(self):
        with support.serve({"/rl": wall}) as base:
            r = support.run(
                "matrix", base + "/rl", "--arms", "native",
                "--selector", "#content", "--passes", "2", "--delay", "0",
                "--rate-limit-budget", "1", "--rate-limit-cooldown", "30")
        self.assertEqual(r.returncode, 1)
        self.assertIn("rate-limit-cooldown", r.stderr)


if __name__ == "__main__":
    unittest.main()
