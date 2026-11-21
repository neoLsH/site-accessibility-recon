"""JSON-serializable, deterministic shared AgentLoop; no optional runtime imports."""

import argparse
import json
import os
import subprocess
import sys
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import shape  # noqa: E402
from reconlib import (UA_DEFAULT, advice, agent as agentlib, arms as armreg,
                      context as ctxmod, discover, evidence, explore, robots)  # noqa: E402

EXPLORE = os.path.join(HERE, "explore.py")
CRAWL = os.path.join(HERE, "crawl.py")
SCHEMA = "agent/1"
DEFAULT_ARMS = "native,cffi:chrome"

GATE_NEXT = {
    "challenge": "形态是挑战页：先按 WAF 机制归类并核对 robots / 限速，默认不加请求",
    "login-wall": "形态是登录墙：走站点方授权的凭证或官方数据源，默认不加请求",
    "unreachable": "链路不可达：先修 DNS / 代理 / TLS，默认不加请求",
    "error-page": "种子返回错误页：先核对 URL、路由与链路，默认不加请求",
    "robots-deny": "robots.txt 不允许：该 host 一个请求都不发",
    "budget-deferred": "请求预算不足：本轮不做站点分析",
}
GATED = frozenset(GATE_NEXT)
FAILED_KINDS = frozenset({"safety", "config", "crawl-error"})


def die(ap, message):
    ap.error(message)


def run_script(path, args, timeout=1800):
    cmd = [sys.executable, path] + args
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def tail(text, limit=300):
    lines = [ln for ln in (text or "").strip().splitlines() if ln.strip()]
    return " | ".join(lines[-3:])[:limit] or "无输出"


def read_urls_file(path):
    urls = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line:
                urls.append(line)
    return urls


def normalize(urls):
    out = []
    for url in urls:
        url = url.strip()
        if not url:
            continue
        url = url if url.startswith(("http://", "https://")) else f"https://{url}"
        if url not in out:
            out.append(url)
    return out


def arm_names(spec, registry, impersonate):
    return armreg.parse_list(spec, registry, impersonate)


def analyze(seeds, args, cfg):
    """每个 host 一个种子：robots → 单请求形态分类。返回 (items, requests)。"""
    items, seen_hosts, used = [], set(), 0
    for url in seeds:
        host = urlsplit(url).netloc
        if not host or host in seen_hosts:
            continue
        seen_hosts.add(host)
        item = {"host": host, "url": url, "requests": 0}
        if used >= args.max_requests:
            item.update({"shape": "budget-deferred", "confidence": "observed",
                         "robots": "unknown", "next": GATE_NEXT["budget-deferred"]})
            items.append(item)
            continue
        allowed = ("skipped" if args.i_have_permission else
                   robots.decision(url, timeout=args.timeout, ua=args.ua))
        item["robots"] = allowed
        if allowed == "deny":
            item.update({"shape": "robots-deny", "confidence": "observed",
                         "next": GATE_NEXT["robots-deny"]})
            items.append(item)
            continue
        status, final_url, ctype, redirects, body = shape.fetch(
            url, args.timeout, shape.UA)
        used += 1
        item["requests"] = 1
        name, signals, _ = shape.classify(
            status, final_url, ctype, redirects, body, args.min_content, cfg)
        text_len = 0
        if body and not body.startswith(b"__ERR__"):
            text_len = shape.text_len(shape.scan_html(body.decode("utf-8", "replace")))
        item.update({
            "shape": name, "confidence": "heuristic", "http": status,
            "final_url": final_url, "redirects": redirects, "bytes": len(body),
            "text_len": text_len, "signals": signals,
            "next": GATE_NEXT.get(name) or shape.NEXT.get(name, ""),
        })
        items.append(item)
    return items, used


