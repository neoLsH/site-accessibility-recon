import json
import os
import shutil
import tempfile
import unittest

import support

from reconlib import brief  # support.py 已把 scripts/ 加入 sys.path

EXAMPLE_DIR = os.path.join(support.ROOT, "assets", "example")


def make(files):
    d = tempfile.mkdtemp(prefix="recon-brief-")
    for name, body in files.items():
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            f.write(body)
    return d


class TokenEstimateTest(unittest.TestCase):
    def test_mixed_text(self):
        self.assertEqual(brief.estimate_tokens("abcdefgh"), 2)   # 8/3.5 -> 2.29 -> 2
        self.assertEqual(brief.estimate_tokens("中文四字"), 4)   # 4 CJK -> 4
        self.assertEqual(brief.estimate_tokens(""), 0)

    def test_order_key_ignores_volatile_fields(self):
        row = {"url": "https://a/1", "outcome": "ok", "elapsed_ms": 12}
        other = {"url": "https://a/1", "outcome": "ok", "elapsed_ms": 999}
        self.assertEqual(brief.order_key(row), brief.order_key(other))
        self.assertNotEqual(brief.order_key(row),
                            brief.order_key({**row, "outcome": "blocked"}))


class GatesTest(unittest.TestCase):
    def test_first_fail_follows_document_order(self):
        profile = {"gates": [
            {"name": "网络可达", "layer": "L1", "status": "pass"},
            {"name": "请求语义", "layer": "L3", "status": "fail"},
            {"name": "运行时", "layer": "L4", "status": "fail"},
            {"name": "业务", "layer": "L5", "status": "pass"},
        ]}
        gates = brief.gates_of(profile)
        self.assertEqual(gates["first_fail"], {"name": "请求语义", "layer": "L3"})
        self.assertEqual((gates["pass"], gates["fail"], gates["unknown"]), (2, 2, 0))

    def test_empty_and_unknown(self):
        self.assertEqual(brief.gates_of(None)["first_fail"], None)
        gates = brief.gates_of({"gates": [{"name": "x", "status": "maybe"}]})
        self.assertEqual((gates["pass"], gates["fail"], gates["unknown"]), (0, 0, 1))


class GapsTest(unittest.TestCase):
    def test_missing_profile(self):
        self.assertEqual(brief.gaps_of(None, {"n": 8}),
                         ["缺 profile.yaml：没有闸门图与口径"])

    def test_profile_gaps(self):
        profile = {
            "metrics": {"baseline": None},
            "gates": [{"name": "请求语义", "status": "fail", "evidence": ""}],
            "plan": {},
        }
        gaps = brief.gaps_of(profile, {"n": 8})
        self.assertEqual(gaps, ["metrics.baseline 未测", "「请求语义」门无证据", "plan.level 未推导"])

    def test_no_evidence(self):
        gaps = brief.gaps_of({"metrics": {"baseline": 1}, "plan": {"level": "S1"}}, {"n": 0})
        self.assertEqual(gaps, ["没有证据行：任何结论都是假设"])


ADVICE = {
    "evidence": {
        "n": 8, "routes": 3,
        "outcomes": {"ok": 4, "degraded": 2, "blocked": 1,
                     "auth": 0, "missing": 0, "noise": 1},
        "noise_excluded_n": 7,
        "arms": ["native", "cffi:chrome"],
        "contexts": {"b.example|list|content": 2, "a.example|article|challenge": 6},
    },
    "findings": [{"code": "blocked", "count": 1, "message": "出现被拦截样本"}],
    "next": {
        "action": "switch-arm", "layer": "L2", "countermeasure": "S2",
        "why": "有臂成功、有臂被拦", "confidence": "assumed",
        "commands": ["recon fetch <url> --arms cffi:chrome"],
        "stop": ["两臂各 3 次仍无差异就停"],
    },
}

