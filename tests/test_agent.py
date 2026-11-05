"""Agent 形态：路径发现、决策、计划增补与有界闭环。"""

import json
import os
import shutil
import tempfile
import unittest

import support  # noqa: F401  （把 scripts/ 加进 sys.path）
from reconlib import agent, discover

BASE = ("--selector", "#content", "--delay", "0", "--timeout", "10",
        "--arms", "native")


def ok(handler):
    support.response(handler, 200, support.long_html())


def page_with_links(handler):
    support.response(handler, 200, support.long_html("首页") + (
        '<a href="/article/1">一</a><a href="/article/2">二</a>'
        '<a href="/img/cover.png">图</a><a href="https://other.test/x">外</a>'))


def login_wall(handler):
    support.response(handler, 200,
                     "<html><body><input type='password'><p>登录</p></body></html>")


def temp_dir(testcase):
    path = tempfile.mkdtemp(prefix="recon-agent-")
    testcase.addCleanup(shutil.rmtree, path, ignore_errors=True)
    return path


class DiscoverTest(unittest.TestCase):
    def test_same_host_absolute_and_relative_links(self):
        html = (
            '<a href="p2">p2</a>'
            '<a href="/p3#frag">p3</a>'
            '<a href="https://a.test/p4">p4</a>'
            '<a href="https://other.test/p5">外部</a>'
            '<a href="//a.test/p6">协议相对</a>'
            '<a href="javascript:void(0)">js</a>'
            '<a href="#top">锚点</a>'
            '<a href="/p2">重复</a>'
        )
        got = discover.links(html, "https://a.test/dir/page?x=1")
        self.assertEqual(got, [
            "https://a.test/dir/p2",
            "https://a.test/p3",
            "https://a.test/p4",
            "https://a.test/p6",
            "https://a.test/p2",
        ])

    def test_skips_static_assets_by_default(self):
        html = ('<a href="/a.png">img</a><a href="/b.css">css</a>'
                '<a href="/c.js">js</a><a href="/d.woff2">font</a>'
                '<a href="/page">page</a>')
        self.assertEqual(discover.links(html, "https://a.test/"),
                         ["https://a.test/page"])
        self.assertEqual(len(discover.links(html, "https://a.test/",
                                            skip_assets=False)), 5)

    def test_host_whitelist_and_cap(self):
        html = "".join(f'<a href="/p{i}">p</a>' for i in range(10))
        html += '<a href="https://b.test/x">b</a>'
        got = discover.links(html, "https://a.test/",
                             hosts=["a.test", "b.test"], max_links=3)
        self.assertEqual(got, ["https://a.test/p0", "https://a.test/p1",
                               "https://a.test/p2"])
        self.assertEqual(discover.links(html, "https://a.test/", max_links=0), [])

    def test_accepts_bytes_and_ignores_broken_markup(self):
        got = discover.links(b'<a href="/x">x</a><a href=', "https://a.test/")
        self.assertEqual(got, ["https://a.test/x"])


def advice(action):
    return {"next": {"action": action}}


class DecideTest(unittest.TestCase):
    def test_terminal_actions_stop(self):
        for action, (kind, _) in agent.TERMINAL.items():
            got = agent.decide(advice(action), 10, 2, 4, True)
            self.assertFalse(got["run"], action)
            self.assertEqual(got["kind"], kind, action)
            self.assertIn(action, got["reason"])

    def test_continue_actions_need_plan(self):
        for action in agent.CONTINUE:
            self.assertTrue(agent.decide(advice(action), 10, 2, 3, True)["run"])
            got = agent.decide(advice(action), 10, 2, 0, True)
            self.assertEqual(got["kind"], "plan-empty")

    def test_maintain_continues_while_frontier_remains(self):
        got = agent.decide(advice("maintain"), 10, 2, 4, True, frontier=3)
        self.assertTrue(got["run"])
        self.assertEqual(got["kind"], "continue")
        self.assertIn("3", got["reason"])
        self.assertEqual(agent.decide(advice("maintain"), 10, 2, 4, True,
                                      frontier=0)["kind"], "goal")

    def test_guards_precede_advice(self):
        self.assertEqual(agent.decide(advice("maintain"), 10, 0, 3, True)["kind"],
                         "max-rounds")
        self.assertEqual(agent.decide(advice("maintain"), 0, 2, 3, True)["kind"],
                         "budget")
        self.assertEqual(agent.decide(advice("maintain"), 10, 2, 3, False)["kind"],
                         "frontier-empty")

    def test_missing_advice_is_not_terminal(self):
        got = agent.decide(None, 10, 2, 1, True)
        self.assertTrue(got["run"])
        self.assertEqual(got["kind"], "continue")


class SafetyStopTest(unittest.TestCase):
    def test_detects_safety_signals(self):
        self.assertEqual(agent.safety_stop(
            {"stopped": "rate-limit-cooldown: 剩余 30s"})["kind"], "safety")
        self.assertEqual(agent.safety_stop(
            {"stopped": "circuit-open: 连续 blocked"})["kind"], "safety")
        self.assertEqual(agent.safety_stop({"suspended_pages": 1})["kind"],
                         "safety")
        self.assertEqual(agent.safety_stop(
            {"stopped": "没有可用的客户端臂"})["kind"], "config")

    def test_no_signal(self):
        self.assertIsNone(agent.safety_stop(None))
        self.assertIsNone(agent.safety_stop({"stopped": None}))
        self.assertIsNone(agent.safety_stop({}))