def crawl_args(args, urls_file, paths, *, plan, policy_in, pages, requests):
    out = ["--from", urls_file,
           "--selector", args.selector, "--min-content", str(args.min_content),
           "--arms", args.arms, "--policy", args.policy,
           "--delay", str(args.delay), "--timeout", str(args.timeout),
           "--ua", args.ua, "--min-pulls", str(args.min_pulls),
           "--cost-weight", str(args.cost_weight),
           "--prior-strength", str(args.prior_strength),
           "--half-life", str(args.half_life),
           "--block-budget", str(args.block_budget),
           "--rate-limit-budget", str(args.rate_limit_budget),
           "--rate-limit-cooldown", str(args.rate_limit_cooldown),
           "--max-pages", str(pages), "--max-requests", str(requests),
           "--evidence-out", paths["evidence"],
           "--trial-out", paths["trials"],
           "--policy-out", paths["policy"], "--json"]
    if args.arms_file:
        out += ["--arms-file", args.arms_file]
    if args.require:
        out += ["--require", args.require]
    if args.impersonate:
        out += ["--impersonate", args.impersonate]
    if args.seed is not None:
        out += ["--seed", str(args.seed)]
    if args.no_cookies:
        out += ["--no-cookies"]
    if args.i_have_permission:
        out += ["--i-have-permission"]
    if policy_in:
        out += ["--policy-in", policy_in]
    if plan:
        out += ["--plan", plan]
    if args.discover:
        out += ["--out-dir", paths["bodies"]]
    return out


def build_plan(args, urls, paths, budget, arm_list):
    """explore 出计划 → 把 frontier 新路由类补进去 → 覆盖写 plan.json。"""
    budget = max(1, min(args.budget, budget))
    plan_args = ["--trials", paths["trials"],
                 "--arms", ",".join(arm_list),
                 "--budget", str(budget),
                 "--max-per-context", str(args.max_per_context),
                 "--block-budget", str(args.block_budget),
                 "--prior-strength", str(args.prior_strength),
                 "--half-life", str(args.half_life),
                 "--cost-weight", str(args.cost_weight),
                 "--min-pulls", str(args.min_pulls),
                 "--plan-out", paths["plan"], "--json"]
    if args.seed is not None:
        plan_args += ["--seed", str(args.seed)]
    r = run_script(EXPLORE, plan_args)
    if r.returncode != 0:
        return None, tail(r.stderr or r.stdout)
    try:
        with open(paths["plan"], encoding="utf-8") as f:
            body = json.load(f)
        queued = {(urlsplit(url).netloc, ctxmod.route_class(url)) for url in urls}
        eligible, _ = armreg.load(args.arms_file).filter(
            arm_list, armreg.parse_require(args.require))
        body["steps"] = [step for step in body["steps"]
                         if tuple(step["context"].split("|")[:2]) in queued
                         and step["arm"] in eligible]
        body = agentlib.augment(body, urls, eligible, per_url=1,
                                max_requests=budget)
        body["remaining"] = budget - body["allocated"]
        body["summary"]["steps"] = len(body["steps"])
        with open(paths["plan"], "w", encoding="utf-8") as f:
            json.dump(body, f, ensure_ascii=False, indent=2)
    except (OSError, ValueError) as e:
        return None, f"计划增补失败: {e}"
    return body, None


def discover_round(trials, allowed_hosts, seen, limit):
    """从本轮留存的响应体里提取同站链接；seen 会被原地更新。"""
    found = []
    if limit <= 0:
        return found
    for row in trials:
        if len(found) >= limit:
            break
        saved = row.get("saved")
        base = row.get("url")
        if not saved or not base:
            continue
        try:
            with open(saved, "rb") as f:
                body = f.read()
        except OSError:
            continue
        for link in discover.links(body, base, hosts=allowed_hosts,
                                   max_links=limit - len(found)):
            if link in seen:
                continue
            seen.add(link)
            found.append(link)
            if len(found) >= limit:
                break
    return found


def retry_urls(rows):
    latest = {}
    for row in rows:
        url = row.get("final_url") or row.get("url")
        if url:
            latest[url] = row
    return [url for url, row in latest.items()
            if row.get("outcome") in ("blocked", "degraded")]


def next_urls(frontier, rows, limit):
    out, seen = [], set()
    for url in frontier + retry_urls(rows):
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
        if len(out) >= limit:
            break
    return out


def slim_summary(summary):
    keys = ("requested", "pages", "requests", "outcomes", "stopped", "plan",
            "plan_exhausted_pages", "suspended_pages", "cooldown_pages",
            "rate_limited_pages", "skipped_robots", "circuit_open", "policy",
            "policy_out", "arms")
    return {k: summary.get(k) for k in keys}


