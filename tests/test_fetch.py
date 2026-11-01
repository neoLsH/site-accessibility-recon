import unittest

import support

OPTS = ("--selector", "#content", "--timeout", "10", "--delay", "0")


class FetchCliTest(unittest.TestCase):
    def test_ok_page(self):
        routes = {"/ok": lambda h: support.response(h, 200, support.long_html())}
        with support.serve(routes) as base:
            r, data = support.run_json("fetch", base + "/ok", *OPTS, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        rec = data["records"][0]
        self.assertEqual(rec["robots"], "allow")
        self.assertEqual(rec["verdict"], "已拿到内容")
        rung = rec["rungs"][0]
        self.assertEqual(rung["rung"], "curl(native)")
        self.assertEqual(rung["outcome"], "ok")
        self.assertGreaterEqual(rung["content"], 200)
        self.assertTrue(rung["attributable"])

    def test_interactive_challenge_is_classified(self):
        routes = {"/wall": lambda h: support.response(
            h, 403, "<html><body>请完成安全验证</body></html>")}
        with support.serve(routes) as base:
            r, data = support.run_json("fetch", base + "/wall", *OPTS, "--json")
        self.assertEqual(r.returncode, 1)
        rec = data["records"][0]
        self.assertEqual(rec["verdict"], "未拿到内容")
        self.assertEqual(rec["rungs"][0]["outcome"], "blocked")
        self.assertEqual(rec["waf_class"], "4 交互验证")

    def test_rate_limit_is_not_attributed_to_client(self):
        routes = {"/slow": lambda h: support.response(
            h, 429, "slow down", headers=[("Retry-After", "30")])}
        with support.serve(routes) as base:
            r, data = support.run_json("fetch", base + "/slow", *OPTS, "--json")
        self.assertEqual(r.returncode, 1)
        rec = data["records"][0]
        rung = rec["rungs"][0]
        self.assertTrue(rung["rate_limited"])
        self.assertFalse(rung["attributable"])
        self.assertEqual(rec["waf_class"], "6 速率与配额")

    def test_link_failure_is_noise(self):
        r, data = support.run_json("fetch", "http://127.0.0.1:9/x", *OPTS, "--json")
        self.assertEqual(r.returncode, 1)
        rung = data["records"][0]["rungs"][0]
        self.assertEqual(rung["outcome"], "noise")
        self.assertFalse(rung["attributable"])

    def test_robots_deny_skips_request(self):
        hits = {"n": 0}

        def page(h):
            hits["n"] += 1
            support.response(h, 200, support.long_html())

        routes = {"/robots.txt": lambda h: support.response(
                      h, 200, "User-agent: *\nDisallow: /private\n", ctype="text/plain"),
                  "/private/a": page}
        with support.serve(routes) as base:
            r, data = support.run_json("fetch", base + "/private/a", *OPTS, "--json")
        self.assertEqual(r.returncode, 1)
        rec = data["records"][0]
        self.assertEqual(rec["robots"], "deny")
        self.assertEqual(rec["verdict"], "合规禁止")
        self.assertEqual(rec["rungs"], [])
        self.assertEqual(hits["n"], 0)

    def test_permission_flag_skips_robots(self):
        routes = {"/robots.txt": lambda h: support.response(
                      h, 200, "User-agent: *\nDisallow: /\n", ctype="text/plain"),
                  "/private/a": lambda h: support.response(h, 200, support.long_html())}
        with support.serve(routes) as base:
            r, data = support.run_json("fetch", base + "/private/a", *OPTS,
                                       "--i-have-permission", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        rec = data["records"][0]
        self.assertEqual(rec["robots"], "skipped")
        self.assertEqual(rec["verdict"], "已拿到内容")

    def test_rung_native_runs_one_step(self):
        routes = {"/ok": lambda h: support.response(h, 200, support.long_html())}
        with support.serve(routes) as base:
            r, data = support.run_json("fetch", base + "/ok", *OPTS,
                                       "--rung", "native", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(data["records"][0]["rungs"]), 1)

    def test_invalid_rung_is_usage_error(self):
        r = support.run("fetch", "http://127.0.0.1:9/x", "--rung", "browser")
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
