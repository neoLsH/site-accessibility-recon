import os
import unittest

import support

TEMPLATE = os.path.join(support.ROOT, "assets", "site-profile.template.yaml")


class PlanTest(unittest.TestCase):
    def test_template_has_no_errors_and_derives_s2(self):
        r, data = support.run_json("plan", TEMPLATE, "--json")
        self.assertEqual(r.returncode, 0, data["lint"]["errors"])
        self.assertEqual(data["lint"]["errors"], [])
        self.assertEqual(data["blocking"]["rung"], "S2")
        self.assertEqual(len(data["steps"]), 5)
        self.assertTrue(data["steps"][2]["command"].startswith("recon matrix"))

    def test_broken_profile_reports_errors(self):
        with open(TEMPLATE, encoding="utf-8") as f:
            body = f.read()
        body = body.replace("confidence: observed", "confidence: mystery")
        body = body.replace("last_verified: 2026-01-01", "last_verified: not-a-date")
        body = body.replace('pattern: "^/path/"', 'pattern: "["')
        body = body.replace("status: assumed", "status: fail")
        path = support.write_temp(body, ".yaml")

        r, data = support.run_json("plan", path, "--json")
        self.assertEqual(r.returncode, 1)
        paths = {e["path"] for e in data["lint"]["errors"]}
        for expected in ("confidence", "last_verified", "routes[0].pattern",
                         "gates[0].evidence"):
            self.assertIn(expected, paths)

    def test_cookie_pair_under_cookies_is_rejected(self):
        with open(TEMPLATE, encoding="utf-8") as f:
            body = f.read()
        body += "\ncookies:\n  - sessionid=abc123\n"
        path = support.write_temp(body, ".yaml")
        r, data = support.run_json("plan", path, "--json")
        self.assertEqual(r.returncode, 1)
        self.assertTrue(any("Cookie" in e["message"] for e in data["lint"]["errors"]))

    def test_missing_profile_is_usage_error(self):
        r = support.run("plan", os.path.join(support.ROOT, "no-such-profile.yaml"))
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