class AugmentTest(unittest.TestCase):
    def plan(self):
        return {"schema": "recon-explore/1", "steps": [
            {"context": "a.test|article|content", "arm": "native", "requests": 2},
        ], "allocated": 2}

    def test_adds_only_uncovered_routes(self):
        urls = ["https://a.test/article/1", "https://a.test/list/2",
                "https://a.test/list/3", "https://b.test/x"]
        body = agent.augment(self.plan(), urls, ["native", "cffi:chrome"])
        self.assertEqual(len(body["steps"]), 5)
        added = [s for s in body["steps"] if s.get("kind") == "frontier"]
        self.assertEqual([(s["context"], s["requests"]) for s in added], [
            ("a.test|list|unknown", 2), ("a.test|list|unknown", 2),
            ("b.test|other|unknown", 1), ("b.test|other|unknown", 1),
        ])
        self.assertEqual(body["frontier"],
                         {"routes": 2, "steps": 4, "requests": 6})

    def test_does_not_mutate_and_respects_cap(self):
        plan = self.plan()
        urls = ["https://a.test/list/1", "https://b.test/x"]
        body = agent.augment(plan, urls, ["native"], max_steps=1)
        self.assertEqual(len(plan["steps"]), 1)
        self.assertEqual(len(body["steps"]), 2)
        self.assertEqual(body["frontier"]["steps"], 1)

    def test_respects_request_cap(self):
        urls = ["https://a.test/list/1", "https://b.test/x"]
        body = agent.augment(self.plan(), urls, ["native", "cffi:chrome"],
                             max_requests=1)
        self.assertEqual(body["frontier"]["steps"], 1)
        self.assertEqual(body["frontier"]["requests"], 1)

    def test_rejects_non_object(self):
        with self.assertRaises(ValueError):
            agent.augment([], [], ["native"])


class AgentCliTest(unittest.TestCase):
    def test_single_round_reports_analysis_and_artifacts(self):
        with support.serve({"/": page_with_links,
                            "/article/1": ok, "/article/2": ok}) as base:
            out = temp_dir(self)
            r, data = support.run_json(
                "agent", base + "/", "--out", out, *BASE, "--max-rounds", "1",
                "--max-requests", "10", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(data["schema"], "agent/1")
        self.assertTrue(data["ok"])
        self.assertEqual(data["analysis"][0]["shape"], "static-html")
        self.assertEqual(len(data["rounds"]), 1)
        self.assertGreaterEqual(data["rounds"][0]["crawl"]["requests"], 1)
        self.assertGreaterEqual(data["rounds"][0]["discovered"], 2)
        self.assertEqual(data["stop"]["kind"], "max-rounds")
        for name in ("evidence.jsonl", "trials.jsonl", "policy.json",
                     "agent.json"):
            self.assertTrue(os.path.exists(os.path.join(out, name)), name)
        with open(os.path.join(out, "agent.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["schema"], "agent/1")

    def test_second_round_crawls_discovered_paths_under_plan(self):
        with support.serve({"/": page_with_links,
                            "/article/1": ok, "/article/2": ok}) as base:
            out = temp_dir(self)
            r, data = support.run_json(
                "agent", base + "/", "--out", out, *BASE,
                "--max-rounds", "2", "--max-pages", "6", "--max-requests", "20",
                "--budget", "6", "--policy", "fixed", "--json",
                "--i-have-permission")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(data["rounds"]), 2)
        self.assertGreaterEqual(data["rounds"][0]["discovered"], 2)
        second = data["rounds"][1]
        self.assertGreaterEqual(second["crawl"]["requests"], 2)
        self.assertGreaterEqual(second["plan"]["frontier"]["requests"], 2)
        with open(os.path.join(out, "evidence.jsonl"), encoding="utf-8") as f:
            urls = [json.loads(line).get("final_url") for line in f if line.strip()]
        self.assertTrue(any("/article/1" in (u or "") for u in urls), urls)
        self.assertTrue(any("/article/2" in (u or "") for u in urls), urls)
        self.assertFalse(any("cover.png" in (u or "") for u in urls), urls)

    def test_login_wall_is_gated_without_crawl(self):
        with support.serve({"/": login_wall}) as base:
            out = temp_dir(self)
            r, data = support.run_json(
                "agent", base + "/", "--out", out, *BASE,
                "--max-rounds", "2", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(data["analysis"][0]["shape"], "login-wall")
        self.assertEqual(data["rounds"], [])
        self.assertEqual(data["stop"]["kind"], "analysis-gated")
        self.assertEqual(data["budget"]["requests"], 1)

    def test_robots_deny_sends_no_page_request(self):
        hits = {"page": 0}

        def robots(handler):
            support.response(handler, 200, "User-agent: *\nDisallow: /\n",
                             ctype="text/plain")

        def page(handler):
            hits["page"] += 1
            ok(handler)

        with support.serve({"/robots.txt": robots, "/": page}) as base:
            out = temp_dir(self)
            r, data = support.run_json(
                "agent", base + "/", "--out", out, *BASE,
                "--max-rounds", "2", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(hits["page"], 0)
        self.assertEqual(data["analysis"][0]["shape"], "robots-deny")
        self.assertEqual(data["rounds"], [])
        self.assertEqual(data["budget"]["requests"], 0)

    def test_no_discover_skips_bodies_and_frontier(self):
        with support.serve({"/": page_with_links}) as base:
            out = temp_dir(self)
            r, data = support.run_json(
                "agent", base + "/", "--out", out, *BASE,
                "--max-rounds", "1", "--no-discover", "--json",
                "--i-have-permission")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(data["rounds"][0]["discovered"], 0)
        self.assertIsNone(data["artifacts"]["bodies"])

    def test_usage_errors(self):
        out = temp_dir(self)
        r = support.run("agent", "--out", out)
        self.assertEqual(r.returncode, 2)
        r = support.run("agent", "https://example.test/", "--out", out,
                        "--max-rounds", "0")
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
