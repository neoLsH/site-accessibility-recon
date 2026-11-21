"""Shared loop and optional real graph contracts; localhost traffic only."""
import importlib.util
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import support
from test_agent import BASE, page_with_links, ok
from reconlib import agent

GRAPH = importlib.util.find_spec("langgraph") is not None


class LoopTest(unittest.TestCase):
    def test_shared_json_state_routes(self):
        self.assertIsNotNone(importlib.util.find_spec("reconlib.loop"))
        from reconlib import loop
        self.assertEqual(set(loop.NODES), {"analyze", "crawl", "discover", "plan",
                                          "review", "decide", "finish"})
        self.assertEqual(loop.route({"phase": "crawl"}), "crawl")
        self.assertNotIn("langgraph", loop.__dict__)

    def test_augmentation_caps_total_not_only_frontier(self):
        plan = {"schema": "recon-explore/1", "allocated": 3, "steps": [
            {"context": "a.test|article|content", "arm": "native", "requests": 3}]}
        body = agent.augment(plan, ["https://a.test/list/1"], ["native"], max_requests=2)
        self.assertLessEqual(sum(s["requests"] for s in body["steps"]), 2)
        self.assertEqual(body["allocated"], 2)

    @patch("reconlib.arms._has_cffi", return_value=True)
    def test_build_plan_keeps_configured_untried_arm_on_known_route(self, _has_cffi):
        from agent import parser
        from reconlib import evidence, loop
        url = "https://a.test/article/1"
        with tempfile.TemporaryDirectory() as out:
            args = parser().parse_args([
                url, "--out", out, "--arms", "native,cffi", "--seed", "7"])
            state = loop.initial_state(vars(args), [url])
            evidence.write_jsonl(state["paths"]["trials"], [{
                "kind": "trial", "url": url, "arm": "native",
                "context": "a.test|article|content", "trial_outcome": "ok"}])
            body, error = loop.build_plan(args, [url], state["paths"], 2,
                                          state["arm_list"])
            self.assertIsNone(error)
            self.assertTrue(any(s["arm"] == "cffi:chrome" and s["kind"] == "unseen"
                                and s["context"] == "a.test|article|content"
                                for s in body["steps"]), body["steps"])
            self.assertEqual(body["unseen_arms"], ["cffi:chrome"])
            self.assertEqual(body["summary"]["unseen_arms"], ["cffi:chrome"])
            self.assertEqual(body["frontier"]["steps"], 0)
            self.assertLessEqual(body["allocated"], 2)
            self.assertEqual(body["allocated"], sum(s["requests"] for s in body["steps"]))
            self.assertEqual(body["remaining"], 2 - body["allocated"])
            with open(state["paths"]["plan"], encoding="utf-8") as f:
                self.assertEqual(json.load(f), body)

    def test_build_plan_filters_ineligible_steps_and_frontier_candidates(self):
        from agent import parser
        from reconlib import evidence, explore, loop
        url = "https://a.test/article/5"
        cases = (
            # name, configured, require, cffi available, historical arm/outcomes, eligible
            ("unavailable", "native,cffi", "", False,
             [("native", "ok")] * 4, ["native"]),
            ("capability", "native,browser:fixture", "tls,js", True,
             [("browser:fixture", "ok")] * 4, ["browser:fixture"]),
            ("historical-removed", "native", "", True,
             [("native", "ok")] * 4 + [("cffi:chrome", "ok")], ["native"]),
            ("none-available", "cffi", "", False,
             [("native", "ok")] * 4, []),
            ("none-capable", "native,cffi", "js", True,
             [("native", "ok")] * 4, []),
            ("eligible-suspended", "native,cffi", "", False,
             [("native", "blocked")] * 3, []),
        )
        for name, configured, require, cffi, history, expected in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as out, patch(
                    "reconlib.arms._has_cffi", return_value=cffi):
                arms_file = os.path.join(out, "arms.json")
                with open(arms_file, "w", encoding="utf-8") as f:
                    json.dump({"arms": [{"name": "browser:fixture", "transport": "native",
                                         "capabilities": ["tls", "js"]}]}, f)
                args = parser().parse_args([
                    url, "--out", out, "--arms", configured, "--arms-file", arms_file,
                    "--require", require, "--budget", "1", "--seed", "7"])
                state = loop.initial_state(vars(args), [url])
                rows = [{"kind": "trial", "url": f"https://a.test/article/{i}",
                         "arm": arm, "context": "a.test|article|content",
                         "trial_outcome": outcome}
                        for i, (arm, outcome) in enumerate(history)]
                evidence.write_jsonl(state["paths"]["trials"], rows)
                raw = explore.plan(*explore.replay(rows, arms=state["arm_list"]), budget=1)
                body, error = loop.build_plan(args, [url], state["paths"], 1,
                                              state["arm_list"])
                self.assertIsNone(error)
                self.assertEqual([s["arm"] for s in body["steps"]], expected)
                self.assertTrue(all(s["kind"] == "frontier" for s in body["steps"]))
                self.assertEqual(body["allocated"], len(expected))
                self.assertEqual(body["allocated"], sum(s["requests"] for s in body["steps"]))
                self.assertEqual(body["remaining"], 1 - body["allocated"])
                self.assertEqual(body["summary"]["steps"], len(body["steps"]))
                self.assertEqual(explore.Quota(body).all_arms(), set(expected))
                for key in ("arms", "contexts", "unseen_arms"):
                    self.assertEqual(body[key], raw[key], key)
                self.assertEqual(body["summary"]["unseen_arms"], raw["summary"]["unseen_arms"])
                with open(state["paths"]["plan"], encoding="utf-8") as f:
                    self.assertEqual(json.load(f), body)

    def test_default_arms_missing_cffi_crawls_known_route_frontier(self):
        hits = []

        def article(h):
            hits.append(h.path)
            support.response(h, 200, support.long_html() +
                             '<a href="/article/5">next</a>')

        runtimes = ("stdlib", "langgraph") if GRAPH else ("stdlib",)
        for runtime in runtimes:
            hits.clear()
            with self.subTest(runtime=runtime), tempfile.TemporaryDirectory() as out, support.serve({
                    f"/article/{i}": article for i in range(1, 6)}) as base:
                # Mask imports in the agent AND its crawl/explore subprocesses,
                # independent of whether the test environment installs curl_cffi.
                with open(os.path.join(out, "sitecustomize.py"), "w", encoding="utf-8") as f:
                    f.write(
                        "import importlib.abc\nimport sys\n"
                        "class NoCffi(importlib.abc.MetaPathFinder):\n"
                        "    def find_spec(self, fullname, path=None, target=None):\n"
                        "        if fullname == 'curl_cffi' or fullname.startswith('curl_cffi.'):\n"
                        "            raise ModuleNotFoundError('curl_cffi masked for test')\n"
                        "sys.meta_path.insert(0, NoCffi())\n")
                arms_file = os.path.join(out, "arms.json")
                with open(arms_file, "w", encoding="utf-8") as f:
                    json.dump({"arms": []}, f)
                seeds = [f"{base}/article/{i}" for i in range(1, 5)]
                graph_args = ("--thread-id", "missing-cffi") if runtime == "langgraph" else ()
                with patch.dict(os.environ, {
                        "PYTHONPATH": out + os.pathsep + os.environ.get("PYTHONPATH", "")}):
                    r, report = support.run_json(
                        "agent", *seeds, "--out", out, "--selector", "#content",
                        "--delay", "0", "--timeout", "10", "--arms-file", arms_file,
                        "--runtime", runtime, *graph_args, "--budget", "1",
                        "--max-rounds", "3", "--max-requests", "20", "--seed", "7",
                        "--i-have-permission", "--json")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual([rd["crawl"]["requests"] for rd in report["rounds"]], [4, 1])
                self.assertEqual(hits, ["/article/1", "/article/1", "/article/2",
                                        "/article/3", "/article/4", "/article/5"])
                self.assertEqual(report["budget"]["requests"], 6)
                self.assertEqual(report["stop"]["kind"], "frontier-empty")
                with open(os.path.join(out, "plan.json"), encoding="utf-8") as f:
                    plan = json.load(f)
                self.assertEqual(plan["unseen_arms"], ["cffi:chrome"])
                self.assertEqual([(s["arm"], s["kind"], s["requests"]) for s in plan["steps"]],
                                 [("native", "frontier", 1)])

    def test_retry_urls_uses_latest_outcome_per_identity(self):
        from reconlib import loop
        url = "https://a.test/article/1"
        for field in ("url", "final_url"):
            for earlier, latest, expected in (
                    ("blocked", "ok", []), ("degraded", "ok", []),
                    ("ok", "blocked", [url]), ("ok", "degraded", [url]),
                    ("blocked", "auth", []), ("blocked", "missing", []),
                    ("blocked", "noise", [])):
                with self.subTest(field=field, earlier=earlier, latest=latest):
                    rows = [{field: url, "outcome": earlier},
                            {field: url, "outcome": latest}]
                    self.assertEqual(loop.retry_urls(rows), expected)

    def test_retry_identity_prefers_final_url_and_keeps_order(self):
        from reconlib import loop
        first = "https://a.test/article/1"
        second = "https://a.test/article/2"
        rows = [
            {"url": "https://a.test/old", "final_url": first, "outcome": "blocked"},
            {"url": second, "final_url": "", "outcome": "degraded"},
            {"url": first, "outcome": "ok"},
            {"url": second, "outcome": "blocked"},
            {"outcome": "blocked"}]
        self.assertEqual(loop.retry_urls(rows), [second])
        self.assertEqual(loop.next_urls([first, second], rows, 2), [first, second])

    def test_discover_retains_cumulative_evidence_but_drops_recovered_retry(self):
        from agent import parser
        from reconlib import loop
        url = "https://a.test/article/1"
        rows = [{"final_url": url, "outcome": "blocked"},
                {"final_url": url, "outcome": "ok"}]
        with tempfile.TemporaryDirectory() as out:
            args = parser().parse_args([url, "--out", out, *BASE, "--no-discover"])
            state = loop.initial_state(vars(args), [url])
            state.update({"ev_all": rows, "tr_round": [], "summary": {}})
            result = loop.step(state, "discover")
        self.assertEqual(result["ev_all"], rows)
        self.assertEqual(result["advice"]["evidence"]["n"], 2)
        self.assertEqual(result["advice"]["evidence"]["outcomes"]["blocked"], 1)
        self.assertEqual(result["advice"]["evidence"]["outcomes"]["ok"], 1)
        self.assertEqual(result["urls"], [])

    def test_small_budget_preserves_unrequested_frontier(self):
        hits = []
        def home(h):
            hits.append(h.path)
            page_with_links(h)
        def article(h):
            hits.append(h.path)
            ok(h)
        runtimes = ("stdlib", "langgraph") if GRAPH else ("stdlib",)
        for runtime in runtimes:
            hits.clear()
            with self.subTest(runtime=runtime), tempfile.TemporaryDirectory() as out, support.serve({
                    "/": home, "/article/1": article, "/article/2": article}) as base:
                graph_args = ("--thread-id", "small") if runtime == "langgraph" else ()
                r, report = support.run_json(
                    "agent", base + "/", "--out", out, *BASE,
                    "--runtime", runtime, *graph_args, "--policy", "fixed",
                    "--budget", "1", "--max-rounds", "3", "--max-requests", "20", "--json")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(hits, ["/", "/", "/article/1", "/article/2"])
                self.assertEqual([rd["crawl"]["requests"] for rd in report["rounds"]], [1, 1, 1])
                self.assertEqual(report["rounds"][1]["crawl"]["plan_exhausted_pages"], 1)
                self.assertEqual(report["budget"]["requests"], 4)

    def test_cli_requires_thread(self):
        with tempfile.TemporaryDirectory() as out:
            r = support.run("agent", "--out", out, "--runtime", "langgraph", "--resume")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--thread-id", r.stderr)

    def test_missing_dependency_is_actionable(self):
        from reconlib import graph
        import builtins
        real_import = builtins.__import__
        def no_optional(name, *args, **kwargs):
            if name.startswith("langgraph"):
                raise ImportError("not installed")
            return real_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=no_optional):
            with self.assertRaisesRegex(RuntimeError, "pip install -r requirements-langgraph.txt"):
                graph.dependencies()

    def test_resume_rejects_new_configuration(self):
        with tempfile.TemporaryDirectory() as out:
            r = support.run("agent", "--out", out, "--runtime", "langgraph",
                            "--thread-id", "test", "--resume", "--max-requests", "100")
        self.assertEqual(r.returncode, 2)
        self.assertIn("saved configuration", r.stderr)

    def test_stream_is_jsonl(self):
        with tempfile.TemporaryDirectory() as out, support.serve({"/": ok}) as base:
            r = support.run("agent", base, "--out", out, *BASE,
                            "--max-rounds", "1", "--stream")
        self.assertEqual(r.returncode, 0, r.stderr)
        events = [json.loads(line) for line in r.stdout.splitlines()]
        self.assertIn("analyze", [e.get("node") for e in events])
        self.assertEqual(events[-1]["schema"], "agent/1")


