#!/usr/bin/env python3
"""agent.py — 默认可访问性探索 Agent：形态分析 → 探索 → 计划 → 配额执行 → 建议。

这是一个**有界**的编排循环，不是常驻调度器：轮数、页面、请求都有上限，
每轮结束都要么进入下一轮、要么给出停止原因。它只调用本仓库已有的
crawl / explore / advice 与原子层，不新增任何绕过手段。

一轮做什么：
    1. 站点分析：每个 host 取一个种子判形态；挑战页 / 登录墙 / 链路失败 /
       robots 禁止的 host 直接拦下，不再发请求；
    2. 探索：crawl 多臂选举，产出证据行与试验行；
    3. 路径发现：从本轮留存 HTML 里提取同站链接（静态资源跳过），作为 frontier；
    4. 规划：explore 把试验行变成配额计划，再把 frontier 的新路由类补进计划；
    5. 执行：crawl --plan 按配额跑下一轮，计划外路由零请求；
    6. 收敛：advise 给出动作；终态动作、限频冷却、熔断、全臂暂停立即停手。

护栏：请求 / 页面 / 轮数三重上限；robots、节流、熔断、限频冷却与配额
全部沿用底层实现；不求解验证码、不轮换出口、不绕过登录或付费墙。
分析阶段每个 host 只发 1 个页面请求（robots 检查另计，不计入 --max-requests）。

产物（全部写进 --out）：
    evidence.jsonl / trials.jsonl    跨轮累计的证据行与试验行
    r<N>-evidence.jsonl / r<N>-trials.jsonl  单轮原始产物
    r<N>-urls.txt                    单轮 URL 集合
    plan.json                        最后一轮执行的配额计划（含 frontier 增补）
    policy.json                      最后一轮选臂后验（recon-policy/1）
    bodies/                          --discover 时留存的响应体（供路径发现）
    agent.json                       本次运行的 agent/1 报告

用法:
    scripts/agent.py https://example.com/ --selector '#content' --out recon/demo
    scripts/agent.py --from seeds.txt --out recon/demo --max-rounds 3 \
        --max-requests 40 --budget 12 --json

退出码: 0 = 在护栏内跑完（含预算 / 轮数 / 终态建议 / 合规拦下）；
        1 = 安全或配置原因停手（限频冷却、熔断、全臂暂停、没有可用臂、crawl 失败）；
        2 = 用法错误。
"""

import argparse
import json
import os
import subprocess
import sys
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import shape  # noqa: E402
from reconlib import (UA_DEFAULT, advice, agent as agentlib, arms as armreg,
                      discover, evidence, fetch, robots)  # noqa: E402

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
        body = agentlib.augment(body, urls, arm_list, per_url=1,
                                max_requests=budget)
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
    out, seen = [], set()
    for row in rows:
        if row.get("outcome") not in ("blocked", "degraded"):
            continue
        url = row.get("final_url") or row.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


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


