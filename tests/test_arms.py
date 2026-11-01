import json
import os
import tempfile
import unittest

import support
from reconlib import arms


def write_cfg(text):
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


class RegistryTest(unittest.TestCase):
    def test_builtin_native_and_unconfigured_browser(self):
        reg = arms.load(write_cfg('{"arms": []}'))
        self.assertTrue(reg.get("native").available())
        browser = reg.get("browser:chromium")
        self.assertFalse(browser.available())
        self.assertIn("arms.json", browser.unavailable_reason())

    def test_external_override_makes_browser_available(self):
        cfg = write_cfg(json.dumps({"arms": [{
            "name": "browser:chromium", "transport": "cmd",
            "command": ["render", "{url}"], "capabilities": ["tls", "js"]}]}))
        reg = arms.load(cfg)
        arm = reg.get("browser:chromium")
        self.assertTrue(arm.available())
        self.assertEqual(arm.command, ["render", "{url}"])
        self.assertEqual(set(arm.capabilities), {"tls", "js"})

    def test_http_endpoint_with_userinfo_is_rejected(self):
        cfg = write_cfg(json.dumps({"arms": [{
            "name": "browser:webkit", "transport": "http",
            "endpoint": "http://user:pass@127.0.0.1:9/render",
            "capabilities": ["js"]}]}))
        with self.assertRaises(ValueError):
            arms.load(cfg)

    def test_cmd_requires_url_placeholder(self):
        cfg = write_cfg(json.dumps({"arms": [{
            "name": "browser:firefox", "transport": "cmd",
            "command": ["render"], "capabilities": ["js"]}]}))
        with self.assertRaises(ValueError):
            arms.load(cfg)

    def test_duplicate_external_name_is_rejected(self):
        cfg = write_cfg(json.dumps({"arms": [
            {"name": "browser:chromium", "transport": "cmd",
             "command": ["r", "{url}"], "capabilities": ["js"]},
            {"name": "browser:chromium", "transport": "cmd",
             "command": ["r", "{url}"], "capabilities": ["js"]}]}))
        with self.assertRaises(ValueError):
            arms.load(cfg)

    def test_bare_cffi_expands_to_chrome(self):
        reg = arms.load(write_cfg('{"arms": []}'))
        self.assertEqual(reg.get("cffi").name, "cffi:chrome")

    def test_parse_list_rejects_bare_browser_and_unknown(self):
        reg = arms.load(write_cfg('{"arms": []}'))
        with self.assertRaises(ValueError):
            arms.parse_list("browser", reg)
        with self.assertRaises(ValueError):
            arms.parse_list("magic", reg)
        self.assertEqual(arms.parse_list("native,cffi", reg),
                         ["native", "cffi:chrome"])

    def test_missing_explicit_config_is_error(self):
        with self.assertRaises(ValueError):
            arms.load("/no/such/arms.json")

    def test_require_filters_capabilities(self):
        reg = arms.load(write_cfg('{"arms": []}'))
        ok, skipped = reg.filter(["native", "browser:chromium"], require=["js"])
        self.assertEqual(ok, [])
        self.assertEqual(len(skipped), 2)


if __name__ == "__main__":
    unittest.main()
