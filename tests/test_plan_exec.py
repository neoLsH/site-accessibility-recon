"""探索计划执行闭环：配额聚合、会话过滤、crawl --plan。"""

import json
import os
import unittest

import support
from reconlib import explore
from reconlib.session import ArmSession

BASE = ("--selector", "#content", "--delay", "0", "--timeout", "10")


def ok(handler):
    support.response(handler, 200, support.long_html())


def plan_body(steps, schema="recon-explore/1"):
    return {"schema": schema, "steps": steps}


def step(context, arm, requests, kind="unseen"):
    return {"context": context, "arm": arm, "kind": kind,
            "requests": requests, "why": "t"}


def session_for(plan, arms=("native",), permission=True):
    return ArmSession(list(arms), policy="fixed", delay=0, permission=permission,
                      cookies=False, plan=plan)


class QuotaTest(unittest.TestCase):
    def test_aggregates_by_host_route_and_arm(self):
        quota = explore.Quota(plan_body([
            step("a.test|/p|ok", "native", 2),
            step("a.test|/p|blocked", "native", 1),
            step("a.test|/p|unknown", "cffi:chrome", 3),
            step("b.test|/p|ok", "native", 1),
        ]))
        self.assertEqual(quota.planned, 7)
        self.assertEqual(quota.left("a.test", "/p", "native"), 3)
        self.assertEqual(quota.left("a.test", "/p", "cffi:chrome"), 3)
        self.assertEqual(quota.left("b.test", "/p", "native"), 1)
        self.assertEqual(quota.left("a.test", "/p", "browser:chromium"), 0)
        self.assertEqual(quota.arms("a.test", "/p"), {"native", "cffi:chrome"})
        self.assertEqual(quota.arms("c.test", "/p"), set())

    def test_consume_stops_at_zero_and_snapshot_tracks(self):
        quota = explore.Quota(plan_body([step("a.test|/|ok", "native", 1)]))
        self.assertTrue(quota.consume("a.test", "/", "native"))
        self.assertFalse(quota.consume("a.test", "/", "native"))
        snap = quota.snapshot()
        self.assertEqual(snap["planned"], 1)
        self.assertEqual(snap["used"], 1)
        self.assertEqual(snap["remaining"], 0)
        self.assertEqual(snap["routes"], 1)

    def test_rejects_malformed_plans(self):
        bad = [
            [],
            {},
            {"schema": "recon-policy/1", "steps": []},
            {"schema": "recon-explore/1"},
            {"schema": "recon-explore/1", "steps": "x"},
        ]
        for body in bad:
            with self.assertRaises(ValueError):
                explore.Quota(body)

    def test_ignores_invalid_steps(self):
        quota = explore.Quota(plan_body([
            {"context": "a|/p|ok", "arm": "", "requests": 2},
            {"context": "bad", "arm": "native", "requests": 2},
            {"context": "a.test|/p|ok", "arm": "native", "requests": 0},
        ]))
        self.assertEqual(quota.planned, 0)
        self.assertEqual(quota.arms("a.test", "/p"), set())


class PlanSessionTest(unittest.TestCase):
    def test_quota_is_consumed_and_second_page_skips_without_request(self):
        hits = {"n": 0}

        def counted(h):
            hits["n"] += 1
            ok(h)

        with support.serve({"/p": counted}) as base:
            host = base.split("//", 1)[1]
            quota = explore.Quota(plan_body([
                step(f"{host}|other|content", "native", 1)]))
            session = session_for(quota)
            try:
                first = session.probe(base + "/p", selector="#content")
                second = session.probe(base + "/p", selector="#content")
            finally:
                session.close()
        self.assertEqual(first["evidence"]["outcome"], "ok")
        self.assertEqual(hits["n"], 1)
        self.assertEqual(second["stopped"], "plan-exhausted")
        self.assertEqual(second["skipped"], "plan-exhausted")
        self.assertEqual(second["requests"], 0)
        self.assertEqual(second["tries"], [])

    def test_unplanned_route_sends_no_request_including_robots(self):
        hits = {"n": 0}

        def counted(h):
            hits["n"] += 1
            if h.path == "/robots.txt":
                support.response(h, 200, "User-agent: *\nDisallow: /\n",
                                 ctype="text/plain")
                return
            ok(h)

        with support.serve({"/p": counted, "/robots.txt": counted}) as base:
            host = base.split("//", 1)[1]
            quota = explore.Quota(plan_body([
                step(f"{host}|api|content", "native", 2)]))
            session = session_for(quota, permission=False)
            try:
                page = session.probe(base + "/p", selector="#content")
            finally:
                session.close()
        self.assertEqual(hits["n"], 0)
        self.assertEqual(page["stopped"], "plan-exhausted")
        self.assertEqual(page["requests"], 0)
        self.assertIsNone(page["robots"])

    def test_planned_arm_missing_from_candidates_is_plan_exhausted(self):
        hits = {"n": 0}

        def counted(h):
            hits["n"] += 1
            ok(h)

        with support.serve({"/p": counted}) as base:
            host = base.split("//", 1)[1]
            quota = explore.Quota(plan_body([
                step(f"{host}|other|content", "cffi:chrome", 2)]))
            session = session_for(quota)
            try:
                page = session.probe(base + "/p", selector="#content")
            finally:
                session.close()
        self.assertEqual(page["stopped"], "plan-exhausted")
        self.assertEqual(page["requests"], 0)
        self.assertEqual(page["tries"], [])


class CrawlPlanTest(unittest.TestCase):
    def test_crawl_plan_limits_requests_and_reports(self):
        hits = {"n": 0}

        def counted(h):
            hits["n"] += 1
            ok(h)

        with support.serve({"/p": counted}) as base:
            host = base.split("//", 1)[1]
            plan = support.write_temp(json.dumps(plan_body([
                step(f"{host}|other|content", "native", 1)])), suffix=".json")
            r, data = support.run_json(
                "crawl", base + "/p", base + "/p", *BASE, "--json",
                "--arms", "native", "--i-have-permission",
                "--plan", plan, "--evidence-out", support.temp_path(".jsonl"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(hits["n"], 1)
        summary = data["summary"]
        self.assertEqual(summary["requests"], 1)
        self.assertEqual(summary["plan_exhausted_pages"], 1)
        self.assertEqual(summary["plan"]["planned"], 1)
        self.assertEqual(summary["plan"]["remaining"], 0)
        self.assertIsNone(summary["stopped"])

    def test_crawl_rejects_bad_plan(self):
        plan = support.write_temp(json.dumps({"schema": "recon-policy/1"}),
                                  suffix=".json")
        with support.serve({"/p": ok}):
            r = support.run("crawl", "http://127.0.0.1:9/p", *BASE,
                            "--arms", "native", "--i-have-permission",
                            "--plan", plan)
        self.assertEqual(r.returncode, 2)
        self.assertIn("计划", r.stderr)

    def test_explore_plan_out_round_trips(self):
        out = support.temp_path(".json")
        r = support.run(
            "explore", "--trials",
            os.path.join(support.ROOT, "assets", "example", "trials.jsonl"),
            "--plan-out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(out, encoding="utf-8") as f:
            body = json.load(f)
        self.assertEqual(body["schema"], "recon-explore/1")
        quota = explore.Quota(body)
        self.assertEqual(quota.planned, body["allocated"])
        self.assertGreater(quota.planned, 0)


if __name__ == "__main__":
    unittest.main()
