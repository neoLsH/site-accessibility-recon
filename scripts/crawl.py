#!/usr/bin/env python3
"""crawl.py — 批量取内容：多臂老虎机在客户端臂之间选举，产出证据行与试验行。

每条 URL 先过 robots；允许的 URL 由策略选臂逐级尝试，直到：
    - 拿到内容（ok）
    - 遇到不可归因的失败（限频 / 鉴权 / 内容不存在 / 链路噪声）
    - 候选臂用完，或该 host 连续 blocked 触发熔断
    - 触及 max-pages / max-requests 护栏

护栏（默认值写在模块顶部，自检会校验）：
    max-pages 50、max-requests 150、每 host 间隔 5s、连续 blocked 3 次熔断。
    429 / 5xx 只推迟下一次请求，不自动重试同一 URL。

产物：
    --evidence-out 证据行 JSONL（outcome 口径，可直接喂 stats）
    --trial-out    试验行 JSONL（kind: trial，带 trial_outcome，stats 会拒绝）

边界：臂只能是已有客户端协议栈；不做验证码求解、不轮换出口、不伪造挑战 cookie。

用法:
    scripts/crawl.py --from urls.txt --selector '#content' \
        --arms native,cffi:chrome --policy thompson \
        --evidence-out recon/demo/evidence.jsonl --trial-out recon/demo/trials.jsonl

退出码: 0 = 在护栏内跑完；1 = 提前停止（预算或熔断）；2 = 用法错误
"""

import argparse
import json
import os
import sys
from urllib.parse import urlsplit

from reconlib import evidence, fetch, measure, pacing, robots
from reconlib.classify import classify
from reconlib.policy import ALGORITHMS, Bandit

MAX_PAGES = 50
MAX_REQUESTS = 150
MAX_CONSECUTIVE_BLOCKED = 3
DEFAULT_DELAY = 5.0
DEFAULT_ARMS = "native,cffi:chrome"

RANK = {"ok": 3, "degraded": 2, "blocked": 1}


def host_of(url):
    return urlsplit(url).netloc or url


def parse_arms(text, impersonate="chrome"):
    arms = []
    for raw in (text or "").split(","):
        raw = raw.strip()
        if not raw:
            continue
        if raw == "cffi":
            raw = f"cffi:{impersonate}"
        fetch.parse_arm(raw)
        arms.append(raw)
    if not arms:
        raise ValueError("至少需要一个客户端臂")
    return arms


def rank(outcome):
    return RANK.get(outcome, 0)


def probe_page(url, args, bandit, pace, budget, ev_rows, trials):
    host = host_of(url)
    page = {"url": url, "host": host, "tries": [], "evidence": None, "stopped": None}
    candidates = list(args.arm_list)
    used = 0

    while candidates:
        if used >= budget:
            page["stopped"] = "budget"
            return page, used, f"达到 max-requests={args.max_requests}"
        arm = bandit.select(host, candidates)
        pace.wait(host)
        obs = fetch.fetch_one(url, arm, timeout=args.timeout, ua=args.ua)
        html = measure.is_html(obs["ctype"])
        content = measure.content_len(obs["body"], args.selector) if html else "-"
        verdict = classify(obs, args.selector, args.min_content, content=content)

        if obs.get("skipped"):
            page["tries"].append({"arm": arm, "skipped": True,
                                  "outcome": verdict["outcome"]})
            candidates.remove(arm)
            continue

        used += 1
        state = pace.observe(host, verdict["outcome"], verdict["rate_limited"],
                             pacing.retry_after(obs.get("headers") or ""))
        bandit.update_from_outcome(host, arm, verdict["outcome"], verdict["rate_limited"])
        saved = fetch.save_html(args.out_dir, url, arm, obs["body"])

        trial = evidence.trial_row(url, arm, verdict, attempt=len(page["tries"]) + 1,
                                   elapsed_ms=obs.get("elapsed_ms"))
        if saved:
            trial["saved"] = saved
        trials.append(trial)
        page["tries"].append({
            "arm": arm, "status": obs["status"], "bytes": obs["bytes"],
            "content": content, "outcome": verdict["outcome"],
            "attributable": verdict["attributable"],
            "rate_limited": verdict["rate_limited"], "saved": saved,
        })

        obs["content"] = content
        row = evidence.evidence_row(obs, verdict, route=evidence.route_of(url))
        if page["evidence"] is None or rank(row["outcome"]) > rank(page["evidence"]["outcome"]):
            page["evidence"] = row

        if verdict["outcome"] == "ok":
            page["stopped"] = "ok"
            return page, used, None
        if not verdict["attributable"]:
            page["stopped"] = verdict["outcome"]
            return page, used, None
        candidates.remove(arm)
        if state["aborted"]:
            page["stopped"] = "circuit-open"
            return page, used, f"circuit-open: {state['reason']}"

    if page["evidence"] is None:
        page["stopped"] = page["stopped"] or "no-arm"
        return page, used, "没有可用的客户端臂"
    return page, used, None


