#!/usr/bin/env python3
"""bandit.py — 离线推演：给定每臂胜率，比较选臂策略的请求开销与成功率。

不联网、不发请求。胜率来源二选一：
    --arms   native=0.4,cffi:chrome=0.7
    --trials crawl 产出的试验行 JSONL（只取可归因、非限频的尝试）

推演回答“如果这个胜率成立，thompson / ucb1 / fixed / round_robin 谁更省请求”，
不替代真实对照：胜率本身仍须来自观测，推演不产生新证据。

用法:
    scripts/bandit.py --arms native=0.4,cffi:chrome=0.7 --pages 200 --seed 7
    scripts/bandit.py --trials recon/demo/trials.jsonl --json

退出码: 0 成功；2 用法或输入错误
"""

import argparse
import json
import sys

from reconlib import evidence, fetch, simulate
from reconlib.policy import ALGORITHMS


def parse_policies(text):
    out = []
    for raw in (text or "").split(","):
        name = raw.strip()
        if not name:
            continue
        if name not in ALGORITHMS:
            raise ValueError(f"未知策略: {name}")
        if name not in out:
            out.append(name)
    if not out:
        raise ValueError("至少需要一个策略")
    return out


def rates_from_args(args):
    if bool(args.arms) == bool(args.trials):
        raise ValueError("--arms 与 --trials 必须二选一")
    if args.arms:
        rates = simulate.parse_rates(args.arms)
        for arm in rates:
            fetch.parse_arm(arm)
        return rates, "arms"
    try:
        rows = evidence.read_jsonl(args.trials)
    except (OSError, ValueError) as e:
        raise ValueError(f"读试验行失败: {e}")
    rates = simulate.rates_from_trials(rows)
    for arm in rates:
        fetch.parse_arm(arm)
    if not rates:
        raise ValueError("试验行里没有可用的胜率样本（需 ok / degraded / blocked 且非限频）")
    return rates, "trials"


def print_table(body):
    rates = body["rates"]
    print(f"胜率来源: {body['source']}，页数 {body['pages']}，seed {body['seed']}")
    print(" ".join(f"{arm}={rate:.2f}" for arm, rate in rates.items()))
    print()
    header = f"{'policy':<12} {'requests':<9} {'successes':<10} {'access':<8} {'req/success':<12} pulls"
    print(header)
    for row in body["rows"]:
        if row["policy"] == "best-single":
            pulls = f"{row['arm']} only"
            access = f"{row['expected_access_rate']:.2f}*"
            rps = f"{1 / row['expected_access_rate']:.2f}*" if row["expected_access_rate"] else "-"
            successes = "-"
        else:
            pulls = " ".join(f"{a}={n}" for a, n in row["pulls"].items())
            access = f"{row['access_rate']:.3f}"
            rps = f"{row['requests_per_success']:.2f}" if row["requests_per_success"] else "-"
            successes = str(row["successes"])
        print(f"{row['policy']:<12} {row['requests']:<9} {successes:<10} "
              f"{access:<8} {rps:<12} {pulls}")
    if any(r["policy"] == "best-single" for r in body["rows"]):
        print("\n* best-single 为期望值，不是采样值。")


def main():
    ap = argparse.ArgumentParser(description="离线推演选臂策略：请求开销与成功率")
    ap.add_argument("--arms", help="每臂胜率：native=0.4,cffi:chrome=0.7")
    ap.add_argument("--trials", help="从试验行 JSONL 估计胜率（crawl --trial-out 的产物）")
    ap.add_argument("--pages", type=int, default=200, help="模拟页数，默认 200")
    ap.add_argument("--policies", default="thompson,ucb1,fixed,round_robin",
                    help="逗号分隔的策略列表")
    ap.add_argument("--seed", type=int, default=7, help="随机种子，默认 7")
    ap.add_argument("--min-pulls", type=int, default=1, help="每臂预热次数，默认 1")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而不是表格")
    args = ap.parse_args()

    if args.pages < 1 or args.min_pulls < 0:
        ap.error("--pages 必须 >=1，--min-pulls 不能为负")
    try:
        pump = parse_policies(args.policies)
        rates, source = rates_from_args(args)
    except ValueError as e:
        ap.error(str(e))

    rows = [simulate.simulate(name, rates, pages=args.pages, seed=args.seed,
                              min_pulls=args.min_pulls) for name in pump]
    rows.append(simulate.best_single(rates, pages=args.pages))

    body = {
        "command": "bandit",
        "ok": True,
        "source": source,
        "rates": rates,
        "pages": args.pages,
        "seed": args.seed,
        "min_pulls": args.min_pulls,
        "rows": rows,
    }
    if args.json:
        print(json.dumps(body, ensure_ascii=False, indent=2))
    else:
        print_table(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