@unittest.skipUnless(GRAPH, "optional LangGraph not installed")
class GraphTest(unittest.TestCase):
    def test_sqlite_reopen_review_resume_and_finished_no_replay(self):
        hits = []
        def home(h):
            hits.append("home")
            page_with_links(h)
        def article(h):
            hits.append("article")
            ok(h)
        with tempfile.TemporaryDirectory() as out, support.serve({
                "/": home, "/article/1": article, "/article/2": article}) as base:
            args = ("agent", "--out", out, "--runtime", "langgraph", "--thread-id", "test", "--json")
            r, paused = support.run_json(*args, base + "/", *BASE, "--interrupt",
                                         "--budget", "6", "--seed", "7")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(paused["stop"]["kind"], "interrupted")
            self.assertEqual(hits, ["home", "home"])
            self.assertTrue(os.path.isfile(os.path.join(out, "checkpoint.sqlite")))
            r, final = support.run_json(*args, "--resume")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(len(final["rounds"]), 2)
            self.assertEqual(hits.count("home"), 2)
            self.assertEqual(hits.count("article"), 2)
            count = len(hits)
            r, again = support.run_json(*args, "--resume")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(again, final)
            self.assertEqual(len(hits), count)

    def test_plan_edits_rejected_without_requests(self):
        for edit in ("host", "arm", "budget", "schema", "route"):
            with self.subTest(edit=edit), tempfile.TemporaryDirectory() as out, support.serve({"/": page_with_links}) as base:
                args = ("agent", "--out", out, "--runtime", "langgraph", "--thread-id", "edit", "--json")
                r, _ = support.run_json(*args, base, *BASE, "--interrupt", "--budget", "6")
                self.assertEqual(r.returncode, 0, r.stderr)
                path = os.path.join(out, "plan.json")
                with open(path) as f:
                    plan = json.load(f)
                step = plan["steps"][0]
                if edit == "host": step["context"] = "outside.test|article|unknown"
                if edit == "arm": step["arm"] = "browser:evil"
                if edit == "budget": step["requests"] = 10000
                if edit == "schema": plan["schema"] = "bad/1"
                if edit == "route": step["context"] = step["context"].split("|")[0] + "|new-route|unknown"
                with open(path, "w") as f:
                    json.dump(plan, f)
                r = support.run(*args, "--resume")
                self.assertNotEqual(r.returncode, 0)
                self.assertFalse(os.path.exists(os.path.join(out, "r2-urls.txt")))

    def test_memory_real_nodes_and_core_parity(self):
        from reconlib import loop, graph
        from agent import parser
        from langgraph.checkpoint.memory import InMemorySaver
        with tempfile.TemporaryDirectory() as out, support.serve({"/": ok}) as base:
            args = parser().parse_args([base, "--out", out, *BASE, "--max-rounds", "1"])
            state = loop.initial_state(vars(args), [base])
            events = []
            saver = InMemorySaver()
            result = graph.run(state, "memory", checkpointer=saver, emit=events.append)
            self.assertEqual([e["node"] for e in events], ["analyze", "decide", "crawl", "discover", "decide", "finish"])
            json.dumps(result)
            app = graph.build_graph(saver)
            snapshot = app.get_state({"configurable": {"thread_id": "memory"}})
            self.assertFalse(snapshot.next)
            self.assertEqual(snapshot.values["report"]["schema"], "agent/1")
            core = loop.run(loop.initial_state(vars(args), [base]))
            self.assertEqual(core["budget"], result["budget"])
            self.assertEqual(core["stop"], result["stop"])

    def test_uncertain_replay_fails_closed(self):
        from reconlib import loop, graph
        from agent import parser
        from langgraph.checkpoint.memory import InMemorySaver
        with tempfile.TemporaryDirectory() as out:
            args = parser().parse_args(["http://localhost/", "--out", out, *BASE])
            state = loop.initial_state(vars(args), args.urls)
            saver = InMemorySaver()
            with patch.object(loop, "analyze", side_effect=RuntimeError("crash")):
                with self.assertRaisesRegex(RuntimeError, "crash"):
                    graph.run(state, "crash", checkpointer=saver)
            with self.assertRaisesRegex(ValueError, "uncertain|in-flight"):
                graph.run(state, "crash", resume=True, checkpointer=saver)


if __name__ == "__main__":
    unittest.main()
