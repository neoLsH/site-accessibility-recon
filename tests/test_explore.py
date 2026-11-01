import json
import os
import unittest

import support
from reconlib import explore, policy, policy_store


def trial(arm, outcome, ctx="h.example|article|challenge", url=None, **extra):
    row = {"kind": "trial", "arm": arm, "trial_outcome": outcome,
           "rate_limited": False}
    if ctx:
        row["context"] = ctx
    if url:
        row["url"] = url
    row.update(extra)
    return row


class HelpersTest(unittest.TestCase):
    def test_parse_arms_rejects_rates(self):
        self.assertEqual(explore.parse_arms("native, cffi:chrome,native"),
                         ["native", "cffi:chrome"])
        with self.assertRaises(ValueError):
            explore.parse_arms("native=0.4")

    def test_context_derived_from_url_uses_route_class(self):
        self.assertEqual(
            explore._context_of({"url": "https://h.example/article/1"}),
            "h.example|article|unknown")
        self.assertEqual(
            explore._context_of({"url": "https://h.example/list?p=2"}),
            "h.example|list|unknown")
        self.assertIsNone(explore._context_of({"route": "/a"}))


class ReplayTest(unittest.TestCase):
    def test_filters_non_attributable_and_rate_limited(self):
        rows = [
            trial("native", "ok"),
            trial("native", "noise"),
            trial("native", "auth"),
            trial("native", "blocked", rate_limited=True),
            trial("cffi:chrome", "degraded"),
        ]
        bandit, pages, observed = explore.replay(rows)
        self.assertEqual(observed["attempts"], 2)
        self.assertEqual(observed["skipped"], 3)
        snap = bandit.snapshot("h.example", "article|challenge")
        pulls = {r["arm"]: r["pulls"] for r in snap["arms"]}
        self.assertEqual(pulls["native"], 1)
        self.assertEqual(pulls["cffi:chrome"], 1)
        self.assertEqual(pages, {})

    def test_pages_count_unique_urls(self):
        rows = [
            trial("native", "blocked", url="https://h.example/article/1"),
            trial("cffi:chrome", "ok", url="https://h.example/article/1"),
            trial("native", "ok", url="https://h.example/article/2"),
        ]
        _, pages, _ = explore.replay(rows)
        self.assertEqual(pages["h.example|article|challenge"], 2)

    def test_no_usable_rows(self):
        with self.assertRaises(ValueError):
            explore.replay([trial("native", "noise")])


class PlanTest(unittest.TestCase):
    def bandit(self, rounds=5):
        b = policy.ContextualBandit(["native", "cffi:chrome"], seed=3)
        for _ in range(rounds):
            b.update("h.example", "article|challenge", "native", "blocked")
            b.update("h.example", "article|challenge", "cffi:chrome", "ok")
        return b

    def test_unseen_arm_gets_probes_and_budget_is_respected(self):
        b = policy.ContextualBandit(["native", "cffi:chrome", "cffi:firefox"], seed=3)
        for _ in range(5):
            b.update("h.example", "article|challenge", "native", "blocked")
            b.update("h.example", "article|challenge", "cffi:chrome", "ok")
        body = explore.plan(b, {"h.example|article|challenge": 10},
                            {"attempts": 10, "successes": 5}, budget=3,
                            max_per_context=3)
        self.assertEqual(body["schema"], explore.SCHEMA)
        self.assertLessEqual(body["allocated"], 3)
        self.assertEqual(body["allocated"], sum(s["requests"] for s in body["steps"]))
        self.assertIn("cffi:firefox", body["unseen_arms"])
        step = next(s for s in body["steps"] if s["arm"] == "cffi:firefox")
        self.assertEqual(step["kind"], "unseen")
        self.assertGreaterEqual(step["requests"], 1)
        self.assertTrue(step["why"])

    def test_max_per_context_caps_allocation(self):
        b = self.bandit()
        body = explore.plan(b, {"h.example|article|challenge": 10},
                            {"attempts": 10, "successes": 5}, budget=20,
                            max_per_context=2)
        self.assertLessEqual(sum(s["requests"] for s in body["steps"]), 2)
        self.assertGreaterEqual(body["remaining"], 18)

    def test_warmup_below_target(self):
        b = policy.ContextualBandit(["native", "cffi:chrome"], seed=3)
        b.update("h.example", "list|content", "native", "ok")
        b.update("h.example", "list|content", "cffi:chrome", "blocked")
        body = explore.plan(b, {"h.example|list|content": 2},
                            {"attempts": 2, "successes": 1}, budget=10,
                            target_pulls=4)
        kinds = {s["kind"] for s in body["steps"]}
        self.assertIn("warmup", kinds)
        warm = next(s for s in body["steps"] if s["kind"] == "warmup")
        self.assertGreaterEqual(warm["requests"], 1)

    def test_exploit_context_goes_to_hold(self):
        b = policy.ContextualBandit(["native", "cffi:chrome"], seed=3)
        for _ in range(10):
            b.update("h.example", "article|content", "native", "ok")
            b.update("h.example", "article|content", "cffi:chrome", "blocked")
        body = explore.plan(b, {"h.example|article|content": 10},
                            {"attempts": 20, "successes": 10}, budget=5)
        self.assertEqual(body["steps"], [])
        self.assertEqual(body["hold"][0]["context"], "h.example|article|content")
        self.assertEqual(body["hold"][0]["arm"], "native")

    def test_deterministic(self):
        b = self.bandit()
        pages = {"h.example|article|challenge": 10}
        a = explore.plan(b, pages, {"attempts": 10, "successes": 5}, budget=5)
        c = explore.plan(b, pages, {"attempts": 10, "successes": 5}, budget=5)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(c, sort_keys=True))