def initial_state(config, seeds):
    """Only JSON values cross checkpoints; runtime resources stay outside state."""
    config = dict(config)
    for key in ("runtime", "thread_id", "resume", "interrupt", "stream", "memory", "json",
                "urls", "from_file"):
        config.pop(key, None)
    config["out"] = os.path.realpath(config["out"])
    for key in ("arms_file", "policy_in"):
        if config.get(key):
            config[key] = os.path.abspath(config[key])
    args = argparse.Namespace(**config)
    arm_list = arm_names(args.arms, armreg.load(args.arms_file), args.impersonate)
    armreg.parse_require(args.require)
    os.makedirs(args.out, exist_ok=True)
    paths = {key: os.path.join(args.out, name) for key, name in {
        "evidence": "evidence.jsonl", "trials": "trials.jsonl", "plan": "plan.json",
        "policy": "policy.json", "bodies": "bodies", "report": "agent.json"}.items()}
    return {"version": "agent-loop/1", "config": config, "seeds": seeds,
            "arm_list": arm_list, "paths": paths, "phase": "analyze", "analysis": [],
            "requests_used": 0, "pages_used": 0, "seen": list(seeds), "frontier": [],
            "discovered_total": 0, "frontier_pending": 0, "rounds": [], "ev_all": [],
            "tr_all": [], "advice": None, "stop": None, "plan_path": None,
            "plan_info": None, "urls": list(seeds), "allowed_hosts": []}


def route(state):
    return state["phase"]


def _analyze(s):
    args = argparse.Namespace(**s["config"])
    s["analysis"], s["requests_used"] = analyze(
        s["seeds"], args, shape.load_signals(shape.DEFAULT_SIGNALS))
    gated = {i["host"] for i in s["analysis"] if i["shape"] in GATED}
    s["allowed_hosts"] = sorted({urlsplit(u).netloc for u in s["seeds"]} - gated)
    s["urls"] = [u for u in s["seeds"] if urlsplit(u).netloc in s["allowed_hosts"]]
    if not s["urls"]:
        s["stop"] = {"kind": "analysis-gated", "reason": "没有可抓取的 host：形态分析或 robots 已拦下全部种子"}
    s["phase"] = "decide"


def _decide(s):
    c = s["config"]
    if s["stop"] is None and s["rounds"]:
        s["stop"] = agentlib.safety_stop(s["rounds"][-1]["crawl"])
    if s["stop"] is None:
        if len(s["rounds"]) >= c["max_rounds"]:
            s["stop"] = {"kind": "max-rounds", "reason": "达到轮数上限"}
        elif s["requests_used"] >= c["max_requests"]:
            s["stop"] = {"kind": "budget", "reason": "请求预算已用尽"}
        elif s["pages_used"] >= c["max_pages"]:
            s["stop"] = {"kind": "budget", "reason": "页面预算已用尽"}
        elif not s["urls"]:
            s["stop"] = {"kind": "frontier-empty", "reason": "没有待重试页，也没有新发现的同站链接"}
    s["phase"] = "finish" if s["stop"] else ("plan" if s["rounds"] else "crawl")


def _plan(s):
    args = argparse.Namespace(**s["config"])
    body, err = build_plan(args, s["urls"], s["paths"],
                           args.max_requests - s["requests_used"], s["arm_list"])
    if err:
        s["stop"] = {"kind": "plan-unavailable", "reason": err}
        s["phase"] = "finish"
        return
    s["plan_path"] = s["paths"]["plan"]
    # Review may reduce this authority, never add routes/arms or quota.
    s["approved_plan"] = body
    s["phase"] = "review"


def validate_plan(s, body):
    if not isinstance(body, dict) or body.get("schema") != explore.SCHEMA:
        raise ValueError("plan review: invalid schema")
    quota = explore.Quota(body)
    original = explore.Quota(s["approved_plan"])
    totals = {}
    for step in body["steps"]:
        if not isinstance(step, dict):
            raise ValueError("plan review: invalid step")
        ctx, arm, count = step.get("context"), step.get("arm"), step.get("requests")
        if (not isinstance(ctx, str) or len(ctx.split("|")) != 3 or
                not all(ctx.split("|")) or type(count) is not int or count <= 0):
            raise ValueError("plan review: invalid context or quota")
        host, route_name, _ = ctx.split("|")
        if host not in s["allowed_hosts"] or arm not in s["arm_list"]:
            raise ValueError("plan review: scope expansion (host/arm)")
        key = (host, route_name, arm)
        totals[key] = totals.get(key, 0) + count
        if totals[key] > original.left(*key):
            raise ValueError("plan review: scope/quota expansion")
    bound = min(s["config"]["budget"], s["config"]["max_requests"] - s["requests_used"])
    if quota.planned > bound:
        raise ValueError("plan review: quota exceeds remaining original budget")
    return quota


