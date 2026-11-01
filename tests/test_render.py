import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import support
from reconlib import render
from reconlib.arms import Arm


def script(body):
    fd, path = tempfile.mkstemp(suffix=".py")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return path


def cmd_arm(path):
    return Arm("browser:chromium", "cmd", engine="chromium",
               capabilities=("tls", "js"),
               command=[sys.executable, path, "{url}", "{engine}", "{ua}", "{timeout}"])


class CmdTransportTest(unittest.TestCase):
    def test_success(self):
        path = script(
            "import json,sys\n"
            "print(json.dumps({'status': 200, 'body': '<h1>ok</h1>', "
            "'final_url': sys.argv[1], 'ctype': 'text/html'}))\n")
        obs = render.fetch("http://127.0.0.1:9/x", cmd_arm(path), timeout=5)
        self.assertEqual(obs["status"], 200)
        self.assertEqual(obs["body"], b"<h1>ok</h1>")
        self.assertEqual(obs["arm"], "browser:chromium")
        self.assertIsNone(obs["error"])

    def test_nonzero_exit_is_error(self):
        path = script("import sys\nsys.stderr.write('boom')\nsys.exit(3)\n")
        obs = render.fetch("http://127.0.0.1:9/x", cmd_arm(path), timeout=5)
        self.assertEqual(obs["status"], 0)
        self.assertIn("rc=3", obs["error"])

    def test_bad_json_is_error(self):
        path = script("print('not json')\n")
        obs = render.fetch("http://127.0.0.1:9/x", cmd_arm(path), timeout=5)
        self.assertIn("JSON", obs["error"])

    def test_timeout_is_error(self):
        path = script("import time\ntime.sleep(5)\n")
        obs = render.fetch("http://127.0.0.1:9/x", cmd_arm(path), timeout=1)
        self.assertIn("超时", obs["error"])

    def test_body_cap(self):
        arm = cmd_arm(script("pass\n"))
        obs = render._payload_observation(
            arm, {"status": 200, "body": "x" * (render.MAX_BODY + 1)}, 1)
        self.assertEqual(len(obs["body"]), render.MAX_BODY)
        self.assertTrue(obs["truncated"])

    def test_unconfigured_arm_is_skipped(self):
        arm = Arm("browser:webkit", "cmd", engine="webkit",
                  capabilities=("js",))
        obs = render.fetch("http://127.0.0.1:9/x", arm, timeout=5)
        self.assertTrue(obs["skipped"])
        self.assertIn("未配置", obs["error"])


class HttpTransportTest(unittest.TestCase):
    def serve(self, status, payload):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                json.loads(self.rfile.read(n))
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return f"http://127.0.0.1:{httpd.server_port}/render"

    def test_success(self):
        endpoint = self.serve(200, {"status": 200, "body": "ok"})
        arm = Arm("browser:webkit", "http", engine="webkit",
                  capabilities=("js",), endpoint=endpoint)
        obs = render.fetch("https://example.test/a", arm, timeout=5)
        self.assertEqual(obs["status"], 200)
        self.assertEqual(obs["body"], b"ok")

    def test_5xx_is_error(self):
        endpoint = self.serve(500, {"error": "boom"})
        arm = Arm("browser:webkit", "http", engine="webkit",
                  capabilities=("js",), endpoint=endpoint)
        obs = render.fetch("https://example.test/a", arm, timeout=5)
        self.assertIn("HTTP 500", obs["error"])


class ShellSafetyTest(unittest.TestCase):
    def test_render_does_not_use_shell(self):
        path = os.path.join(support.SCRIPTS, "reconlib", "render.py")
        with open(path, encoding="utf-8") as f:
            self.assertNotIn("shell=True", f.read())


if __name__ == "__main__":
    unittest.main()
