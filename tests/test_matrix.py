import json
import os
import sys
import tempfile
import unittest

import support

OPTS = ("--selector", "#content", "--delay", "0", "--timeout", "10")


def fixture_registry():
    fd, script = tempfile.mkstemp(suffix=".py")
    os.close(fd)
    with open(script, "w", encoding="utf-8") as f:
        f.write(
            "import json,sys\n"
            "print(json.dumps({'status': 200, 'body': "
            "'<article id=\"content\">' + '正文' * 120 + '</article>'}))\n")
    fd, cfg = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    with open(cfg, "w", encoding="utf-8") as f:
        f.write(json.dumps({"arms": [
            {"name": "browser:chromium", "transport": "cmd",
             "command": [sys.executable, script, "{url}"],
             "capabilities": ["tls", "js"]},
            {"name": "browser:webkit", "transport": "cmd",
             "command": [sys.executable, script, "{url}"],
             "capabilities": ["tls", "js"]},
        ]}))
    return cfg, ("browser:chromium", "browser:webkit")


class MatrixTest(unittest.TestCase):
    def arms(self):
        cfg, names = fixture_registry()
        self.addCleanup(lambda: os.path.exists(cfg) and os.unlink(cfg))
        return cfg, names

    def test_exhaustive_by_default(self):
        cfg, names = self.arms()
        routes = {"/ok": lambda h: support.response(h, 200, support.long_html())}
        with support.serve(routes) as base:
            r, data = support.run_json(
                "matrix", base + "/ok", *OPTS, "--arms", ",".join(names),
                "--arms-file", cfg, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(data["rows"]), 2)
        self.assertEqual({row["client"] for row in data["rows"]}, set(names))

    def test_stop_on_ok_runs_one_arm(self):
        cfg, names = self.arms()
        routes = {"/ok": lambda h: support.response(h, 200, support.long_html())}
        with support.serve(routes) as base:
            r, data = support.run_json(
                "matrix", base + "/ok", *OPTS, "--arms", ",".join(names),
                "--arms-file", cfg, "--stop-on-ok", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(data["rows"]), 1)

    def test_html_offline(self):
        path = support.write_temp(support.long_html(), suffix=".html")
        try:
            r = support.run("matrix", "--html", path, "--selector", "#content")
        finally:
            support.os.unlink(path)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("content\t", r.stdout)

    def test_clients_compat_maps_curl_to_native(self):
        routes = {"/ok": lambda h: support.response(h, 200, support.long_html())}
        with support.serve(routes) as base:
            r, data = support.run_json(
                "matrix", base + "/ok", *OPTS, "--clients", "curl", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([row["client"] for row in data["rows"]], ["native"])


if __name__ == "__main__":
    unittest.main()