def crawl(urls, args):
    bandit = Bandit(args.arm_list, algorithm=args.policy, seed=args.seed,
                    min_pulls=args.min_pulls)
    pace = pacing.Pacing(delay=args.delay,
                         max_consecutive_blocked=args.max_consecutive_blocked)
    pages, ev_rows, trials = [], [], []
    requests = 0
    stopped = None
    warned = set()

    for url in urls:
        if len(pages) >= args.max_pages:
            stopped = f"达到 max-pages={args.max_pages}"
            break
        host = host_of(url)
        if not args.i_have_permission:
            allowed = robots.decision(url, timeout=args.timeout)
            if allowed == "deny":
                pages.append({"url": url, "host": host, "tries": [], "evidence": None,
                              "stopped": "robots-deny", "skipped": "robots-deny"})
                continue
            if allowed == "unknown" and host not in warned:
                warned.add(host)
                print(f"# 警告：{host} 的 robots.txt 取不到，合规状态未知", file=sys.stderr)
        if pace.aborted(host):
            reason = pace.snapshot(host)["reason"]
            pages.append({"url": url, "host": host, "tries": [], "evidence": None,
                          "stopped": "circuit-open",
                          "skipped": f"circuit-open: {reason}"})
            continue

        page, used, stop = probe_page(url, args, bandit, pace,
                                      args.max_requests - requests, ev_rows, trials)
        requests += used
        pages.append(page)
        if page["evidence"]:
            ev_rows.append(page["evidence"])
        if stop:
            stopped = stopped or stop
            # 熔断只封这个 host：余下同 host 的 URL 会在顶部被记为 skipped，其他 host 仍可跑
            if not stop.startswith("circuit-open"):
                break

    if args.evidence_out:
        evidence.write_jsonl(args.evidence_out, ev_rows)
    if args.trial_out:
        evidence.write_jsonl(args.trial_out, trials)

    outcomes = {k: 0 for k in evidence.OUTCOMES}
    for row in ev_rows:
        outcomes[row["outcome"]] += 1
    hosts = sorted({host_of(u) for u in urls})
    summary = {
        "requested": len(urls),
        "pages": len(pages),
        "requests": requests,
        "skipped_robots": sum(1 for p in pages if p.get("skipped") == "robots-deny"),
        "circuit_open": sum(1 for p in pages
                            if str(p.get("skipped", "")).startswith("circuit-open")),
        "outcomes": outcomes,
        "stopped": stopped,
        "policy": args.policy,
        "arms": args.arm_list,
        "seed": args.seed,
        "hosts": [bandit.snapshot(h) for h in hosts],
    }
    return pages, summary, ev_rows, trials


def print_table(result, args):
    pages, summary = result[0], result[1]
    print(f"{'url':<48} {'tries':<6} {'outcome':<10} stopped")
    for page in pages:
        outcome = page["evidence"]["outcome"] if page["evidence"] else "-"
        print(f"{page['url'][:48]:<48} {len(page['tries']):<6} {outcome:<10} "
              f"{page.get('skipped') or page['stopped']}")
    print()
    print(f"请求 {summary['requests']}/{args.max_requests}，页面 {summary['pages']}/"
          f"{summary['requested']}，策略 {summary['policy']}，"
          f"臂 {','.join(summary['arms'])}")
    print(f"结局: " + " ".join(f"{k}={v}" for k, v in summary["outcomes"].items() if v))
    if args.evidence_out:
        print(f"证据行 -> {args.evidence_out}")
    if args.trial_out:
        print(f"试验行 -> {args.trial_out}")
    if summary["stopped"]:
        print(f"提前停止: {summary['stopped']}")


