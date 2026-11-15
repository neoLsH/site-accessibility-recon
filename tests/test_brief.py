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


if __name__ == "__main__":
    unittest.main()
