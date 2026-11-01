import json
import os
import unittest

import support

BASE = ("--selector", "#content", "--arms", "native", "--delay", "0",
        "--timeout", "10", "--json")


def pages_of(data):
    return data["pages"]


class CrawlTest(unittest.TestCase):
    def test_ok_page_emits_evidence(self):
        routes = {"/ok": lambda h: support.response(h, 200, support.long_html())}
        with support.serve(routes) as base:
            r, data = support.run_json("crawl", base + "/ok", *BASE)
        self.assertEqual(r.returncode, 0, r.stderr)
        page = pages_of(data)[0]
        self.assertEqual(page["evidence"]["outcome"], "ok")
        self.assertEqual(page["stopped"], "ok")
        self.assertEqual(data["summary"]["requests"], 1)
        self.assertIsNone(data["summary"]["stopped"])

    def test_blocked_page_and_circuit(self):
        routes = {"/wall": lambda h: support.response(h, 403, "Forbidden")}
        with support.serve(routes) as base:
            r, data = support.run_json("crawl", base + "/wall", base + "/wall",
                                       *BASE, "--max-consecutive-blocked", "1")
        self.assertEqual(r.returncode, 1)
        pages = pages_of(data)
        self.assertEqual(pages[0]["evidence"]["outcome"], "blocked")
        self.assertEqual(pages[0]["stopped"], "circuit-open")
        self.assertEqual(len(pages[0]["tries"]), 1)
        self.assertTrue(pages[1]["skipped"].startswith("circuit-open"))
        self.assertEqual(data["summary"]["requests"], 1)

    def test_rate_limit_stops_without_updating_policy(self):
        routes = {"/slow": lambda h: support.response(
            h, 429, "slow down", headers=[("Retry-After", "30")])}
        with support.serve(routes) as base:
            r, data = support.run_json("crawl", base + "/slow", *BASE)
        self.assertEqual(r.returncode, 0, r.stderr)
        page = pages_of(data)[0]
        self.assertEqual(page["evidence"]["outcome"], "blocked")
        self.assertTrue(page["tries"][0]["rate_limited"])
        self.assertEqual(len(page["tries"]), 1)

    def test_robots_deny_skips_request(self):
        hits = {"n": 0}

        def page(h):
            hits["n"] += 1
            support.response(h, 200, support.long_html())

        routes = {"/robots.txt": lambda h: support.response(
                      h, 200, "User-agent: *\nDisallow: /private\n", ctype="text/plain"),
                  "/private/a": page}
        with support.serve(routes) as base:
            r, data = support.run_json("crawl", base + "/private/a", *BASE)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(hits["n"], 0)
        self.assertEqual(pages_of(data)[0]["skipped"], "robots-deny")
        self.assertEqual(data["summary"]["skipped_robots"], 1)

    def test_max_pages_stops_early(self):
        routes = {"/ok": lambda h: support.response(h, 200, support.long_html())}
        with support.serve(routes) as base:
            r, data = support.run_json("crawl", base + "/ok", base + "/ok",
                                       *BASE, "--max-pages", "1")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(len(pages_of(data)), 1)
        self.assertIn("max-pages", data["summary"]["stopped"])

    def test_max_requests_stops_before_next_page(self):
        routes = {"/ok": lambda h: support.response(h, 200, support.long_html())}
        with support.serve(routes) as base:
            r, data = support.run_json("crawl", base + "/ok", base + "/ok",
                                       *BASE, "--max-requests", "1")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(data["summary"]["requests"], 1)
        self.assertIn("max-requests", data["summary"]["stopped"])

    def test_evidence_and_trial_files(self):
        evidence = support.temp_path(".jsonl")
        trials = support.temp_path(".jsonl")
        try:
            routes = {"/ok": lambda h: support.response(h, 200, support.long_html()),
                      "/wall": lambda h: support.response(h, 403, "Forbidden")}
            with support.serve(routes) as base:
                r, data = support.run_json(
                    "crawl", base + "/ok", base + "/wall", *BASE,
                    "--evidence-out", evidence, "--trial-out", trials)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(evidence, encoding="utf-8") as f:
                rows = [json.loads(x) for x in f if x.strip()]
            with open(trials, encoding="utf-8") as f:
                trs = [json.loads(x) for x in f if x.strip()]
            self.assertEqual([x["outcome"] for x in rows], ["ok", "blocked"])
            self.assertTrue(all(x["kind"] == "trial" for x in trs))
            self.assertTrue(all("outcome" not in x for x in trs))
            stats = support.run("stats", evidence)
            self.assertEqual(stats.returncode, 0, stats.stderr)
        finally:
            for path in (evidence, trials):
                if os.path.exists(path):
                    os.unlink(path)

    def test_unconfigured_browser_arm_is_skipped(self):
        cfg = support.write_temp('{"arms": []}')
        try:
            r, data = support.run_json("crawl", "http://127.0.0.1:9/x",
                                       "--arms", "browser:chromium",
                                       "--arms-file", cfg, "--delay", "0",
                                       "--timeout", "5", "--json")
        finally:
            os.unlink(cfg)
        self.assertEqual(r.returncode, 1)
        page = data["pages"][0]
        self.assertEqual(page["skipped"], "no-arm")
        self.assertEqual(page["requests"], 0)
        self.assertIn("没有可用的客户端臂", data["summary"]["stopped"])

    def test_invalid_policy_is_usage_error(self):
        r = support.run("crawl", "http://127.0.0.1:9/x", "--policy", "magic")
        self.assertEqual(r.returncode, 2)

    def test_invalid_arm_is_usage_error(self):
        r = support.run("crawl", "http://127.0.0.1:9/x", "--arms", "browser")
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
