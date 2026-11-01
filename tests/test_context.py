import unittest

import support
from reconlib import context


class RouteClassTest(unittest.TestCase):
    def test_classes(self):
        self.assertEqual(context.route_class("https://a.test/article/1"), "article")
        self.assertEqual(context.route_class("https://a.test/news/2026/x"), "article")
        self.assertEqual(context.route_class("https://a.test/list"), "list")
        self.assertEqual(context.route_class("https://a.test/"), "list")
        self.assertEqual(context.route_class("https://a.test/api/v1/x"), "api")
        self.assertEqual(context.route_class("https://a.test/x.json"), "api")
        self.assertEqual(context.route_class("https://a.test/help"), "other")


class ResponseClassTest(unittest.TestCase):
    def test_classes(self):
        self.assertEqual(context.response_class({"outcome": "ok"}), "content")
        self.assertEqual(context.response_class({"outcome": "degraded"}), "shell")
        self.assertEqual(context.response_class({"outcome": "blocked", "waf_no": 4}),
                         "challenge")
        self.assertEqual(context.response_class({"outcome": "auth"}), "login")
        self.assertEqual(context.response_class({"outcome": "missing"}), "missing")
        self.assertEqual(context.response_class({"outcome": "noise"}), "noise")
        self.assertEqual(context.response_class(None), "unknown")


class KeyTest(unittest.TestCase):
    def test_key_and_fallbacks(self):
        key = context.key("a.test", "article", "content")
        self.assertEqual(key, "a.test|article|content")
        self.assertEqual(context.fallbacks(key),
                         ["a.test|article|content", "a.test|article|*",
                          "a.test|*|*", "*"])


if __name__ == "__main__":
    unittest.main()
