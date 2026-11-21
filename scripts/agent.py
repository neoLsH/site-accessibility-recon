#!/usr/bin/env python3
"""Bounded recon CLI; phase logic lives in reconlib.loop."""
import argparse
import json
import os
import sys

from reconlib import UA_DEFAULT, loop
from reconlib.loop import DEFAULT_ARMS, die


def parser():
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
    ap.add_argument("--runtime", choices=("stdlib", "langgraph"), default="stdlib")
    ap.add_argument("--thread-id", help="Required stable LangGraph checkpoint identity")
    ap.add_argument("--resume", action="store_true", help="Resume saved thread/configuration")
    ap.add_argument("--interrupt", action="store_true", help="Pause before plan review in rounds 2+")
    ap.add_argument("--stream", action="store_true", help="JSONL node events followed by agent/1 report")
    ap.add_argument("--memory", action="store_true", help="Opt in to process-local checkpoints")
    return ap


def main():
    ap = parser()
    args = ap.parse_args()
    if args.stream and args.json:
        ap.error("--stream and --json are mutually exclusive")
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

    if args.runtime == "stdlib" and (args.resume or args.interrupt or args.memory or args.thread_id):
        ap.error("--resume / --interrupt / --memory / --thread-id require --runtime langgraph")
    if args.runtime == "langgraph" and not args.thread_id:
        ap.error("--runtime langgraph requires --thread-id")
    if args.resume and args.memory:
        ap.error("--memory is process-local; CLI --resume requires SQLite")
    if args.resume:
        allowed = {"--out", "--runtime", "--thread-id", "--resume", "--json", "--stream"}
        # Resuming cannot silently replace saved seeds, scope or original budgets.
        for token in sys.argv[1:]:
            if token.startswith("--") and token.split("=", 1)[0] not in allowed:
                ap.error("--resume uses saved configuration; do not supply " + token)
        if args.urls:
            ap.error("--resume uses saved seeds; do not supply URLs")
    seeds = list(args.urls)
    try:
        if args.from_file:
            seeds.extend(loop.read_urls_file(args.from_file))
        seeds = loop.normalize(seeds)
        if not seeds and not args.resume:
            ap.error("需要至少一个种子 URL（位置参数或 --from）")
        emit = (lambda event: print(json.dumps(event, ensure_ascii=False), flush=True)) if args.stream else None
        if args.runtime == "langgraph":
            from reconlib import graph
            report = graph.run_cli(vars(args), seeds, emit=emit)
        else:
            report = loop.run(loop.initial_state(vars(args), seeds), emit=emit)
    except (OSError, ValueError, RuntimeError) as exc:
        ap.error(str(exc))
    if args.stream:
        print(json.dumps(report, ensure_ascii=False))
    elif args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        loop.print_text(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
