#!/usr/bin/env python3
"""crawl.py — 批量取内容：多臂老虎机在客户端臂之间选举，产出证据行与试验行。

每条 URL 先过 robots；允许的 URL 由策略选臂逐级尝试，直到：
    - 拿到内容（ok）
    - 遇到不可归因的失败（限频 / 鉴权 / 内容不存在 / 链路噪声）
    - 候选臂用完，或该 host 连续 blocked 触发熔断
    - 触及 max-pages / max-requests 护栏

护栏（默认值写在模块顶部，自检会校验）：
    max-pages 50、max-requests 150、每 host 间隔 5s、连续 blocked 3 次熔断。
    连续 2 次限频进入 host 冷却 30s（或 Retry-After，取较长者），冷却期内该
    host 不再发请求；限频样本不更新选臂后验。

产物：
    --evidence-out 证据行 JSONL（outcome 口径，可直接喂 stats）
    --trial-out    试验行 JSONL（kind: trial，带 trial_outcome，stats 会拒绝）

计划：
    --plan 读取 explore --plan-out 的配额，只发计划内 (host + 路由类, 臂) 的请求；
    计划外或额度用尽的路由零请求（含 robots.txt），页面记 plan-exhausted。

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
from reconlib import evidence, explore, fetch, policy_store
from reconlib.policy import ALGORITHMS

MAX_PAGES = 50
MAX_REQUESTS = 150
MAX_CONSECUTIVE_BLOCKED = 3
DEFAULT_DELAY = 5.0
DEFAULT_ARMS = "native,cffi:chrome"
DEFAULT_RATE_LIMIT_BUDGET = 2
DEFAULT_RATE_LIMIT_COOLDOWN = 30.0

def crawl(urls, args):
    from reconlib import arms as armreg
    from reconlib.session import ArmSession, host_of

    registry = armreg.load(args.arms_file)
    arm_list = armreg.parse_list(args.arms, registry, args.impersonate)
    require = armreg.parse_require(args.require)
    save = (lambda url, arm, body: fetch.save_html(args.out_dir, url, arm, body)
            if args.out_dir else None)
    session = ArmSession(
        arm_list, registry=registry, policy=args.policy, seed=args.seed,
        min_pulls=args.min_pulls, delay=args.delay, timeout=args.timeout,
        ua=args.ua, max_consecutive_blocked=args.max_consecutive_blocked,
        save=save, require=require, permission=args.i_have_permission,
        cost_weight=args.cost_weight, prior_strength=args.prior_strength,
        half_life=args.half_life, block_budget=args.block_budget,
        cookies=not args.no_cookies,
        rate_limit_budget=args.rate_limit_budget,
        rate_limit_cooldown=args.rate_limit_cooldown,
        plan=args.plan_quota)
    policy_in = None
    if args.policy_in:
        policy_in = policy_store.restore(session.bandit, args.policy_in)

    pages, requests, stopped = [], 0, None
    warned = set()

    def warn(host):
        if host not in warned:
            warned.add(host)
            print(f"# 警告：{host} 的 robots.txt 取不到，合规状态未知",
                  file=sys.stderr)

    try:
        for url in urls:
            if len(pages) >= args.max_pages:
                stopped = f"达到 max-pages={args.max_pages}"
                break
            page = session.probe(
                url, selector=args.selector, min_content=args.min_content,
                max_attempts=args.max_requests - requests, require=require,
                permission=args.i_have_permission, warn=warn)
            requests += page["requests"]
            pages.append(page)
            if page["halt"] == "budget":
                stopped = stopped or f"达到 max-requests={args.max_requests}"
                break
            if page["halt"] == "no-arm":
                stopped = stopped or "没有可用的客户端臂"
                break
            if page["halt"] == "circuit-open":
                stopped = stopped or f"circuit-open: {page.get('reason', '连续 blocked')}"
                # 熔断只封这个 host：余下 URL 会在 probe 顶部被记为 skipped
            if page["halt"] == "rate-limit-cooldown":
                stopped = stopped or (f"rate-limit-cooldown: 剩余 "
                                      f"{page.get('cooldown_left', 0):.0f}s")
                # 冷却同样只封这个 host；其他 host 照常探测
    finally:
        session.close()

    ev_rows = [p["evidence"] for p in pages if p["evidence"]]
    trials = session.trials

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
        "suspended_pages": sum(1 for p in pages
                               if p.get("halt") == "all-arms-suspended"),
        "cooldown_pages": sum(1 for p in pages
                              if p.get("halt") == "rate-limit-cooldown"),
        "rate_limited_pages": sum(
            1 for p in pages if any(t.get("rate_limited") for t in p["tries"])),
        "plan_exhausted_pages": sum(1 for p in pages
                                    if p.get("stopped") == "plan-exhausted"),
        "plan": args.plan_quota.snapshot() if args.plan_quota else None,
        "outcomes": outcomes,
        "stopped": stopped,
        "policy": args.policy,
        "block_budget": args.block_budget,
        "rate_limit_budget": args.rate_limit_budget,
        "rate_limit_cooldown": args.rate_limit_cooldown,
        "arms": arm_list,
        "seed": args.seed,
        "policy_in": policy_in,
        "policy_out": None,
        "plan_path": args.plan,
        "hosts": [session.snapshot(h) for h in hosts],
    }
    if args.policy_out:
        try:
            summary["policy_out"] = policy_store.dump(
                session.bandit, args.policy_out,
                meta={"command": "crawl", "requests": requests,
                      "pages": len(pages)})["path"]
        except (OSError, ValueError) as e:
            print(f"# 警告：策略保存失败: {e}", file=sys.stderr)
            summary["policy_out_error"] = str(e)
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
    if summary.get("suspended_pages"):
        print(f"暂停页: {summary['suspended_pages']}（该路由候选臂连续 blocked，"
              f"本轮已暂停）")
    if summary.get("cooldown_pages"):
        print(f"冷却页: {summary['cooldown_pages']}（{summary['rate_limited_pages']} "
              f"页出现限频，host 冷却 {summary['rate_limit_cooldown']:.0f}s 内不再请求）")
    plan = summary.get("plan")
    if plan:
        print(f"计划: 用 {plan['used']}/{plan['planned']} 请求，剩余 "
              f"{plan['remaining']}，计划外/额度用尽 {summary['plan_exhausted_pages']} 页")
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
    ap.add_argument("--arms-file",
                    help="外部臂配置 JSON（默认 ~/.config/site-recon/arms.json）")
    ap.add_argument("--require", default="", help="硬性能力要求，如 js（逗号分隔）")
    ap.add_argument("--impersonate", default="chrome", help="裸 cffi 臂使用的仿真目标")
    ap.add_argument("--policy", choices=ALGORITHMS, default="thompson",
                    help="选举策略，默认 thompson")
    ap.add_argument("--seed", type=int, help="thompson 的随机种子（复现用）")
    ap.add_argument("--min-pulls", type=int, default=1, help="每臂预热次数，默认 1")
    ap.add_argument("--cost-weight", type=float, default=0.1,
                    help="成本惩罚权重（5000ms 归一化），默认 0.1；0 关闭")
    ap.add_argument("--prior-strength", type=float, default=4.0,
                    help="分层先验伪计数，默认 4；0 关闭")
    ap.add_argument("--half-life", type=float, default=0.0,
                    help="按更新次数指数遗忘的半衰期，默认 0（不遗忘）")
    ap.add_argument("--block-budget", type=int, default=3,
                    help="同一路由连续 blocked 几次暂停该臂，默认 3；0 关闭")
    ap.add_argument("--rate-limit-budget", type=int,
                    default=DEFAULT_RATE_LIMIT_BUDGET,
                    help="同一 host 连续几次限频进入冷却，默认 2；0 关闭")
    ap.add_argument("--rate-limit-cooldown", type=float,
                    default=DEFAULT_RATE_LIMIT_COOLDOWN,
                    help="限频冷却时长（秒），默认 30；Retry-After 更长时取更长者")
    ap.add_argument("--no-cookies", action="store_true",
                    help="不保留会话状态（默认按臂+host 复用本轮服务端下发的 cookie）")
    ap.add_argument("--policy-in", help="读取上一轮策略档案（recon-policy/1）")
    ap.add_argument("--policy-out", help="把本轮后验写到该路径，供下一轮热启动")
    ap.add_argument("--plan",
                    help="按探索计划执行（recon explore --plan-out 的产物）："
                         "只发计划内的请求，计划外路由零请求")
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
    if args.cost_weight < 0:
        ap.error("--cost-weight 不能为负")
    if args.prior_strength < 0:
        ap.error("--prior-strength 不能为负")
    if args.half_life < 0:
        ap.error("--half-life 不能为负")
    if args.block_budget < 0:
        ap.error("--block-budget 不能为负")
    if args.rate_limit_budget < 0:
        ap.error("--rate-limit-budget 不能为负")
    if args.rate_limit_cooldown < 0:
        ap.error("--rate-limit-cooldown 不能为负")
    from reconlib import arms as armreg
    try:
        registry = armreg.load(args.arms_file)
        args.arm_list = armreg.parse_list(args.arms, registry, args.impersonate)
        armreg.parse_require(args.require)
    except ValueError as e:
        ap.error(str(e))

    args.plan_quota = None
    if args.plan:
        try:
            with open(args.plan, encoding="utf-8") as f:
                body = json.load(f)
            args.plan_quota = explore.Quota(body)
        except OSError:
            sys.exit(f"找不到计划文件: {args.plan}")
        except ValueError as e:
            ap.error(f"计划不可用: {e}")
        if args.plan_quota.planned and not (
                args.plan_quota.all_arms() & set(args.arm_list)):
            print("# 警告：计划里的臂与 --arms 无交集，所有路由都会 plan-exhausted",
                  file=sys.stderr)

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

    try:
        pages, summary, ev_rows, trials = crawl(urls, args)
    except ValueError as e:
        ap.error(str(e))
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