def _review(s):
    with open(s["plan_path"], encoding="utf-8") as f:
        body = json.load(f)
    quota = validate_plan(s, body)
    body["allocated"] = quota.planned
    s["reviewed_plan"] = body
    s["plan_info"] = {"allocated": quota.planned, "steps": len(body["steps"]),
                      "frontier": body.get("frontier")}
    c = s["config"]
    decision = agentlib.decide(s["advice"], c["max_requests"] - s["requests_used"],
                              c["max_rounds"] - len(s["rounds"]), len(body["steps"]),
                              bool(s["urls"]), frontier=s["frontier_pending"])
    s["frontier_pending"] = 0
    if not decision["run"]:
        s["stop"] = {"kind": decision["kind"], "reason": decision["reason"]}
    s["phase"] = "finish" if s["stop"] else "crawl"


def _crawl(s):
    args = argparse.Namespace(**s["config"])
    n = len(s["rounds"]) + 1
    out, paths = args.out, s["paths"]
    urls_file = os.path.join(out, f"r{n}-urls.txt")
    with open(urls_file, "w", encoding="utf-8") as f:
        f.write("\n".join(s["urls"]) + "\n")
    round_paths = {"evidence": os.path.join(out, f"r{n}-evidence.jsonl"),
                   "trials": os.path.join(out, f"r{n}-trials.jsonl"),
                   "policy": paths["policy"], "bodies": paths["bodies"]}
    policy_in = args.policy_in if n == 1 else (paths["policy"] if os.path.exists(paths["policy"]) else None)
    if s["plan_path"]:
        # Execute exactly the checkpointed review, not a later unvalidated edit.
        with open(s["plan_path"], "w", encoding="utf-8") as f:
            json.dump(s["reviewed_plan"], f, ensure_ascii=False, indent=2)
    cmd = crawl_args(args, urls_file, round_paths, plan=s["plan_path"],
                     policy_in=policy_in, pages=args.max_pages - s["pages_used"],
                     requests=args.max_requests - s["requests_used"])
    try:
        proc = run_script(CRAWL, cmd)
        result = json.loads(proc.stdout)
        summary = result["summary"]
        s["frontier"] = [page["url"] for page in result["pages"]
                         if page.get("stopped") == "plan-exhausted" and page["requests"] == 0]
    except subprocess.TimeoutExpired:
        s["stop"] = {"kind": "crawl-error", "reason": "crawl 超时"}
        s["phase"] = "finish"
        return
    except (ValueError, KeyError, TypeError):
        s["stop"] = {"kind": "crawl-error", "reason": tail(proc.stderr or proc.stdout)}
        s["phase"] = "finish"
        return
    s["requests_used"] += int(summary.get("requests") or 0)
    s["pages_used"] += int(summary.get("pages") or 0)
    s["tr_round"] = []
    for path, key in ((round_paths["evidence"], "ev_all"), (round_paths["trials"], "tr_all")):
        try:
            rows = list(evidence.read_jsonl(path))
        except (OSError, ValueError):
            rows = []
        s[key].extend(rows)
        if key == "tr_all":
            s["tr_round"] = rows
    evidence.write_jsonl(paths["evidence"], s["ev_all"])
    evidence.write_jsonl(paths["trials"], s["tr_all"])
    s["summary"] = summary
    s["phase"] = "discover"