def main():
    ap = argparse.ArgumentParser(description="批量取内容：多臂选举客户端臂，产出证据行")
    ap.add_argument("urls", nargs="*", help="目标 URL")
    ap.add_argument("--from", dest="from_file", help="从文件读 URL，一行一个，# 为注释")
    ap.add_argument("--selector", default="", help="内容区选择器（#id / .class / 标签名）")
    ap.add_argument("--min-content", type=int, default=200, help="内容区字符下限，默认 200")
    ap.add_argument("--arms", default=DEFAULT_ARMS,
                    help=f"逗号分隔的客户端臂，默认 {DEFAULT_ARMS}")
    ap.add_argument("--impersonate", default="chrome", help="裸 cffi 臂使用的仿真目标")
    ap.add_argument("--policy", choices=ALGORITHMS, default="thompson",
                    help="选举策略，默认 thompson")
    ap.add_argument("--seed", type=int, help="thompson 的随机种子（复现用）")
    ap.add_argument("--min-pulls", type=int, default=1, help="每臂预热次数，默认 1")
    ap.add_argument("--max-pages", type=int, default=MAX_PAGES,
                    help=f"最多处理多少 URL，默认 {MAX_PAGES}")
    ap.add_argument("--max-requests", type=int, default=MAX_REQUESTS,
                    help=f"最多发多少请求（不含 robots），默认 {MAX_REQUESTS}")
    ap.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                    help=f"每 host 请求间隔，默认 {DEFAULT_DELAY}s")
    ap.add_argument("--max-consecutive-blocked", type=int,
                    default=MAX_CONSECUTIVE_BLOCKED,
                    help=f"连续 blocked 几次熔断，默认 {MAX_CONSECUTIVE_BLOCKED}")
    ap.add_argument("--timeout", type=int, default=20)
    ap.add_argument("--ua", default=fetch.UA_DEFAULT)
    ap.add_argument("--out-dir", help="保存每次尝试的响应体")
    ap.add_argument("--evidence-out", help="证据行 JSONL 输出路径")
    ap.add_argument("--trial-out", help="试验行 JSONL 输出路径")
    ap.add_argument("--i-have-permission", action="store_true",
                    help="跳过 robots 检查（仅在你已获站点方许可时使用）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而不是表格")
    args = ap.parse_args()

    if args.max_pages < 1 or args.max_requests < 1:
        ap.error("--max-pages / --max-requests 必须 >= 1")
    if args.delay < 0:
        ap.error("--delay 不能为负")
    try:
        args.arm_list = parse_arms(args.arms, args.impersonate)
    except ValueError as e:
        ap.error(str(e))

    urls = list(args.urls)
    if args.from_file:
        try:
            with open(args.from_file, encoding="utf-8") as f:
                for line in f:
                    line = line.split("#", 1)[0].strip()
                    if line:
                        urls.append(line)
        except OSError:
            sys.exit(f"找不到文件: {args.from_file}")
    if not urls:
        ap.print_help()
        return 2
    urls = [u if u.startswith(("http://", "https://")) else f"https://{u}" for u in urls]

    pages, summary, ev_rows, trials = crawl(urls, args)
    rc = 0 if summary["stopped"] is None else 1
    if args.json:
        print(json.dumps({
            "command": "crawl",
            "ok": rc == 0,
            "summary": summary,
            "pages": pages,
            "evidence_out": os.path.abspath(args.evidence_out) if args.evidence_out else None,
            "trial_out": os.path.abspath(args.trial_out) if args.trial_out else None,
        }, ensure_ascii=False, indent=2, default=str))
    else:
        print_table((pages, summary), args)
        if summary["stopped"]:
            print("建议：降低请求预算或先处理熔断原因；不要重试已被 robots 禁止的 URL。")
    return rc


if __name__ == "__main__":
    sys.exit(main())
