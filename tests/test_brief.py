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


def twelve_route_body():
    groups = [{"route": f"/p{i}", "n": 2,
               "access": {"rate": 0.5}, "complete": {"rate": 0.5}} for i in range(12)]
    groups[0]["access"]["rate"] = None
    summary = {"overall": {"n": 24, "access": {"rate": 0.5, "ci95": [0.3, 0.7]},
                           "complete": {"rate": 0.5, "ci95": [0.3, 0.7]}},
               "groups": groups}
    return brief.compose(ADVICE, summary, None, {"artifacts": {}}, doc_chars)


class TrimTest(unittest.TestCase):
    def test_trim_routes_and_keep_core(self):
        body = twelve_route_body()
        untrimmed = brief.json_chars(body)
        view, dropped, truncated = brief.trim(body, untrimmed - 1)
        self.assertFalse(truncated)
        self.assertTrue(dropped)
        self.assertLessEqual(brief.json_chars(view), untrimmed - 1)
        self.assertEqual(len(view["metrics"]["routes"]), 6)
        self.assertEqual(view["metrics"]["routes"][0]["route"], "/p0")
        self.assertEqual(view["verdict"]["action"], "switch-arm")
        self.assertEqual(view["evidence"]["n"], 8)

    def test_tiny_budget_marks_truncated(self):
        view, dropped, truncated = brief.trim(twelve_route_body(), 200)
        self.assertTrue(truncated)
        self.assertEqual(view["verdict"]["action"], "switch-arm")
        self.assertIn("metrics.routes 省略 6 条", dropped)

    def test_trim_is_deterministic(self):
        body = twelve_route_body()
        first = brief.trim(body, 800)
        second = brief.trim(body, 800)
        self.assertEqual(json.dumps(first, ensure_ascii=False),
                         json.dumps(second, ensure_ascii=False))

    def test_trim_removes_zero_outcomes_and_extra_reads(self):
        view, dropped, _ = brief.trim(twelve_route_body(), 600)
        self.assertNotIn("auth", view["evidence"]["outcomes"])
        self.assertLessEqual(len(view["next_reads"]), 1)
        self.assertTrue(any("outcomes" in item for item in dropped))


class RenderTest(unittest.TestCase):
    def out(self):
        return brief.finalize(twelve_route_body(), 4000)

    def test_text_contains_decisions(self):
        text = brief.render_text(self.out())
        self.assertIn("switch-arm", text)
        self.assertIn("L2 / S2", text)
        self.assertIn("预算:", text)
        self.assertIn("references/strategy.md", text)

    def test_cache_hit_renders_short(self):
        text = brief.render_text({"schema": "brief/1", "command": "brief",
                                  "ok": True, "digest": "sha256:abc",
                                  "cache": {"hit": True}})
        self.assertIn("命中", text)
        self.assertNotIn("switch-arm", text)


def copy_example():
    d = tempfile.mkdtemp(prefix="recon-brief-")
    for name in ("profile.yaml", "evidence.jsonl", "trials.jsonl"):
        src = os.path.join(EXAMPLE_DIR, name)
        if os.path.isfile(src):
            shutil.copy(src, os.path.join(d, name))
    return d