def _discover(s):
    c = s["config"]
    seen = set(s["seen"])
    found = discover_round(s["tr_round"], s["allowed_hosts"], seen,
                           c["max_discovered"] - s["discovered_total"]) if c["discover"] else []
    s["seen"] = sorted(seen)
    s["discovered_total"] += len(found)
    pending = list(dict.fromkeys(s["frontier"] + found))
    s["frontier_pending"] = len(pending)
    s["advice"] = advice.recommend(s["ev_all"]) if s["ev_all"] else None
    s["rounds"].append({"n": len(s["rounds"]) + 1, "urls": len(s["urls"]),
                        "crawl": slim_summary(s.pop("summary")), "plan": s["plan_info"],
                        "discovered": len(found), "advice_action": (s["advice"] or {}).get("next", {}).get("action"),
                        "requests_used": s["requests_used"], "pages_used": s["pages_used"]})
    s["urls"] = next_urls(pending, s["ev_all"], max(0, c["max_pages"] - s["pages_used"]))
    s.pop("tr_round", None)
    s["phase"] = "decide"


def report(s, stop=None):
    c, paths = s["config"], s["paths"]
    stop = stop or s["stop"]
    return {"schema": SCHEMA, "command": "agent", "ok": stop["kind"] not in FAILED_KINDS,
            "seeds": s["seeds"], "out": c["out"],
            "budget": {"max_requests": c["max_requests"], "requests": s["requests_used"],
                       "max_pages": c["max_pages"], "pages": s["pages_used"],
                       "max_rounds": c["max_rounds"], "rounds": len(s["rounds"])},
            "analysis": s["analysis"], "rounds": s["rounds"], "advice": s["advice"], "stop": stop,
            "artifacts": {"evidence": paths["evidence"] if s["ev_all"] else None,
                          "trials": paths["trials"] if s["tr_all"] else None,
                          "plan": s["plan_path"],
                          "policy": paths["policy"] if os.path.exists(paths["policy"]) else None,
                          "bodies": paths["bodies"] if c["discover"] and os.path.isdir(paths["bodies"]) else None,
                          "report": paths["report"]}}


def _finish(s):
    s["report"] = report(s)
    with open(s["paths"]["report"], "w", encoding="utf-8") as f:
        json.dump(s["report"], f, ensure_ascii=False, indent=2)
    s["phase"] = "end"


NODES = {"analyze": _analyze, "crawl": _crawl, "discover": _discover,
         "plan": _plan, "review": _review, "decide": _decide, "finish": _finish}


def step(state, node):
    # A fresh JSON tree prevents checkpoint snapshots from sharing mutable lists.
    state = json.loads(json.dumps(state))
    NODES[node](state)
    return state


def event(node, state):
    return {"event": "node", "node": node, "phase": state["phase"],
            "rounds": len(state["rounds"]), "requests": state["requests_used"]}


def run(state, emit=None):
    while route(state) != "end":
        node = route(state)
        state = step(state, node)
        if emit:
            emit(event(node, state))
    return state["report"]


def print_text(report):
    budget = report["budget"]
    print(f"种子 {len(report['seeds'])} 个，host {len(report['analysis'])} 个；"
          f"预算 {budget['requests']}/{budget['max_requests']} 请求、"
          f"{budget['pages']}/{budget['max_pages']} 页")
    for item in report["analysis"]:
        print(f"  分析 {item['host']}: {item['shape']}"
              f"（robots={item.get('robots')}）")
    for rd in report["rounds"]:
        s = rd["crawl"]
        got = " ".join(f"{k}={v}" for k, v in (s.get("outcomes") or {}).items() if v)
        print(f"第 {rd['n']} 轮: URL {rd['urls']}，页面 {s.get('pages')}，"
              f"请求 {s.get('requests')}，{got or '无结局'}")
        plan = rd.get("plan") or {}
        if plan:
            print(f"  计划: 分配 {plan.get('allocated')} 请求，"
                  f"frontier 增补 {plan.get('frontier', {}).get('requests', 0)} 请求")
        if rd.get("discovered"):
            print(f"  路径发现: +{rd['discovered']} 条同站链接")
    if report.get("advice"):
        nxt = report["advice"]["next"]
        print(f"建议: {nxt['action']}（{nxt['layer']} / {nxt['countermeasure']}，"
              f"置信度 {nxt['confidence']}）")
        print(f"  {nxt['why']}")
    stop = report["stop"]
    print(f"停止: {stop['kind']} — {stop['reason']}")
    art = report["artifacts"]
    print("产物: " + "  ".join(f"{k}={v}" for k, v in art.items() if v))
