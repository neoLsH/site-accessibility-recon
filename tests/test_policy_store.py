import json
import os
import shutil
import unittest

import support
from reconlib import policy_store
from reconlib.policy import ContextualBandit

ARMS = ("native", "cffi:chrome")


def temp_json(self):
    path = support.temp_path(".json")
    self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
    return path


class StoreTest(unittest.TestCase):
    def test_roundtrip_restores_state(self):
        bandit = ContextualBandit(ARMS, algorithm="ucb1", min_pulls=0,
                                  prior_strength=2, half_life=15)
        bandit.update("h", "article|content", "native", "ok", cost_ms=12)
        bandit.update("h", "article|content", "cffi:chrome", "blocked", cost_ms=80)
        path = temp_json(self)
        info = policy_store.dump(bandit, path, meta={"command": "test"})
        self.assertEqual(info["contexts"], 1)
        fresh = ContextualBandit(ARMS, algorithm="ucb1", min_pulls=0,
                                 prior_strength=2, half_life=15)
        restored = policy_store.restore(fresh, path)
        self.assertEqual(restored["ignored_arms"], [])
        self.assertEqual(fresh.export_state(), bandit.export_state())
        self.assertEqual(fresh.snapshot("h", "article|content")["arms"],
                         bandit.snapshot("h", "article|content")["arms"])

    def test_unknown_arms_are_ignored(self):
        bandit = ContextualBandit(ARMS, algorithm="fixed", min_pulls=0)
        bandit.update("h", "article|content", "cffi:chrome", "ok", cost_ms=10)
        path = temp_json(self)
        policy_store.dump(bandit, path)
        fresh = ContextualBandit(("native",), algorithm="fixed", min_pulls=0)
        restored = policy_store.restore(fresh, path)
        self.assertEqual(restored["ignored_arms"], ["cffi:chrome"])
        self.assertEqual(set(fresh.export_state()["h|article|content"]["arms"]),
                         {"native"})

    def test_bad_schema_and_state_are_rejected(self):
        path = temp_json(self)
        for payload in ({"schema": "other", "state": {}},
                        {"schema": policy_store.SCHEMA, "state": []}):
            with self.subTest(payload=payload):
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(payload, f)
                with self.assertRaises(ValueError):
                    policy_store.load(path)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"schema": policy_store.SCHEMA,
                       "state": {"h|article": {"arms": {}}}}, f)
        bandit = ContextualBandit(ARMS)
        with self.assertRaises(ValueError):
            policy_store.restore(bandit, path)

    def test_missing_file_is_rejected(self):
        with self.assertRaises(ValueError):
            policy_store.load("/nonexistent/recon-policy.json")

    def test_dump_creates_parent_directory(self):
        root = support.temp_path("")
        self.addCleanup(lambda: os.unlink(root) if os.path.exists(root) else None)
        path = os.path.join(root + ".d", "nested", "policy.json")
        self.addCleanup(lambda: shutil.rmtree(root + ".d", ignore_errors=True))
        bandit = ContextualBandit(ARMS, algorithm="fixed", min_pulls=0)
        info = policy_store.dump(bandit, path)
        self.assertTrue(os.path.isfile(info["path"]))
        self.assertEqual(info["contexts"], 0)

    def test_context_rates_use_local_samples(self):
        bandit = ContextualBandit(ARMS, algorithm="fixed", min_pulls=0)
        bandit.update("h", "article|content", "native", "ok", cost_ms=10)
        bandit.update("h", "article|content", "native", "ok", cost_ms=10)
        bandit.update("h", "article|content", "cffi:chrome", "blocked", cost_ms=10)
        path = temp_json(self)
        policy_store.dump(bandit, path)
        groups = policy_store.context_rates(policy_store.load(path))
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["context"], "h|article|content")
        self.assertEqual(groups[0]["rates"]["native"], 1.0)
        self.assertEqual(groups[0]["rates"]["cffi:chrome"], 0.0)
        self.assertGreaterEqual(groups[0]["pages"], 1)

    def test_context_rates_empty(self):
        bandit = ContextualBandit(ARMS, algorithm="fixed", min_pulls=0)
        path = temp_json(self)
        policy_store.dump(bandit, path)
        with self.assertRaises(ValueError):
            policy_store.context_rates(policy_store.load(path))


if __name__ == "__main__":
    unittest.main()
