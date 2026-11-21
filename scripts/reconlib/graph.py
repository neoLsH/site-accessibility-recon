"""Optional StateGraph adapter. Checkpoints do not promise exactly-once I/O."""
import hashlib
import json
import os
from contextlib import contextmanager

from . import loop

INSTALL = "Install optional runtime: python -m pip install -r requirements-langgraph.txt"


def dependencies():
    try:
        from langgraph.graph import StateGraph, START, END
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError as exc:
        raise RuntimeError(INSTALL) from exc
    return StateGraph, START, END, InMemorySaver, SqliteSaver


def _network_marker(state, node, thread_id):
    """Keep a durable phase marker so a pre-checkpoint crash cannot replay requests."""
    key = hashlib.sha256(thread_id.encode()).hexdigest()
    name = f".inflight-{key}-{node}-{len(state['rounds']) + 1}.json"
    path = os.path.join(state["config"]["out"], name)
    try:
        with open(path, "x", encoding="utf-8") as f:
            json.dump({"thread_id": thread_id, "node": node,
                       "round": len(state["rounds"]) + 1}, f)
            f.flush()
            os.fsync(f.fileno())
        # Persist the directory entry as well as the marker content.
        fd = os.open(state["config"]["out"], os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except FileExistsError as exc:
        raise ValueError("uncertain in-flight network phase; refusing replay. "
                         "Inspect artifacts; start a new output/thread with a newly approved budget.") from exc


def build_graph(checkpointer, interrupt=False):
    StateGraph, START, END, _, _ = dependencies()
    builder = StateGraph(dict)

    def wrap(name):
        def node(state, config):
            if name in ("analyze", "crawl"):
                _network_marker(state, name, config["configurable"]["thread_id"])
            return loop.step(state, name)
        return node

    destinations = {name: name for name in loop.NODES}
    destinations["end"] = END
    for name in loop.NODES:
        builder.add_node(name, wrap(name))
        builder.add_conditional_edges(name, loop.route, destinations)
    builder.add_conditional_edges(START, loop.route, destinations)
    return builder.compile(checkpointer=checkpointer,
                           interrupt_before=["review"] if interrupt else [])


def run(state, thread_id, *, checkpointer, resume=False, interrupt=False, emit=None):
    if not thread_id:
        raise ValueError("--thread-id is required")
    app = build_graph(checkpointer, interrupt=interrupt)
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 100000}
    snapshot = app.get_state(config)
    if resume:
        if not snapshot.values:
            raise ValueError("No saved checkpoint for --thread-id; cannot resume")
        if os.path.realpath(state["config"]["out"]) != snapshot.values["config"]["out"]:
            raise ValueError("Resume must use the same output directory")
        if not snapshot.next:
            return snapshot.values["report"]
        value = None
    else:
        if snapshot.values:
            raise ValueError("Thread already exists; use --resume (budgets cannot reset)")
        value = state
    for update in app.stream(value, config, stream_mode="updates", durability="sync"):
        for node, result in update.items():
            if node != "__interrupt__" and emit:
                emit(loop.event(node, result))
    snapshot = app.get_state(config)
    if snapshot.next:
        return loop.report(snapshot.values, {"kind": "interrupted", "reason":
                           "Paused before review; edit plan.json within original quota, then --resume"})
    return snapshot.values["report"]


@contextmanager
def _output_lock(out):
    # OS releases this lock on process exit, unlike the permanent network markers.
    import fcntl
    with open(os.path.join(out, ".agent.lock"), "a", encoding="utf-8") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Output is already in use by another AgentLoop") from exc
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def run_cli(config, seeds, emit=None):
    _, _, _, InMemorySaver, SqliteSaver = dependencies()
    out = os.path.realpath(config["out"])
    os.makedirs(out, exist_ok=True)
    with _output_lock(out):
        owner = os.path.join(out, ".agent-thread.json")
        if os.path.exists(owner):
            with open(owner, encoding="utf-8") as f:
                binding = json.load(f)
            if binding != {"out": out, "thread_id": config["thread_id"]}:
                raise ValueError("Output belongs to a different thread/directory")
        elif config["resume"]:
            raise ValueError("No saved thread in this output directory")
        else:
            with open(owner, "x", encoding="utf-8") as f:
                json.dump({"out": out, "thread_id": config["thread_id"]}, f)
        state = {"config": {"out": out}} if config["resume"] else loop.initial_state(config, seeds)
        kwargs = {"resume": config["resume"], "interrupt": config["interrupt"], "emit": emit}
        if config["memory"]:
            return run(state, config["thread_id"], checkpointer=InMemorySaver(), **kwargs)
        with SqliteSaver.from_conn_string(os.path.join(out, "checkpoint.sqlite")) as saver:
            # The original interrupt setting is part of runtime checkpoint metadata.
            saved = saver.get_tuple({"configurable": {"thread_id": config["thread_id"]}})
            if config["resume"] and saved:
                kwargs["interrupt"] = saved.checkpoint["channel_values"]["__root__"].get("interrupt", False)
            if not config["resume"]:
                state["interrupt"] = config["interrupt"]
            return run(state, config["thread_id"], checkpointer=saver, **kwargs)
