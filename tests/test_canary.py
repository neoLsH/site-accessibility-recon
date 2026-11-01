import json
import os
import unittest

import support

PASSES = ("--passes", "2", "--delay", "2", "--timeout", "10")


class CanaryTest(unittest.TestCase):
    def test_deterministic_pass(self):
        with support.serve({"/ok": lambda h: support.response(h, 200, support.long_html())}) as base:
            r, data = support.run_json("canary", base + "/ok", *PASSES, "--json")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(data["verdict"], "deterministic-pass")
        self.assertEqual(data["noise_passes"], 0)

    def test_probability_gate(self):
        state = {"n": 0}

        def gate(h):
            state["n"] += 1
            if state["n"] % 2:
                support.response(h, 200, support.long_html())
            else:
                support.response(h, 403, "<html><body>请完成安全验证</body></html>")

        with support.serve({"/gate": gate}) as base:
            r, data = support.run_json("canary", base + "/gate", *PASSES, "--json")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(data["verdict"], "probability-gate")
        self.assertEqual(data["flip_count"], 1)
        self.assertEqual(data["flip_rate"], 1.0)

    def test_noise_only_is_not_blocked(self):
        with support.serve({"/noise": lambda h: support.response(h, 503, "upstream error")}) as base:
            r, data = support.run_json("canary", base + "/noise", *PASSES, "--json")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(data["verdict"], "unstable-infra")
        self.assertEqual(data["noise_passes"], 2)

    def test_degraded(self):
        with support.serve({"/short": lambda h: support.response(h, 200, "<p>加载中</p>")}) as base:
            r, data = support.run_json("canary", base + "/short", *PASSES, "--json")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(data["verdict"], "deterministic-degraded")

    def test_save_and_compare(self):
        state = {"n": 0}

        def gate(h):
            state["n"] += 1
            if state["n"] % 2:
                support.response(h, 200, support.long_html())
            else:
                support.response(h, 403, "<html><body>请完成安全验证</body></html>")

        baseline = support.temp_path(".json")
        routes = {"/ok": lambda h: support.response(h, 200, support.long_html())}
        with support.serve(routes) as base:
            save = support.run("canary", base + "/ok", *PASSES, "--save", baseline)
            self.assertEqual(save.returncode, 0, save.stderr)
            with open(baseline, encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(saved["schema"], "canary-baseline/1")
            self.assertIn("rows", saved)

            same, data = support.run_json("canary", base + "/ok", *PASSES,
                                          "--compare", baseline, "--json")
            self.assertEqual(same.returncode, 0)
            self.assertFalse(data["compare"]["regressed"])

        with support.serve({"/gate": gate}) as base:
            worse, data = support.run_json("canary", base + "/gate", *PASSES,
                                           "--compare", baseline, "--json")
        self.assertEqual(worse.returncode, 1)
        self.assertTrue(data["compare"]["regressed"])
        self.assertTrue(data["compare"]["verdict_changed"])

    def test_save_and_compare_are_mutually_exclusive(self):
        r = support.run("canary", "http://127.0.0.1:9/x", *PASSES,
                        "--save", support.temp_path(".json"),
                        "--compare", support.temp_path(".json"))
        self.assertEqual(r.returncode, 2)

    def test_missing_baseline_is_usage_error(self):
        r = support.run("canary", "http://127.0.0.1:9/x", *PASSES,
                        "--compare", os.path.join(support.ROOT, "no-such-baseline.json"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("基线", r.stderr)


if __name__ == "__main__":
    unittest.main()