class BriefCliTest(unittest.TestCase):
    def workspace(self, files=None):
        d = copy_example() if files is None else make(files)
        self.addCleanup(shutil.rmtree, d, True)
        return d

    def test_empty_workspace(self):
        r, data = support.run_json("brief", self.workspace({}), "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(data["verdict"]["action"], "collect-more")
        self.assertFalse(data["truncated"])
        self.assertTrue(data["digest"].startswith("sha256:"))
        self.assertIn("缺 profile.yaml：没有闸门图与口径", data["gaps"])

    def test_example_matches_stats_and_routing(self):
        ws = self.workspace()
        r, data = support.run_json("brief", ws, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        _, stats_body = support.run_json(
            "stats", os.path.join(ws, "evidence.jsonl"), "--json")
        self.assertEqual(data["metrics"]["overall"]["access_rate"],
                         stats_body["overall"]["access"]["rate"])
        self.assertEqual(data["metrics"]["overall"]["complete_rate"],
                         stats_body["overall"]["complete"]["rate"])
        self.assertEqual(data["next_reads"][0]["path"], "references/evidence-matrix.md")
        self.assertEqual(data["artifacts"]["profile"], "profile.yaml")

    def test_byte_stable_across_runs(self):
        ws = self.workspace()
        first = support.run("brief", ws, "--json")
        second = support.run("brief", ws, "--json")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, second.stdout)

    def test_row_order_irrelevant(self):
        ws = self.workspace()
        with open(os.path.join(EXAMPLE_DIR, "evidence.jsonl"), encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
        with open(os.path.join(EXAMPLE_DIR, "profile.yaml"), encoding="utf-8") as f:
            profile_text = f.read()
        with open(os.path.join(EXAMPLE_DIR, "trials.jsonl"), encoding="utf-8") as f:
            trials_text = f.read()
        reversed_ws = self.workspace({
            "profile.yaml": profile_text,
            "trials.jsonl": trials_text,
            "evidence.jsonl": "".join(json.dumps(row, ensure_ascii=False) + "\n"
                                      for row in reversed(rows)),
        })
        _, a = support.run_json("brief", ws, "--json")
        _, b = support.run_json("brief", reversed_ws, "--json")
        self.assertEqual(a["digest"], b["digest"])
        self.assertEqual(a["verdict"], b["verdict"])
        self.assertEqual(a["metrics"], b["metrics"])

    def test_semantic_change_changes_digest(self):
        ws = self.workspace()
        _, before = support.run_json("brief", ws, "--json")
        path = os.path.join(ws, "evidence.jsonl")
        with open(path, encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        for row in lines:
            if row.get("outcome") == "ok":
                row["outcome"] = "blocked"
                break
        with open(path, "w", encoding="utf-8") as f:
            f.write("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in lines))
        _, after = support.run_json("brief", ws, "--json")
        self.assertNotEqual(before["digest"], after["digest"])

    def test_volatile_field_ignored(self):
        ws = self.workspace()
        _, before = support.run_json("brief", ws, "--json")
        path = os.path.join(ws, "evidence.jsonl")
        with open(path, encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        lines[0]["elapsed_ms"] = 123456
        with open(path, "w", encoding="utf-8") as f:
            f.write("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in lines))
        _, after = support.run_json("brief", ws, "--json")
        self.assertEqual(before["digest"], after["digest"])

    def test_budget_trimming(self):
        ws = self.workspace()
        _, full = support.run_json("brief", ws, "--json", "--budget", "4000")
        used = full["budget"]["used"]
        _, tight = support.run_json("brief", ws, "--json", "--budget", str(used - 1))
        self.assertFalse(tight["truncated"])
        self.assertLessEqual(tight["budget"]["used"], used - 1)
        self.assertTrue(tight["budget"]["dropped"])
        self.assertEqual(full["digest"], tight["digest"])

    def test_tiny_budget_truncated(self):
        _, data = support.run_json("brief", self.workspace(), "--json", "--budget", "200")
        self.assertTrue(data["truncated"])
        self.assertEqual(data["verdict"]["action"], "collect-more")

    def test_expect_digest_hit(self):
        ws = self.workspace()
        _, full = support.run_json("brief", ws, "--json")
        r, hit = support.run_json("brief", ws, "--json",
                                  "--expect-digest", full["digest"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(hit["cache"]["hit"])
        self.assertNotIn("verdict", hit)
        self.assertLessEqual(len(r.stdout), 200)

    def test_expect_digest_miss(self):
        _, data = support.run_json("brief", self.workspace(), "--json",
                                   "--expect-digest", "sha256:" + "0" * 64)
        self.assertFalse(data["cache"]["hit"])
        self.assertIn("verdict", data)

    def test_bad_inputs(self):
        r = support.run("brief", "/nonexistent-workspace", "--json")
        self.assertEqual(r.returncode, 2)
        ws = self.workspace({"evidence.jsonl": json.dumps(
            {"kind": "trial", "trial_outcome": "ok"}) + "\n"})
        self.assertEqual(support.run("brief", ws, "--json").returncode, 2)
        self.assertEqual(support.run("brief", self.workspace({}), "--budget", "199").returncode, 2)

    def test_profile_without_gates(self):
        with open(os.path.join(EXAMPLE_DIR, "evidence.jsonl"), encoding="utf-8") as f:
            evidence_text = f.read()
        ws = self.workspace({"profile.yaml": "host: example.test\nconfidence: observed\n",
                             "evidence.jsonl": evidence_text})
        r, data = support.run_json("brief", ws, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNone(data["gates"]["first_fail"])
        self.assertEqual(data["gates"]["unknown"], 0)


if __name__ == "__main__":
    unittest.main()
