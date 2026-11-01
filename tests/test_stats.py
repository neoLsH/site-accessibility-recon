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


def rows(route, **counts):
    out = []
    for outcome, n in counts.items():
        out.extend({"route": route, "outcome": outcome, "content_len": 100} for _ in range(n))
    return out


def write_rows(items):
    return support.write_temp("\n".join(json.dumps(r) for r in items) + "\n", ".jsonl")


class StatsCompareTest(unittest.TestCase):
    def compare(self, before, after, *extra):
        r, data = support.run_json("stats", write_rows(before), "--compare",
                                   write_rows(after), "--json", *extra)
        return r, data

    def test_improved_and_insufficient(self):
        before = rows("/a", ok=5, blocked=5) + rows("/b", ok=1, blocked=1)
        after = rows("/a", ok=9, blocked=1) + rows("/b", ok=2)
        r, data = self.compare(before, after)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(data["mode"], "compare")
        self.assertEqual(data["compare"]["overall"]["access"]["status"], "improved")
        routes = {g["route"]: g for g in data["compare"]["routes"]}
        self.assertEqual(routes["/a"]["access"]["status"], "improved")
        self.assertEqual(routes["/a"]["access"]["delta"], 0.4)
        self.assertEqual(routes["/b"]["access"]["status"], "insufficient")
        self.assertIn("下限", routes["/b"]["access"]["reason"])

    def test_regression_sets_exit_code(self):
        before = rows("/a", ok=9, blocked=1)
        after = rows("/a", ok=5, blocked=5)
        r, data = self.compare(before, after)
        self.assertEqual(r.returncode, 1)
        self.assertFalse(data["ok"])
        self.assertTrue(data["compare"]["regressed"])
        self.assertEqual(data["compare"]["regressed_routes"], ["/a"])

    def test_route_set_changes_are_listed(self):
        before = rows("/a", ok=6, blocked=4) + rows("/b", ok=6, blocked=4)
        after = rows("/a", ok=6, blocked=4) + rows("/c", ok=6, blocked=4)
        r, data = self.compare(before, after)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(data["compare"]["only_before"], ["/b"])
        self.assertEqual(data["compare"]["only_after"], ["/c"])

    def test_min_den_override_is_explicit(self):
        before = rows("/b", ok=1, blocked=1)
        after = rows("/b", ok=3)
        _, strict = self.compare(before, after)
        _, loose = self.compare(before, after, "--min-den", "2")
        self.assertEqual(strict["compare"]["routes"][0]["access"]["status"], "insufficient")
        self.assertNotEqual(loose["compare"]["routes"][0]["access"]["status"], "insufficient")

    def test_zero_denominator_is_insufficient(self):
        before = rows("/z", blocked=3)
        after = rows("/z", ok=3)
        _, data = self.compare(before, after)
        complete = data["compare"]["routes"][0]["complete"]
        self.assertEqual(complete["status"], "insufficient")
        self.assertIn("分母为 0", complete["reason"])

    def test_human_output_has_both_tables(self):
        before = rows("/a", ok=5, blocked=5)
        after = rows("/a", ok=9, blocked=1)
        r = support.run("stats", write_rows(before), "--compare", write_rows(after))
        self.assertEqual(r.returncode, 0)
        self.assertIn("改前 -> 改后", r.stdout)
        self.assertIn("可访问性成功率", r.stdout)
        self.assertIn("内容完整率", r.stdout)
        self.assertIn("Newcombe", r.stdout)
        self.assertIn("回归: 无", r.stdout)


if __name__ == "__main__":
    unittest.main()
