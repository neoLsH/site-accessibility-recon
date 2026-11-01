import json
import unittest

import support

OPTS = ("--selector", "#content", "--delay", "0", "--timeout", "10")


class QuickTest(unittest.TestCase):
    def test_ok_page_is_feasible(self):
        routes = {"/ok": lambda h: support.response(h, 200, support.long_html())}
        with support.serve(routes) as base:
            r, data = support.run_json("quick", base + "/ok", *OPTS, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        row = data["rows"][0]
        self.assertEqual(row["verdict"], "可行·HTTP")
        self.assertEqual(row["http"], "200")
        self.assertGreaterEqual(int(row["content"]), 200)

    def test_csr_shell_is_browser_feasible(self):
        body = ('<html><body><div id="root">加载中</div><script>'
                + "x" * 1200 + "</script></body></html>")
        with support.serve({"/app": lambda h: support.response(h, 200, body)}) as base:
            r, data = support.run_json("quick", base + "/app", *OPTS, "--json")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(data["rows"][0]["verdict"], "可行·浏览器")

    def test_challenge_is_suspected_block(self):
        routes = {"/wall": lambda h: support.response(
            h, 403, "<html><body>请完成安全验证</body></html>")}
        with support.serve(routes) as base:
            r, data = support.run_json("quick", base + "/wall", *OPTS, "--json")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(data["rows"][0]["verdict"], "疑似拦截")

    def test_robots_deny(self):
        hits = {"n": 0}

        def page(h):
            hits["n"] += 1
            support.response(h, 200, support.long_html())

        routes = {"/robots.txt": lambda h: support.response(
                      h, 200, "User-agent: *\nDisallow: /private\n", ctype="text/plain"),
                  "/private/a": page}
        with support.serve(routes) as base:
            r, data = support.run_json("quick", base + "/private/a", *OPTS, "--json")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(data["rows"][0]["verdict"], "合规禁止")
        self.assertEqual(hits["n"], 0)

    def test_one_request_per_url(self):
        hits = {"n": 0}

        def page(h):
            hits["n"] += 1
            support.response(h, 200, support.long_html())

        with support.serve({"/ok": page}) as base:
            support.run_json("quick", base + "/ok", *OPTS, "--json")
        self.assertEqual(hits["n"], 1)

    def test_unconfigured_arm_falls_back(self):
        cfg = support.write_temp('{"arms": []}')
        routes = {"/ok": lambda h: support.response(h, 200, support.long_html())}
        try:
            with support.serve(routes) as base:
                r, data = support.run_json(
                    "quick", base + "/ok", *OPTS, "--arms", "browser:chromium,native",
                    "--arms-file", cfg, "--json")
        finally:
            support.os.unlink(cfg)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(data["rows"][0]["verdict"], "可行·HTTP")


    def test_policy_artifact_written(self):
        path = support.temp_path(".json")
        try:
            routes = {"/ok": lambda h: support.response(h, 200, support.long_html())}
            with support.serve(routes) as base:
                r = support.run("quick", base + "/ok", *OPTS, "--policy-out", path)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(path, encoding="utf-8") as f:
                body = json.load(f)
            self.assertEqual(body["schema"], "recon-policy/1")
            self.assertGreaterEqual(body["contexts"], 1)
        finally:
            support.os.unlink(path)

    def test_bad_policy_in_is_usage_error(self):
        path = support.write_temp('{"schema": "other"}')
        try:
            r = support.run("quick", "http://127.0.0.1:9/x", *OPTS,
                            "--policy-in", path)
        finally:
            support.os.unlink(path)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
