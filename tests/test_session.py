import json
import os
import sys
import tempfile
import unittest

import support
from reconlib import arms
from reconlib.session import ArmSession


def empty_registry():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"arms": []}')
    return arms.load(path)


def render_arm(script_body, name="browser:chromium"):
    fd, path = tempfile.mkstemp(suffix=".py")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        f.write(script_body)
    fd, cfg = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    with open(cfg, "w", encoding="utf-8") as f:
        f.write(json.dumps({"arms": [{
            "name": name, "transport": "cmd",
            "command": [sys.executable, path, "{url}"],
            "capabilities": ["tls", "js"]}]}))
    return arms.load(cfg)


class SessionTest(unittest.TestCase):
    def test_ok_page_records_evidence_and_trial(self):
        routes = {"/ok": lambda h: support.response(h, 200, support.long_html())}
        with support.serve(routes) as base:
            s = ArmSession(["native"], registry=empty_registry(),
                           policy="fixed", delay=0, permission=True)
            page = s.probe(base + "/ok", selector="#content", min_content=200)
        self.assertEqual(page["evidence"]["outcome"], "ok")
        self.assertEqual(page["requests"], 1)
        self.assertEqual(page["stopped"], "ok")
        self.assertEqual(len(s.trials), 1)
        self.assertEqual(s.trials[0]["kind"], "trial")

    def test_robots_deny_makes_zero_requests(self):
        hits = {"n": 0}

        def page(h):
            hits["n"] += 1
            support.response(h, 200, support.long_html())

        routes = {"/robots.txt": lambda h: support.response(
                      h, 200, "User-agent: *\nDisallow: /private\n", ctype="text/plain"),
                  "/private/a": page}
        with support.serve(routes) as base:
            s = ArmSession(["native"], registry=empty_registry(), delay=0)
            got = s.probe(base + "/private/a")
        self.assertEqual(got["skipped"], "robots-deny")
        self.assertEqual(got["requests"], 0)
        self.assertEqual(hits["n"], 0)

    def test_unavailable_arm_is_skipped_not_counted(self):
        s = ArmSession(["browser:chromium"], registry=empty_registry(), delay=0,
                       permission=True)
        page = s.probe("http://127.0.0.1:9/x")
        self.assertEqual(page["requests"], 0)
        self.assertEqual(page["halt"], "no-arm")
        self.assertTrue(page["tries"][0]["skipped"])

    def test_budget_stops_before_request(self):
        routes = {"/ok": lambda h: support.response(h, 200, support.long_html())}
        with support.serve(routes) as base:
            s = ArmSession(["native"], registry=empty_registry(), delay=0,
                           permission=True)
            page = s.probe(base + "/ok", max_attempts=0)
        self.assertEqual(page["halt"], "budget")
        self.assertEqual(page["requests"], 0)

    def test_circuit_opens_and_blocks_next_page(self):
        routes = {"/wall": lambda h: support.response(h, 403, "Forbidden")}
        with support.serve(routes) as base:
            s = ArmSession(["native"], registry=empty_registry(), delay=0,
                           permission=True, max_consecutive_blocked=1)
            first = s.probe(base + "/wall")
            second = s.probe(base + "/wall")
        self.assertEqual(first["stopped"], "circuit-open")
        self.assertTrue(second["skipped"].startswith("circuit-open"))
        self.assertEqual(second["requests"], 0)

    def test_all_arms_suspended_halts_without_request(self):
        routes = {"/wall": lambda h: support.response(h, 403, "Forbidden")}
        with support.serve(routes) as base:
            s = ArmSession(["native"], registry=empty_registry(), delay=0,
                           permission=True, block_budget=1,
                           max_consecutive_blocked=5)
            first = s.probe(base + "/wall")
            second = s.probe(base + "/wall")
        self.assertEqual(first["evidence"]["outcome"], "blocked")
        self.assertEqual(second["halt"], "all-arms-suspended")
        self.assertEqual(second["requests"], 0)
        self.assertEqual(second["suspended"], ["native"])

    def test_block_budget_zero_keeps_requesting(self):
        routes = {"/wall": lambda h: support.response(h, 403, "Forbidden")}
        with support.serve(routes) as base:
            s = ArmSession(["native"], registry=empty_registry(), delay=0,
                           permission=True, block_budget=0,
                           max_consecutive_blocked=5)
            s.probe(base + "/wall")
            second = s.probe(base + "/wall")
        self.assertEqual(second["requests"], 1)
        self.assertEqual(second["evidence"]["outcome"], "blocked")

    def test_require_filters_without_request(self):
        s = ArmSession(["native"], registry=empty_registry(), delay=0,
                       permission=True)
        page = s.probe("http://127.0.0.1:9/x", require=("js",))
        self.assertEqual(page["halt"], "no-arm")
        self.assertIn("缺少能力", page["tries"][0]["reason"])

    def test_external_error_becomes_noise(self):
        reg = render_arm("import sys\nsys.stderr.write('boom')\nsys.exit(2)\n")
        s = ArmSession(["browser:chromium"], registry=reg, policy="fixed",
                       delay=0, permission=True)
        page = s.probe("http://127.0.0.1:9/x", selector="#content")
        self.assertEqual(page["evidence"]["outcome"], "noise")
        self.assertEqual(page["requests"], 1)
        self.assertEqual(page["stopped"], "noise")

    def test_context_and_cost_are_recorded(self):
        routes = {"/article/1": lambda h: support.response(h, 200, support.long_html())}
        with support.serve(routes) as base:
            s = ArmSession(["native"], registry=empty_registry(),
                           policy="fixed", delay=0, permission=True)
            page = s.probe(base + "/article/1", selector="#content",
                           min_content=200)
        self.assertEqual(page["context"], "article|content")
        full = f"{page['host']}|article|content"
        self.assertEqual(s.trials[0]["context"], full)
        self.assertIn("cost_ms", s.trials[0])
        self.assertEqual(page["evidence"]["context"], full)

    def test_stop_on_ok_false_runs_all_arms(self):
        ok_script = (
            "import json,sys\n"
            "print(json.dumps({'status': 200, 'body': "
            "'<article id=\"content\">' + '正文' * 120 + '</article>'}))\n")
        reg = render_arm(ok_script)
        command = reg.get("browser:chromium").command
        fd, cfg = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        with open(cfg, "w", encoding="utf-8") as f:
            f.write(json.dumps({"arms": [
                {"name": "browser:chromium", "transport": "cmd",
                 "command": command, "capabilities": ["tls"]},
                {"name": "browser:webkit", "transport": "cmd",
                 "command": command, "capabilities": ["tls"]}]}))
        reg = arms.load(cfg)
        s = ArmSession(["browser:chromium", "browser:webkit"], registry=reg,
                       policy="round_robin", delay=0, permission=True)
        page = s.probe("http://127.0.0.1:9/x", selector="#content",
                       max_attempts=2, stop_on_ok=False)
        self.assertEqual(len(page["tries"]), 2)
        self.assertEqual(page["requests"], 2)


if __name__ == "__main__":
    unittest.main()
