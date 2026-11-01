import json
import os
import unittest

import support

EXAMPLE = os.path.join(support.ROOT, "assets", "example", "evidence.jsonl")


class AdviseCliTest(unittest.TestCase):
    def test_example(self):
        r, data = support.run_json("advise", EXAMPLE, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(data["command"], "advise")
        self.assertEqual(data["schema"], "advise/1")
        self.assertTrue(data["ok"])
        self.assertEqual(data["evidence"]["n"], 8)
        self.assertEqual(data["next"]["action"], "collect-more")

    def test_route_filter(self):
        r, data = support.run_json("advise", EXAMPLE, "--route", "/article/1", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(data["route"], "/article/1")
        self.assertEqual(data["next"]["action"], "maintain")

    def test_unknown_route_is_usage_error(self):
        r = support.run("advise", EXAMPLE, "--route", "/nope")
        self.assertEqual(r.returncode, 2)
        self.assertIn("没有路由", r.stderr)

    def test_trial_rows_rejected(self):
        path = support.temp_path(".jsonl")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(json.dumps({"kind": "trial", "trial_outcome": "ok"}) + "\n")
            r = support.run("advise", path)
        finally:
            os.unlink(path)
        self.assertEqual(r.returncode, 2)
        self.assertIn("试验行", r.stderr)

    def test_table_output(self):
        r = support.run("advise", EXAMPLE)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("下一步", r.stdout)
        self.assertIn("停止条件", r.stdout)


if __name__ == "__main__":
    unittest.main()
