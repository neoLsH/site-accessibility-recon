#!/usr/bin/env python3
"""bandit.py — 离线推演：给定每臂胜率，比较选臂策略的请求开销与成功率。

不联网、不发请求。胜率来源四选一：
    --arms        native=0.4,cffi:chrome=0.7
    --trials      crawl 产出的试验行 JSONL（只取可归因、非限频的尝试）
    --rates-json  按上下文分组的胜率（每组独立选臂、独立更新后验）
    --policy-in   上一轮另存的策略档案（recon-policy/1，按上下文分桶）

推演回答“如果这个胜率成立，thompson / ucb1 / fixed / round_robin 谁更省请求”，
不替代真实对照：胜率本身仍须来自观测，推演不产生新证据。

用法:
    scripts/bandit.py --arms native=0.4,cffi:chrome=0.7 --pages 200 --seed 7
    scripts/bandit.py --trials recon/demo/trials.jsonl --json
    scripts/bandit.py --rates-json rates.json --pages 200 --json
    scripts/bandit.py --policy-in recon/policy.json --json

退出码: 0 成功；2 用法或输入错误
"""

import argparse
import json
import sys

from reconlib import evidence, fetch, policy_store, simulate
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


def load_contexts(path):
    """读上下文分组的胜率：{contexts: [{context, pages?, rates}]}。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError as e:
        raise ValueError(f"读上下文胜率失败: {e}")
    except ValueError as e:
        raise ValueError(f"上下文胜率不是合法 JSON: {e}")
    groups = data.get("contexts") if isinstance(data, dict) else None
    if not isinstance(groups, list) or not groups:
        raise ValueError("上下文胜率需要非空的 contexts 数组")
    out = []
    for item in groups:
        if not isinstance(item, dict):
            raise ValueError("contexts 的每一项都必须是对象")
        ctx = str(item.get("context") or "").strip()
        if not ctx:
            raise ValueError("每个上下文组都需要非空 context")
        raw_rates = item.get("rates")
        if not isinstance(raw_rates, dict) or not raw_rates:
            raise ValueError(f"上下文 {ctx} 缺少非空 rates")
        rates = {}
        for arm, raw in raw_rates.items():
            try:
                rate = float(raw)
            except (TypeError, ValueError):
                raise ValueError(f"上下文 {ctx} 的胜率不是数字: {arm}={raw!r}")
            if not 0.0 <= rate <= 1.0:
                raise ValueError(f"上下文 {ctx} 的胜率应在 0–1: {arm}={rate}")
            rates[str(arm)] = rate
        pages = item.get("pages")
        if pages is not None:
            try:
                pages = int(pages)
            except (TypeError, ValueError):
                raise ValueError(f"上下文 {ctx} 的 pages 必须是整数")
            if pages < 1:
                raise ValueError(f"上下文 {ctx} 的 pages 必须 >= 1")
        out.append({"context": ctx, "pages": pages, "rates": rates})
    return out


def rates_from_args(args):
    sources = [bool(args.arms), bool(args.trials), bool(args.rates_json),
               bool(args.policy_in)]
    if sum(sources) != 1:
        raise ValueError("--arms / --trials / --rates-json / --policy-in 必须四选一")
    if args.rates_json:
        return load_contexts(args.rates_json), "rates-json"
    if args.policy_in:
        body = policy_store.load(args.policy_in)
        return policy_store.context_rates(body), "policy-in"
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
    if rates:
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
    contexts = body.get("contexts") or {}
    if contexts:
        print()
        print(f"按上下文（source={body['source']}）:")
        for ctx, info in contexts.items():
            pulls = " ".join(f"{a}={n}" for a, n in info["pulls"].items())
            wins = " ".join(f"{a}={n}" for a, n in info["wins"].items())
            print(f"  {ctx:<28} pages={info['pages']:<4} pulls={pulls} wins={wins}")


def main():
    ap = argparse.ArgumentParser(description="离线推演选臂策略：请求开销与成功率")
    ap.add_argument("--arms", help="每臂胜率：native=0.4,cffi:chrome=0.7")
    ap.add_argument("--trials", help="从试验行 JSONL 估计胜率（crawl --trial-out 的产物）")
    ap.add_argument("--rates-json",
                    help="按上下文分组的胜率 JSON：{\"contexts\": [{context, pages, rates}]}")
    ap.add_argument("--policy-in",
                    help="上一轮的策略档案（recon-policy/1），按上下文分桶推演")
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

    rows = []
    contexts = {}
    if source in ("rates-json", "policy-in"):
        rows = [simulate.simulate_contexts(
                    name, rates, pages=args.pages, seed=args.seed,
                    min_pulls=args.min_pulls) for name in pump]
        if rows:
            contexts = rows[0]["contexts"]
        body_rates = {}
    else:
        rows = [simulate.simulate(name, rates, pages=args.pages, seed=args.seed,
                                  min_pulls=args.min_pulls) for name in pump]
        rows.append(simulate.best_single(rates, pages=args.pages))
        body_rates = rates

    body = {
        "command": "bandit",
        "ok": True,
        "source": source,
        "rates": body_rates,
        "contexts": contexts,
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