SUMMARY = {
    "overall": {"n": 8, "access": {"rate": 0.857, "ci95": [0.487, 0.974]},
                "complete": {"rate": 0.667, "ci95": [0.3, 0.903]}},
    "groups": [
        {"route": "/article/1", "n": 3, "access": {"rate": 1.0}, "complete": {"rate": 1.0}},
        {"route": "/article/2", "n": 3, "access": {"rate": 1.0}, "complete": {"rate": 0.333}},
        {"route": "/article/3", "n": 2, "access": {"rate": 0.0}, "complete": {"rate": None}},
    ],
}


def doc_chars(name):
    return 8100 if name == "strategy.md" else (2990 if name == "arms.md" else 0)


class ComposeTest(unittest.TestCase):
    def compose(self, profile=None, extras=None):
        return brief.compose(
            ADVICE, SUMMARY, profile,
            extras or {"artifacts": {"profile": "profile.yaml", "evidence": "evidence.jsonl"}},
            doc_chars)

    def test_routes_worst_first_and_null_is_worst(self):
        body = self.compose()
        self.assertEqual([r["route"] for r in body["metrics"]["routes"]],
                         ["/article/3", "/article/2", "/article/1"])
        self.assertEqual(body["metrics"]["routes"][0]["complete_rate"], None)

    def test_verdict_is_verbatim_advice(self):
        self.assertEqual(self.compose()["verdict"]["action"], "switch-arm")
        self.assertEqual(self.compose()["verdict"]["confidence"], "assumed")
        self.assertEqual(self.compose()["verdict"]["commands"],
                         ["recon fetch <url> --arms cffi:chrome"])

    def test_evidence_sorted_for_order_independence(self):
        ev = self.compose()["evidence"]
        self.assertEqual(ev["arms"], ["cffi:chrome", "native"])
        self.assertEqual(list(ev["contexts"]),
                         ["a.example|article|challenge", "b.example|list|content"])

    def test_next_reads_follow_action_and_cost(self):
        reads = self.compose()["next_reads"]
        self.assertEqual([r["path"] for r in reads],
                         ["references/strategy.md", "references/arms.md"])
        self.assertEqual([r["chars"] for r in reads], [8100, 2990])

    def test_unknown_action_yields_no_reads(self):
        advice_body = json.loads(json.dumps(ADVICE))
        advice_body["next"]["action"] = "brand-new-action"
        body = brief.compose(advice_body, SUMMARY, None, {"artifacts": {}}, doc_chars)
        self.assertEqual(body["next_reads"], [])

    def test_metrics_overall_shape(self):
        overall = self.compose()["metrics"]["overall"]
        self.assertEqual(overall, {"n": 8, "access_rate": 0.857,
                                   "access_ci": [0.487, 0.974],
                                   "complete_rate": 0.667,
                                   "complete_ci": [0.3, 0.903]})


class DigestTest(unittest.TestCase):
    def body(self):
        return brief.compose(ADVICE, SUMMARY, {"gates": []},
                             {"artifacts": {"profile": "profile.yaml"}}, doc_chars)

    def test_digest_format_and_stability(self):
        first = brief.digest_of(self.body())
        second = brief.digest_of(self.body())
        self.assertTrue(first.startswith("sha256:"))
        self.assertEqual(len(first), len("sha256:") + 64)
        self.assertEqual(first, second)

    def test_semantics_change_digest(self):
        changed = self.body()
        changed["evidence"]["outcomes"]["ok"] = 3
        self.assertNotEqual(brief.digest_of(self.body()), brief.digest_of(changed))

    def test_budget_does_not_change_digest(self):
        body = self.body()
        small = brief.finalize(body, 700)
        big = brief.finalize(body, 4000)
        self.assertEqual(small["digest"], big["digest"])

    def test_finalize_shape(self):
        out = brief.finalize(self.body(), 4000)
        self.assertEqual(out["schema"], "brief/1")
        self.assertEqual(out["command"], "brief")
        self.assertTrue(out["ok"])
        self.assertEqual(out["budget"]["limit"], 4000)
        self.assertEqual(out["budget"]["dropped"], [])
        self.assertFalse(out["truncated"])
        self.assertGreater(out["budget"]["tokens_est"], 0)


if __name__ == "__main__":
    unittest.main()
