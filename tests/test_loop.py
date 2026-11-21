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
