import json
import os
import unittest

import support
from reconlib.evidence import (append_jsonl, evidence_row, read_jsonl,
                               route_of, trial_row, write_jsonl)


def cls(outcome="ok", no=0, attributable=True, rate_limited=False):
    return {"outcome": outcome, "waf_no": no, "waf_class": "x",
            "evidence": [], "attributable": attributable, "rate_limited": rate_limited}


def obs(**kw):
    base = {"arm": "native", "status": 200, "final_url": "https://example.test/a/1?q=2",
            "content": 321, "elapsed_ms": 42}
    base.update(kw)
    return base


class EvidenceRowTest(unittest.TestCase):
    def test_route_and_contract(self):
        row = evidence_row(obs(), cls())
        self.assertEqual(row["route"], "/a/1")
        self.assertEqual(row["outcome"], "ok")
        self.assertEqual(row["content_len"], 321)
        self.assertEqual(row["arm"], "native")
        self.assertEqual(row["final_url"], "https://example.test/a/1?q=2")

    def test_route_override(self):
        row = evidence_row(obs(), cls(), route="/fixed")
        self.assertEqual(row["route"], "/fixed")
        self.assertEqual(route_of("https://example.test"), "/")

    def test_non_integer_content_becomes_null(self):
        row = evidence_row(obs(content="missing"), cls(outcome="degraded"))
        self.assertIsNone(row["content_len"])

    def test_invalid_outcome_raises(self):
        with self.assertRaises(ValueError):
            evidence_row(obs(), cls(outcome="weird"))


class TrialRowTest(unittest.TestCase):
    def test_trial_does_not_carry_outcome(self):
        row = trial_row("https://example.test/a", "cffi:chrome",
                        cls(outcome="blocked", no=3), attempt=2, elapsed_ms=88)
        self.assertEqual(row["kind"], "trial")
        self.assertEqual(row["trial_outcome"], "blocked")
        self.assertNotIn("outcome", row)
        self.assertEqual(row["attempt"], 2)

    def test_stats_rejects_trial_log(self):
        path = support.temp_path(".jsonl")
        write_jsonl(path, [trial_row("https://example.test/a", "native", cls())])
        r = support.run("stats", path)
        self.assertEqual(r.returncode, 2)
        self.assertIn("试验行", r.stderr)
        os.unlink(path)


class JsonlTest(unittest.TestCase):
    def test_write_append_and_read(self):
        path = support.temp_path(".jsonl")
        write_jsonl(path, [{"a": 1}])
        append_jsonl(path, [{"a": 2}])
        with open(path, encoding="utf-8") as f:
            self.assertEqual(len(f.read().splitlines()), 2)
        self.assertEqual(read_jsonl(path), [{"a": 1}, {"a": 2}])
        os.unlink(path)

    def test_bad_line_reports_position(self):
        path = support.temp_path(".jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"a": 1}\nnot-json\n')
        with self.assertRaises(ValueError) as ctx:
            read_jsonl(path)
        self.assertIn(":2", str(ctx.exception))
        os.unlink(path)


if __name__ == "__main__":
    unittest.main()