def main():
    ap = argparse.ArgumentParser(
        description="默认可访问性探索 Agent：分析 → 探索 → 计划 → 配额执行（有界）")
    ap.add_argument("urls", nargs="*", help="种子 URL")
    ap.add_argument("--from", dest="from_file", help="从文件读种子 URL，一行一个")
    ap.add_argument("--out", required=True, help="产物目录，如 recon/<代号>")
    ap.add_argument("--selector", default="", help="内容区选择器")
    ap.add_argument("--min-content", type=int, default=200,
                    help="内容区字符下限，默认 200")
    ap.add_argument("--max-rounds", type=int, default=2, help="最多跑几轮，默认 2")
    ap.add_argument("--max-pages", type=int, default=20,
                    help="整次运行的页面上限，默认 20")
    ap.add_argument("--max-requests", type=int, default=40,
                    help="整次运行的请求上限（含站点分析），默认 40")
    ap.add_argument("--budget", type=int, default=12,
                    help="单轮探索计划的请求预算，默认 12")
    ap.add_argument("--max-per-context", type=int, default=6,
                    help="探索计划单上下文请求上限，默认 6")
    ap.add_argument("--discover", action=argparse.BooleanOptionalAction,
                    default=True, help="从留存 HTML 发现同站链接，默认开启")
    ap.add_argument("--max-discovered", type=int, default=20,
                    help="整次运行最多发现多少条新链接，默认 20")
    ap.add_argument("--arms", default=DEFAULT_ARMS,
                    help=f"逗号分隔的客户端臂，默认 {DEFAULT_ARMS}")
    ap.add_argument("--arms-file", help="外部臂配置 JSON")
    ap.add_argument("--require", default="", help="硬性能力要求，如 js")
    ap.add_argument("--impersonate", default="chrome", help="裸 cffi 臂的仿真目标")
    ap.add_argument("--policy", default="thompson",
                    choices=("thompson", "ucb1", "fixed", "round_robin"),
                    help="选臂策略，默认 thompson")
    ap.add_argument("--policy-in", help="首轮热启动：读取 recon-policy/1 策略档案")
    ap.add_argument("--seed", type=int, help="策略随机种子（复现用）")
    ap.add_argument("--min-pulls", type=int, default=1, help="每臂预热次数，默认 1")
    ap.add_argument("--cost-weight", type=float, default=0.1,
                    help="成本惩罚权重，默认 0.1；0 关闭")
    ap.add_argument("--prior-strength", type=float, default=4.0,
                    help="分层先验伪计数，默认 4；0 关闭")
    ap.add_argument("--half-life", type=float, default=0.0,
                    help="指数遗忘半衰期，默认 0（不遗忘）")
    ap.add_argument("--block-budget", type=int, default=3,
                    help="同路由连续 blocked 几次暂停该臂，默认 3；0 关闭")
    ap.add_argument("--rate-limit-budget", type=int, default=2,
                    help="同 host 连续几次限频进入冷却，默认 2；0 关闭")
    ap.add_argument("--rate-limit-cooldown", type=float, default=30.0,
                    help="限频冷却秒数，默认 30；Retry-After 更长时取更长者")
    ap.add_argument("--delay", type=float, default=5.0,
                    help="同 host 请求间隔，默认 5s")
    ap.add_argument("--timeout", type=int, default=20, help="单请求超时，默认 20s")
    ap.add_argument("--ua", default=UA_DEFAULT, help="统一 User-Agent")
    ap.add_argument("--no-cookies", action="store_true",
                    help="不保留本轮会话状态")
    ap.add_argument("--i-have-permission", action="store_true",
                    help="跳过 robots 检查（仅在你已获站点方许可时使用）")
    ap.add_argument("--json", action="store_true", help="输出单个 JSON 对象")
    args = ap.parse_args()

    if args.max_rounds < 1 or args.max_pages < 1 or args.max_requests < 1:
        die(ap, "--max-rounds / --max-pages / --max-requests 必须 >= 1")
    if args.budget < 1 or args.max_per_context < 1:
        die(ap, "--budget / --max-per-context 必须 >= 1")
    if args.max_discovered < 0:
        die(ap, "--max-discovered 不能为负")
    for name in ("delay", "cost_weight", "prior_strength", "half_life",
                 "rate_limit_cooldown"):
        if getattr(args, name) < 0:
            die(ap, f"--{name.replace('_', '-')} 不能为负")
    for name in ("block_budget", "rate_limit_budget", "min_pulls"):
        if getattr(args, name) < 0:
            die(ap, f"--{name.replace('_', '-')} 不能为负")

    seeds = list(args.urls)
    if args.from_file:
        try:
            seeds.extend(read_urls_file(args.from_file))
        except OSError:
            sys.exit(f"找不到文件: {args.from_file}")
    seeds = normalize(seeds)
    if not seeds:
        die(ap, "需要至少一个种子 URL（位置参数或 --from）")

    try:
        registry = armreg.load(args.arms_file)
        arm_list = arm_names(args.arms, registry, args.impersonate)
        armreg.parse_require(args.require)
        cfg = shape.load_signals(shape.DEFAULT_SIGNALS)
    except ValueError as e:
        die(ap, str(e))

    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    paths = {
        "evidence": os.path.join(out, "evidence.jsonl"),
        "trials": os.path.join(out, "trials.jsonl"),
        "plan": os.path.join(out, "plan.json"),
        "policy": os.path.join(out, "policy.json"),
        "bodies": os.path.join(out, "bodies"),
        "report": os.path.join(out, "agent.json"),
    }

    analysis, analysis_requests = analyze(seeds, args, cfg)
    gated = {item["host"] for item in analysis if item["shape"] in GATED}
    allowed_hosts = {urlsplit(u).netloc for u in seeds} - gated
    urls = [u for u in seeds if urlsplit(u).netloc in allowed_hosts]

    requests_used, pages_used = analysis_requests, 0
    seen, frontier = set(seeds), []
    discovered_total, frontier_pending = 0, 0
    rounds, ev_all, tr_all = [], [], []
    advice_body, stop, plan_path = None, None, None

    if not urls:
        stop = {"kind": "analysis-gated",
                "reason": "没有可抓取的 host：形态分析或 robots 已拦下全部种子"}

    while stop is None:
        if len(rounds) >= args.max_rounds:
            stop = {"kind": "max-rounds", "reason": "达到轮数上限"}
            break
        requests_left = args.max_requests - requests_used
        pages_left = args.max_pages - pages_used
        if requests_left <= 0:
            stop = {"kind": "budget", "reason": "请求预算已用尽"}
            break
        if pages_left <= 0:
            stop = {"kind": "budget", "reason": "页面预算已用尽"}
            break
        n = len(rounds) + 1
        plan_info, policy_in = None, args.policy_in
        if n > 1:
            policy_in = paths["policy"] if os.path.exists(paths["policy"]) else None
            plan_body, err = build_plan(args, urls, paths, requests_left, arm_list)
            if err:
                stop = {"kind": "plan-unavailable", "reason": err}
                break
            plan_path = paths["plan"]
            plan_info = {"allocated": plan_body.get("allocated"),
                         "steps": len(plan_body.get("steps") or []),
                         "frontier": plan_body.get("frontier")}
            decision = agentlib.decide(
                advice_body, requests_left, args.max_rounds - (n - 1),
                plan_info["steps"], bool(urls), frontier=frontier_pending)
            frontier_pending = 0
            if not decision["run"]:
                stop = {"kind": decision["kind"], "reason": decision["reason"]}
                break

        urls_file = os.path.join(out, f"r{n}-urls.txt")
        with open(urls_file, "w", encoding="utf-8") as f:
            f.write("\n".join(urls) + "\n")
        round_paths = {
            "evidence": os.path.join(out, f"r{n}-evidence.jsonl"),
            "trials": os.path.join(out, f"r{n}-trials.jsonl"),
            "policy": paths["policy"],
            "bodies": paths["bodies"],
        }
        cmd = crawl_args(args, urls_file, round_paths, plan=plan_path,
                         policy_in=policy_in, pages=pages_left,
                         requests=requests_left)
        try:
            proc = run_script(CRAWL, cmd)
        except subprocess.TimeoutExpired:
            stop = {"kind": "crawl-error", "reason": "crawl 超时"}
            break
        try:
            data = json.loads(proc.stdout)
            summary = data["summary"]
        except (ValueError, KeyError, TypeError):
            stop = {"kind": "crawl-error",
                    "reason": tail(proc.stderr or proc.stdout)}
            break

        requests_used += int(summary.get("requests") or 0)
        pages_used += int(summary.get("pages") or 0)
        ev_round, tr_round = [], []
        for path, sink in ((round_paths["evidence"], ev_round),
                           (round_paths["trials"], tr_round)):
            try:
                sink.extend(evidence.read_jsonl(path))
            except (OSError, ValueError):
                pass
        ev_all.extend(ev_round)
        tr_all.extend(tr_round)
        evidence.write_jsonl(paths["evidence"], ev_all)
        evidence.write_jsonl(paths["trials"], tr_all)

        found = []
        if args.discover:
            found = discover_round(
                tr_round, allowed_hosts, seen,
                args.max_discovered - discovered_total)
            discovered_total += len(found)
            frontier.extend(found)
            frontier_pending = len(found)

        advice_body = advice.recommend(ev_all) if ev_all else None
        rounds.append({
            "n": n, "urls": len(urls), "crawl": slim_summary(summary),
            "plan": plan_info, "discovered": len(found),
            "advice_action": (advice_body or {}).get("next", {}).get("action"),
            "requests_used": requests_used, "pages_used": pages_used,
        })

        safety = agentlib.safety_stop(summary)
        if safety:
            stop = safety
            break
        if len(rounds) >= args.max_rounds:
            stop = {"kind": "max-rounds", "reason": "达到轮数上限"}
            break
        if requests_used >= args.max_requests:
            stop = {"kind": "budget", "reason": "请求预算已用尽"}
            break
        urls = next_urls(frontier, ev_all, pages_left)
        frontier = []
        if not urls:
            stop = {"kind": "frontier-empty",
                    "reason": "没有待重试页，也没有新发现的同站链接"}
            break

    rc = 1 if (stop or {}).get("kind") in FAILED_KINDS else 0
    artifacts = {
        "evidence": paths["evidence"] if ev_all else None,
        "trials": paths["trials"] if tr_all else None,
        "plan": plan_path,
        "policy": paths["policy"] if os.path.exists(paths["policy"]) else None,
        "bodies": paths["bodies"] if args.discover and os.path.isdir(paths["bodies"]) else None,
        "report": paths["report"],
    }
    report = {
        "schema": SCHEMA, "command": "agent", "ok": rc == 0,
        "seeds": seeds, "out": out,
        "budget": {
            "max_requests": args.max_requests, "requests": requests_used,
            "max_pages": args.max_pages, "pages": pages_used,
            "max_rounds": args.max_rounds, "rounds": len(rounds),
        },
        "analysis": analysis,
        "rounds": rounds,
        "advice": advice_body,
        "stop": stop,
        "artifacts": artifacts,
    }
    try:
        with open(paths["report"], "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    except OSError as e:
        print(f"# 警告：报告写入失败: {e}", file=sys.stderr)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print_text(report)
    return rc


if __name__ == "__main__":
    sys.exit(main())
