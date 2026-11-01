import json
import os
import unittest

import support

ROWS = [
    {"route": "/a", "outcome": "ok", "content_len": 800},
    {"route": "/a", "outcome": "ok", "content_len": 900},
    {"route": "/a", "outcome": "degraded", "content_len": 120},
    {"route": "/a", "outcome": "noise", "content_len": 0},
    {"route": "/b", "outcome": "blocked", "content_len": 0},
    {"route": "/b", "outcome": "missing", "content_len": 0},
    {"route": "/b", "outcome": "noise", "content_len": 0},
    {"route": "/b", "outcome": "ok", "content_len": 700},
]


class StatsTest(unittest.TestCase):
    def setUp(self):
        self.path = support.write_temp("\n".join(json.dumps(r) for r in ROWS) + "\n", ".jsonl")

    def test_dual_metrics_split_by_route(self):
        r, data = support.run_json("stats", self.path, "--json")
        self.assertEqual(r.returncode, 0)
        overall = data["overall"]
        self.assertEqual(overall["n"], 8)
        self.assertEqual(overall["noise"], 2)
        self.assertEqual(overall["access"]["num"], 4)
        self.assertEqual(overall["access"]["den"], 6)
        self.assertEqual(overall["access"]["rate"], 0.667)
        self.assertEqual(overall["complete"]["num"], 3)
        self.assertEqual(overall["complete"]["den"], 4)
        self.assertEqual(overall["complete"]["rate"], 0.75)

        groups = {g["route"]: g for g in data["groups"]}
        self.assertEqual(groups["/a"]["access"]["rate"], 1.0)
        self.assertEqual(groups["/a"]["complete"]["rate"], 0.667)
        self.assertEqual(groups["/b"]["access"]["rate"], 0.333)
        self.assertEqual(groups["/b"]["complete"]["rate"], 1.0)

        lo, hi = overall["access"]["ci95"]
        self.assertTrue(0.0 <= lo < 0.667 < hi <= 1.0)

    def test_canary_baseline_is_accepted(self):
        baseline = support.write_temp(json.dumps({
            "schema": "canary-baseline/1",
            "rows": [
                {"outcome": "ok", "content": 800, "final_url": "http://example.test/list/1"},
                {"outcome": "degraded", "content": 40, "final_url": "http://example.test/list/1"},
            ],
        }))
        r, data = support.run_json("stats", baseline, "--json")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(data["groups"][0]["route"], "/list/1")
        self.assertEqual(data["overall"]["access"]["rate"], 1.0)
        self.assertEqual(data["overall"]["complete"]["rate"], 0.5)

    def test_human_output_has_both_metrics(self):
        r = support.run("stats", self.path)
        self.assertEqual(r.returncode, 0)
        self.assertIn("access_rate", r.stdout)
        self.assertIn("complete_rate", r.stdout)
        self.assertIn("ALL", r.stdout)

    def test_unknown_outcome_is_usage_error(self):
        path = support.write_temp('{"route": "/x", "outcome": "blocked-ish"}\n', ".jsonl")
        r = support.run("stats", path)
        self.assertEqual(r.returncode, 2)
        self.assertIn("outcome", r.stderr)

    def test_empty_input_is_zero_rows(self):
        path = support.write_temp("", ".jsonl")
        r, data = support.run_json("stats", path, "--json")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(data["rows"], 0)
        self.assertIsNone(data["overall"]["access"]["rate"])


if __name__ == "__main__":
    unittest.main()
