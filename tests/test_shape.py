import json
import os
import unittest

import support


class ShapeOfflineTest(unittest.TestCase):
    def shape(self, *args):
        return support.run_json("shape", "--json", *args)

    def test_static_fixture(self):
        r, data = self.shape("--html", support.fixture("static.html"))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(data["shape"], "static-html")
        self.assertGreaterEqual(data["text_len"], 200)
        self.assertTrue(data["explain"]["decisions"])

    def test_csr_shell_fixture(self):
        r, data = self.shape("--html", support.fixture("csr.html"))
        self.assertEqual(data["shape"], "csr-shell")
        self.assertTrue(data["signals"]["mount_point"])
        self.assertTrue(data["signals"]["csr_global"])

    def test_api_fixture_sniffed_without_content_type(self):
        r, data = self.shape("--html", support.fixture("api.json"))
        self.assertEqual(data["shape"], "api-response")

    def test_thin_fixture(self):
        r, data = self.shape("--html", support.fixture("thin.html"))
        self.assertEqual(data["shape"], "thin-page")

    def test_custom_signals_override(self):
        with open(os.path.join(support.ROOT, "assets", "shape-signals.json"), encoding="utf-8") as f:
            signals = json.load(f)
        signals["challenge"] = ["custom-wall"]
        path = support.write_temp(json.dumps(signals))
        default = support.run_json("shape", "--html", support.fixture("custom-marker.html"), "--json")[1]
        custom = support.run_json("shape", "--html", support.fixture("custom-marker.html"),
                                  "--signals", path, "--json")[1]
        self.assertEqual(default["shape"], "thin-page")
        self.assertEqual(custom["shape"], "challenge")
        self.assertIn("custom-wall", custom["explain"]["matched"]["challenge"])

    def test_broken_signals_file_is_usage_error(self):
        path = support.write_temp("{}")
        r = support.run("shape", "--html", support.fixture("thin.html"), "--signals", path)
        self.assertEqual(r.returncode, 2)
        self.assertIn("缺少", r.stderr)

    def test_signals_top_level_must_be_object(self):
        path = support.write_temp("[1, 2]")
        r = support.run("shape", "--html", support.fixture("thin.html"), "--signals", path)
        self.assertEqual(r.returncode, 2)
        self.assertIn("对象", r.stderr)


class ShapeLiveTest(unittest.TestCase):
    def test_status_and_redirect_shapes(self):
        def challenge(h):
            support.response(h, 403, "<html><head><title>验证</title></head>"
                                     "<body><p>请完成安全验证</p></body></html>")

        def login_page(h):
            support.response(h, 200, "<html><body><form>"
                                     "<input type=\"password\"><button>登录</button>"
                                     "</form></body></html>")

        redirect_login = lambda h: support.response(h, 302, "", headers=[("Location", "/login-page")])
        with open(support.fixture("csr.html"), encoding="utf-8") as f:
            csr_body = f.read()

        routes = {
            "/static": lambda h: support.response(h, 200, support.long_html()),
            "/csr": lambda h: support.response(h, 200, csr_body),
            "/challenge": challenge,
            "/login": redirect_login,
            "/login-page": login_page,
            "/api": lambda h: support.response(h, 200, '{"ok": true}', "application/json"),
            "/missing": lambda h: support.response(h, 404, "<html><body>gone</body></html>"),
        }
        expected = {
            "/static": "static-html",
            "/csr": "csr-shell",
            "/challenge": "challenge",
            "/login": "login-wall",
            "/api": "api-response",
            "/missing": "error-page",
        }
        with support.serve(routes) as base:
            for path, shape in expected.items():
                with self.subTest(path=path):
                    r, data = support.run_json("shape", base + path, "--json")
                    self.assertEqual(data["shape"], shape)
                    self.assertEqual(r.returncode, 0)

    def test_explain_output_lists_matched_marker(self):
        def challenge(h):
            support.response(h, 403, "<html><body><p>请完成人机验证</p></body></html>")

        with support.serve({"/c": challenge}) as base:
            r = support.run("shape", base + "/c", "--explain")
            self.assertEqual(r.returncode, 0)
            self.assertIn("hit challenge:", r.stdout)
            self.assertIn("gates:", r.stdout)


if __name__ == "__main__":
    unittest.main()
