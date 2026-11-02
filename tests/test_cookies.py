import json
import os
import stat
import tempfile
import unittest

import support
from reconlib import arms, cookies, fetch
from reconlib.session import ArmSession


def empty_registry():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"arms": []}')
    return arms.load(path)


def routes():
    def set_cookie(h):
        support.response(h, 200, support.long_html("设置会话"),
                         headers=[("Set-Cookie", "sid=ok; Path=/")])

    def echo(h):
        got = h.headers.get("Cookie") or ""
        if "sid=ok" in got:
            support.response(h, 200, support.long_html("会话回显"))
        else:
            support.response(h, 403, "<html><body>请完成安全验证</body></html>")

    return {"/set": set_cookie, "/echo": echo}


class CookieStoreTest(unittest.TestCase):
    def test_jars_are_isolated_per_arm_and_host(self):
        store = cookies.CookieStore()
        try:
            native_a = store.curl_jar("native", "a.example")
            cffi_a = store.curl_jar("cffi:chrome", "a.example")
            native_b = store.curl_jar("native", "b.example")
            self.assertTrue(os.path.exists(native_a))
            self.assertEqual(len({native_a, cffi_a, native_b}), 3)
            self.assertEqual(stat.S_IMODE(os.stat(native_a).st_mode), 0o600)
            self.assertTrue(os.path.commonpath(
                [native_a, cffi_a, native_b, store.root]) == store.root)
            self.assertEqual(store.curl_jar("native", "a.example"), native_a)
            root = store.root
        finally:
            store.close()
        self.assertFalse(os.path.exists(root))

    def test_disabled_store_keeps_no_state(self):
        store = cookies.CookieStore(enabled=False)
        self.assertIsNone(store.curl_jar("native", "a.example"))
        self.assertIsNone(store.cffi_session("cffi:chrome", "a.example"))
        self.assertIsNone(store.root)
        store.close()

    def test_cffi_session_is_per_arm_and_host(self):
        try:
            import curl_cffi  # noqa: F401
        except ImportError:
            self.skipTest("未安装 curl_cffi")
        store = cookies.CookieStore()
        try:
            one = store.cffi_session("cffi:chrome", "a.example")
            two = store.cffi_session("cffi:chrome", "b.example")
            self.assertIsNotNone(one)
            self.assertIsNot(one, two)
            self.assertIs(store.cffi_session("cffi:chrome", "a.example"), one)
        finally:
            store.close()


class NativeCookieFlowTest(unittest.TestCase):
    def test_server_cookie_is_replayed_within_run(self):
        with support.serve(routes()) as base:
            store = cookies.CookieStore()
            try:
                first = fetch.fetch_one(base + "/set", "native", cookies=store)
                second = fetch.fetch_one(base + "/echo", "native", cookies=store)
            finally:
                store.close()
        self.assertEqual(first["status"], 200)
        self.assertEqual(second["status"], 200)

    def test_no_store_does_not_replay(self):
        with support.serve(routes()) as base:
            fetch.fetch_one(base + "/set", "native")
            second = fetch.fetch_one(base + "/echo", "native")
        self.assertEqual(second["status"], 403)


class SessionCookieTest(unittest.TestCase):
    def test_session_replays_cookie_and_keeps_rows_clean(self):
        with support.serve(routes()) as base:
            s = ArmSession(["native"], registry=empty_registry(),
                           policy="fixed", delay=0, permission=True)
            try:
                first = s.probe(base + "/set", selector="#content", min_content=200)
                second = s.probe(base + "/echo", selector="#content", min_content=200)
            finally:
                s.close()
        self.assertEqual(first["evidence"]["outcome"], "ok")
        self.assertEqual(second["evidence"]["outcome"], "ok")
        payload = json.dumps({"trials": s.trials},
                             ensure_ascii=False)
        self.assertNotIn("sid=ok", payload)

    def test_session_can_disable_cookies(self):
        with support.serve(routes()) as base:
            s = ArmSession(["native"], registry=empty_registry(),
                           policy="fixed", delay=0, permission=True,
                           cookies=False)
            try:
                first = s.probe(base + "/set", selector="#content", min_content=200)
                second = s.probe(base + "/echo", selector="#content", min_content=200)
            finally:
                s.close()
        self.assertEqual(first["evidence"]["outcome"], "ok")
        self.assertEqual(second["evidence"]["outcome"], "blocked")


class CookieCliTest(unittest.TestCase):
    def test_crawl_uses_cookies_by_default(self):
        urls = support.write_temp("", ".txt")
        try:
            with support.serve(routes()) as base:
                with open(urls, "w", encoding="utf-8") as f:
                    f.write(base + "/set\n" + base + "/echo\n")
                r, data = support.run_json(
                    "crawl", "--from", urls, "--selector", "#content",
                    "--arms", "native", "--policy", "fixed", "--delay", "0",
                    "--max-pages", "2", "--max-requests", "4",
                    "--i-have-permission", "--json")
        finally:
            os.unlink(urls)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([p["evidence"]["outcome"] for p in data["pages"]],
                         ["ok", "ok"])

    def test_crawl_no_cookies_skips_replay(self):
        urls = support.write_temp("", ".txt")
        try:
            with support.serve(routes()) as base:
                with open(urls, "w", encoding="utf-8") as f:
                    f.write(base + "/set\n" + base + "/echo\n")
                r, data = support.run_json(
                    "crawl", "--from", urls, "--selector", "#content",
                    "--arms", "native", "--policy", "fixed", "--delay", "0",
                    "--max-pages", "2", "--max-requests", "4",
                    "--i-have-permission", "--no-cookies", "--json")
        finally:
            os.unlink(urls)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([p["evidence"]["outcome"] for p in data["pages"]],
                         ["ok", "blocked"])

    def test_fetch_accepts_no_cookies(self):
        with support.serve(routes()) as base:
            r, data = support.run_json(
                "fetch", base + "/set", "--selector", "#content",
                "--rung", "native", "--delay", "0", "--no-cookies", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(data["records"][0]["rungs"][0]["outcome"], "ok")

    def test_quick_accepts_no_cookies(self):
        with support.serve(routes()) as base:
            r = support.run("quick", base + "/set", "--selector", "#content",
                            "--delay", "0", "--no-cookies", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
