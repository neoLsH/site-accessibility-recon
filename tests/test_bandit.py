import json
import os
import unittest

import support

BASE = ("--pages", "40", "--seed", "7", "--json")


class BanditCliTest(unittest.TestCase):
    def test_arms_run(self):
        r, data = support.run_json("bandit", "--arms", "native=0.3,cffi:chrome=0.8", *BASE)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(data["command"], "bandit")
        self.assertTrue(data["ok"])
        self.assertEqual(data["source"], "arms")
        self.assertEqual(data["rates"], {"native": 0.3, "cffi:chrome": 0.8})
        policies = [row["policy"] for row in data["rows"]]
        self.assertEqual(policies, ["thompson", "ucb1", "fixed", "round_robin", "best-single"])
        self.assertEqual(data["rows"][-1]["arm"], "cffi:chrome")

    def test_same_seed_same_rows(self):
        args = ("bandit", "--arms", "native=0.4,cffi:chrome=0.6", *BASE)
        _, a = support.run_json(*args)
        _, b = support.run_json(*args)
        self.assertEqual(a["rows"], b["rows"])

    def test_policy_filter(self):
        r, data = support.run_json("bandit", "--arms", "native=0.5",
                                   "--policies", "thompson", *BASE)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([row["policy"] for row in data["rows"]],
                         ["thompson", "best-single"])

    def test_trials_input(self):
        path = support.temp_path(".jsonl")
        try:
            with open(path, "w", encoding="utf-8") as f:
                for row in (
                    {"kind": "trial", "arm": "native", "trial_outcome": "ok"},
                    {"kind": "trial", "arm": "native", "trial_outcome": "blocked"},
                    {"kind": "trial", "arm": "cffi:chrome", "trial_outcome": "ok"},
                    {"kind": "trial", "arm": "cffi:chrome", "trial_outcome": "ok"},
                ):
                    f.write(json.dumps(row) + "\n")
            r, data = support.run_json("bandit", "--trials", path, *BASE)
        finally:
            os.unlink(path)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(data["source"], "trials")
        self.assertEqual(data["rates"], {"native": 0.5, "cffi:chrome": 1.0})

    def test_trials_without_samples_is_usage_error(self):
        path = support.temp_path(".jsonl")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(json.dumps({"kind": "trial", "arm": "native",
                                    "trial_outcome": "noise"}) + "\n")
            r = support.run("bandit", "--trials", path, "--pages", "40", "--seed", "7")
        finally:
            os.unlink(path)
        self.assertEqual(r.returncode, 2)
        self.assertIn("胜率样本", r.stderr)

    def test_requires_exactly_one_source(self):
        r = support.run("bandit", *BASE)
        self.assertEqual(r.returncode, 2)
        r = support.run("bandit", "--arms", "native=0.5", "--trials", "x.jsonl", *BASE)
        self.assertEqual(r.returncode, 2)

    def test_invalid_inputs_are_usage_errors(self):
        for args in (("--arms", "native"),
                     ("--arms", "native=2"),
                     ("--arms", "browser=0.5"),
                     ("--arms", "native=0.5", "--policies", "magic")):
            with self.subTest(args=args):
                r = support.run("bandit", *args, *BASE)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_rates_json_context_groups(self):
        path = support.write_temp(json.dumps({
            "contexts": [
                {"context": "article|unknown", "pages": 40,
                 "rates": {"native": 0.4, "cffi:chrome": 0.75}},
                {"context": "list|unknown", "pages": 40,
                 "rates": {"native": 0.9, "cffi:chrome": 0.8}},
            ]}))
        try:
            r, data = support.run_json("bandit", "--rates-json", path,
                                       "--policies", "thompson",
                                       "--pages", "80", "--json")
        finally:
            support.os.unlink(path)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("contexts", data)
        self.assertEqual(len(data["contexts"]), 2)
        self.assertEqual(data["rows"][0]["policy"], "thompson")

    def test_rates_json_validation(self):
        for payload in ({"contexts": []},
                        {"contexts": [{"context": "", "rates": {"native": 0.5}}]},
                        {"contexts": [{"context": "a", "rates": {}}]},
                        {"contexts": [{"context": "a", "rates": {"native": 2}}]}):
            with self.subTest(payload=payload):
                path = support.write_temp(json.dumps(payload))
                try:
                    r = support.run("bandit", "--rates-json", path,
                                    "--pages", "10", "--seed", "7")
                finally:
                    os.unlink(path)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_table_output(self):
        r = support.run("bandit", "--arms", "native=0.2,cffi:chrome=0.9",
                        "--pages", "20", "--seed", "7")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("best-single", r.stdout)
        self.assertIn("thompson", r.stdout)


if __name__ == "__main__":
    unittest.main()
