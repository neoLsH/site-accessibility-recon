import unittest

import support
from reconlib import advice


def row(outcome, route="/a", arm="native", waf_no=0, url="https://example.invalid/a"):
    return {"route": route, "final_url": url, "arm": arm, "outcome": outcome,
            "content_len": 300 if outcome == "ok" else 0,
            "status": 200, "waf_no": waf_no,
            "attributable": outcome in ("ok", "degraded", "blocked")}


class RecommendTest(unittest.TestCase):
    def action(self, rows):
        return advice.recommend(rows)["next"]["action"]

    def test_empty(self):
        body = advice.recommend([])
        self.assertEqual(body["next"]["action"], "collect-more")
        self.assertEqual(body["findings"][0]["code"], "no-evidence")

    def test_noise_only(self):
        self.assertEqual(self.action([row("noise"), row("noise")]), "separate-noise")

    def test_all_ok(self):
        self.assertEqual(self.action([row("ok"), row("ok")]), "maintain")

    def test_auth(self):
        self.assertEqual(self.action([row("auth"), row("auth")]), "check-authorization")

    def test_missing(self):
        self.assertEqual(self.action([row("missing"), row("missing")]), "check-routing")

    def test_degraded(self):
        self.assertEqual(self.action([row("degraded"), row("degraded")]), "check-extraction")

    def test_rate_limited(self):
        self.assertEqual(self.action([row("blocked", waf_no=6), row("blocked", waf_no=6)]),
                         "backoff")

    def test_switch_arm(self):
        body = advice.recommend([row("ok", arm="cffi:chrome"),
                                 row("blocked", arm="native")])
        self.assertEqual(body["next"]["action"], "switch-arm")
        joined = " ".join(body["next"]["commands"])
        self.assertIn("cffi:chrome", joined)
        self.assertIn("native", joined)

    def test_differentiate(self):
        body = advice.recommend([row("blocked"), row("blocked")])
        self.assertEqual(body["next"]["action"], "differentiate")
        self.assertIn("stop", body["next"])

    def test_mixed_needs_more_samples(self):
        rows = [row("ok"), row("auth"), row("missing"), row("blocked"), row("degraded")]
        self.assertEqual(self.action(rows), "collect-more")

    def test_noise_excluded_from_denominator(self):
        body = advice.recommend([row("ok"), row("ok"), row("noise")])
        self.assertEqual(body["evidence"]["noise_excluded_n"], 2)
        self.assertEqual(body["next"]["action"], "maintain")

    def test_routes_and_arms(self):
        body = advice.recommend([row("ok", route="/a"), row("ok", route="/b", arm="cffi:chrome")])
        self.assertEqual(body["evidence"]["routes"], 2)
        self.assertEqual(body["evidence"]["arms"], ["native", "cffi:chrome"])

    def test_rejects_trial_and_unknown(self):
        with self.assertRaises(ValueError):
            advice.recommend([{"kind": "trial", "trial_outcome": "ok"}])
        with self.assertRaises(ValueError):
            advice.recommend([{"route": "/a", "outcome": "banana"}])


if __name__ == "__main__":
    unittest.main()