class ExploreCliTest(unittest.TestCase):
    def test_example_trials(self):
        r, data = support.run_json(
            "explore", "--trials", os.path.join(support.ROOT, "assets", "example", "trials.jsonl"),
            "--arms", "native,cffi:chrome,cffi:firefox", "--budget", "8", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(data["command"], "explore")
        self.assertEqual(data["schema"], "recon-explore/1")
        self.assertEqual(data["source"], "trials")
        self.assertLessEqual(data["allocated"], 8)
        self.assertIn("cffi:firefox", data["unseen_arms"])
        self.assertEqual(len(data["contexts"]), 1)
        self.assertTrue(data["stop"])

    def test_policy_artifact_source(self):
        b = policy.ContextualBandit(["native", "cffi:chrome"], seed=3)
        for _ in range(5):
            b.update("h.example", "article|challenge", "native", "blocked")
            b.update("h.example", "article|challenge", "cffi:chrome", "ok")
        path = support.temp_path(".json")
        try:
            policy_store.dump(b, path, meta={"command": "test"})
            r, data = support.run_json(
                "explore", "--policy-in", path, "--arms", "browser:chromium",
                "--budget", "4", "--json")
        finally:
            os.unlink(path)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(data["source"], "policy")
        self.assertIn("browser:chromium", data["arms"])
        self.assertIn("browser:chromium", data["unseen_arms"])

    def test_sources_are_exclusive(self):
        path = support.temp_path(".json")
        try:
            r = support.run("explore", "--trials", path, "--policy-in", path)
        finally:
            os.unlink(path)
        self.assertEqual(r.returncode, 2)

    def test_bad_budget_is_usage_error(self):
        r = support.run("explore", "--trials",
                        os.path.join(support.ROOT, "assets", "example", "trials.jsonl"),
                        "--budget", "0")
        self.assertEqual(r.returncode, 2)
        self.assertIn("budget", r.stderr)

    def test_no_usable_rows_is_usage_error(self):
        path = support.write_temp(json.dumps(trial("native", "noise")) + "\n", ".jsonl")
        try:
            r = support.run("explore", "--trials", path)
        finally:
            os.unlink(path)
        self.assertEqual(r.returncode, 2)
        self.assertIn("可归因", r.stderr)

    def test_text_output(self):
        r = support.run("explore", "--trials",
                        os.path.join(support.ROOT, "assets", "example", "trials.jsonl"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("预算", r.stdout)
        self.assertIn("停止条件", r.stdout)


if __name__ == "__main__":
    unittest.main()
