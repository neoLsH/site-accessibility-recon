import os
import tempfile
import unittest

import support
from reconlib import measure, robots
from reconlib.classify import classify
from reconlib.fetch import parse_arm, save_html

H = "text/html; charset=utf-8"


def obs(status=200, body="", ctype=H, headers="", error=None):
    data = body.encode("utf-8") if isinstance(body, str) else body
    return {"status": status, "bytes": len(data), "final_url": "https://example.test/x",
            "ctype": ctype, "headers": headers, "body": data, "error": error}


class MeasureTest(unittest.TestCase):
    def test_counts_only_selected_region_and_skips_script(self):
        body = ('<html><body><div id="content">hello<script>var x=1</script> world</div>'
                '<div class="side">导航</div></body></html>')
        self.assertEqual(measure.content_len(body.encode(), "#content"), 10)

    def test_class_and_tag_selectors(self):
        body = b'<article class="post">' + "正文".encode() * 4 + b'</article>'
        self.assertEqual(measure.content_len(body, "article"), 8)
        self.assertEqual(measure.content_len(body, ".post"), 8)

    def test_missing_and_unsupported(self):
        self.assertEqual(measure.content_len(b"<p>hi</p>", "#content"), "missing")
        self.assertEqual(measure.content_len(support.long_html().encode(), "div > p"),
                         "?unsupported")
        self.assertEqual(measure.content_len(support.long_html().encode(), ""), "-")

    def test_is_html(self):
        self.assertTrue(measure.is_html("text/html; charset=utf-8"))
        self.assertTrue(measure.is_html(""))
        self.assertTrue(measure.is_html("application/xhtml+xml"))
        self.assertFalse(measure.is_html("application/json"))


class ClassifyTest(unittest.TestCase):
    def test_ok_when_region_meets_floor(self):
        r = classify(obs(body=support.long_html()), "#content", 200)
        self.assertEqual(r["outcome"], "ok")
        self.assertEqual(r["waf_no"], 0)
        self.assertTrue(r["attributable"])

    def test_short_body_is_degraded(self):
        r = classify(obs(body="<p>加载中</p>"), "#content", 200)
        self.assertEqual(r["outcome"], "degraded")
        self.assertEqual(r["waf_no"], 5)
        self.assertFalse(r["rate_limited"])

    def test_interactive_challenge(self):
        r = classify(obs(403, "<html><body>请完成安全验证</body></html>"), "#content", 200)
        self.assertEqual((r["outcome"], r["waf_no"]), ("blocked", 4))
        self.assertIn("交互验证", r["waf_class"])

    def test_compute_challenge(self):
        body = "<html><body>Please verify you are human</body></html>"
        r = classify(obs(403, body), "#content", 200)
        self.assertEqual((r["outcome"], r["waf_no"]), ("blocked", 3))

    def test_hard_edge_block(self):
        r = classify(obs(403, "Forbidden"), "#content", 200)
        self.assertEqual((r["outcome"], r["waf_no"]), ("blocked", 1))
        self.assertTrue(r["attributable"])

    def test_rate_limit_is_not_attributable(self):
        r = classify(obs(429, "slow down", headers="Retry-After: 30"), "#content", 200)
        self.assertEqual((r["outcome"], r["waf_no"]), ("blocked", 6))
        self.assertTrue(r["rate_limited"])
        self.assertFalse(r["attributable"])

    def test_auth_and_missing_are_not_waf(self):
        auth = classify(obs(401, "login"), "#content", 200)
        missing = classify(obs(404, "nope"), "#content", 200)
        self.assertEqual(auth["outcome"], "auth")
        self.assertEqual(missing["outcome"], "missing")
        self.assertFalse(auth["attributable"])
        self.assertFalse(missing["attributable"])

    def test_5xx_is_noise(self):
        r = classify(obs(503, "upstream error"), "#content", 200)
        self.assertEqual((r["outcome"], r["waf_no"]), ("noise", 0))

    def test_link_failure_is_noise(self):
        r = classify(obs(0, error="curl rc=28: timed out"), "#content", 200)
        self.assertEqual((r["outcome"], r["waf_no"]), ("noise", 1))
        self.assertFalse(r["attributable"])

    def test_non_html_2xx_is_ok(self):
        r = classify(obs(200, '{"a": 1}', ctype="application/json"), "#content", 200)
        self.assertEqual(r["outcome"], "ok")

    def test_script_shell_is_compute_challenge(self):
        shell = ('<html><body><div id="content">加载中</div><script>'
                 + "x" * 1200 + "</script></body></html>")
        r = classify(obs(body=shell), "#content", 200)
        self.assertEqual((r["outcome"], r["waf_no"]), ("blocked", 3))

    def test_no_selector_uses_byte_floor(self):
        small = classify(obs(body="<p>" + "字" * 100 + "</p>"), "", 200)
        big = classify(obs(body="<p>" + "字" * 400 + "</p>"), "", 200)
        self.assertEqual(small["outcome"], "degraded")
        self.assertEqual(big["outcome"], "ok")


class RobotsTest(unittest.TestCase):
    def routes(self, status=200, body="User-agent: *\nDisallow: /private\n"):
        return {"/robots.txt": lambda h: support.response(h, status, body, ctype="text/plain")}

    def test_allow_and_deny(self):
        with support.serve(self.routes()) as base:
            self.assertEqual(robots.decision(base + "/public/a", timeout=5), "allow")
            self.assertEqual(robots.decision(base + "/private/a", timeout=5), "deny")

    def test_robots_403_is_deny(self):
        with support.serve(self.routes(403, "no")) as base:
            self.assertEqual(robots.decision(base + "/a", timeout=5), "deny")

    def test_robots_5xx_is_unknown(self):
        with support.serve(self.routes(500, "boom")) as base:
            self.assertEqual(robots.decision(base + "/a", timeout=5), "unknown")

    def test_unreachable_is_unknown(self):
        self.assertEqual(robots.decision("http://127.0.0.1:9/x", timeout=2), "unknown")


class FetchHelpersTest(unittest.TestCase):
    def test_parse_arm(self):
        self.assertEqual(parse_arm("native"), ("native", None))
        self.assertEqual(parse_arm("cffi:chrome124"), ("cffi", "chrome124"))
        with self.assertRaises(ValueError):
            parse_arm("browser")

    def test_save_html(self):
        with tempfile.TemporaryDirectory() as d:
            path = save_html(d, "https://example.test/a/b", "cffi:chrome", b"<html>ok</html>")
            self.assertTrue(path.endswith("--cffi_chrome.html"))
            with open(path, "rb") as f:
                self.assertEqual(f.read(), b"<html>ok</html>")
            self.assertIsNone(save_html("", "https://example.test/a", "native", b"x"))
            self.assertIsNone(save_html(d, "https://example.test/a", "native", b""))


if __name__ == "__main__":
    unittest.main()
