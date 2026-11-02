#!/usr/bin/env python3
"""explore.py — 只读探索规划：从试验行或策略档案算出下一批探测请求。

不联网、不发请求、不写证据行。输入二选一：
    --trials recon/<代号>/trials.jsonl     crawl --trial-out 的产物
    --policy-in recon/<代号>/policy.json   上一轮的 recon-policy/1 档案

输出按上下文分组的探索计划：未见臂、补样本、探索次优臂，以及已经可以
exploit 的上下文（不再花请求）。每条动作带请求数与理由，并给出停止规则。

用法:
    scripts/explore.py --trials recon/demo/trials.jsonl --budget 30 --json
    scripts/explore.py --policy-in recon/demo/policy.json --arms native,cffi:chrome
    scripts/explore.py --trials trials.jsonl --plan-out recon/demo/plan.json
    scripts/crawl.py --from urls.txt --plan recon/demo/plan.json

退出码: 0 成功；2 用法或输入错误
"""

import argparse
import json
import os
import sys

from reconlib import evidence, explore, policy_store


def print_text(body):
    obs = body["observed"]
    print(f"来源 {body['source']}，臂 {', '.join(body['arms'])}")
    print(f"观测: 尝试 {obs['attempts']} 次，成功 {obs['successes']} 次"
          f"（单次成功率 {obs['attempt_success_rate']}），剔除 {obs['skipped']} 行")
    if obs["context_derived"]:
        print(f"注意: {obs['context_derived']} 行缺 context，已按 url + route 推导为 unknown")
    print(f"预算 {body['budget']}，已分配 {body['allocated']}，剩余 {body['remaining']}")
    suspended = body["summary"].get("suspended_contexts") or 0
    if suspended:
        print(f"暂停上下文: {suspended}（所有候选臂连续 blocked，本轮不再分配）")
    if body["unseen_arms"]:
        print(f"全局未试臂: {', '.join(body['unseen_arms'])}")
    print()
    print(f"{'context':<40} {'pages':>5} {'best':<16} {'P(best)':>7} {'decision':<8} level")
    for ctx in body["contexts"]:
        print(f"{ctx['context']:<40} {ctx['pages']:>5} {str(ctx['best_arm']):<16} "
              f"{ctx['confidence']:>7.3f} {ctx['decision']:<8} {ctx['level']}")
    if body["steps"]:
        print()
        print("探测计划:")
        for step in body["steps"]:
            print(f"  {step['context']}  {step['arm']}  x{step['requests']}  "
                  f"[{step['kind']}] {step['why']}")
    if body["hold"]:
        print()
        print("保持:")
        for item in body["hold"]:
            print(f"  {item['context']}  用 {item['arm']}：{item['why']}")
    if body["notes"]:
        print()
        for note in body["notes"]:
            print(f"# {note}")
    print()
    print("停止条件:")
    for stop in body["stop"]:
        print(f"  - {stop}")


def main():
    ap = argparse.ArgumentParser(description="只读探索规划：后验 → 下一批探测（不联网）")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--trials", help="试验行 JSONL（crawl --trial-out）")
    group.add_argument("--policy-in", help="策略档案（recon-policy/1）")
    ap.add_argument("--arms", default="",
                    help="逗号分隔的臂名；可加入档案里没有的新臂，触发未见臂探测")
    ap.add_argument("--budget", type=int, default=30, help="本轮请求预算，默认 30")
    ap.add_argument("--max-per-context", type=int, default=6,
                    help="单上下文请求上限，默认 6")
    ap.add_argument("--target-pulls", type=int, default=4,
                    help="每臂本地样本目标，默认 4（与 exploit 门槛一致）")
    ap.add_argument("--min-pulls", type=int, default=1,
                    help="回退聚合时每臂的最小样本，默认 1")
    ap.add_argument("--prior-strength", type=float, default=4.0,
                    help="分层先验伪计数，默认 4；0 关闭")
    ap.add_argument("--half-life", type=float, default=0.0,
                    help="按更新次数指数遗忘的半衰期，默认 0（不遗忘）")
    ap.add_argument("--cost-weight", type=float, default=0.1,
                    help="成本惩罚权重（5000ms 归一化），默认 0.1；0 关闭")
    ap.add_argument("--block-budget", type=int, default=3,
                    help="同一路由连续 blocked 几次暂停该臂，默认 3；0 关闭")
    ap.add_argument("--seed", type=int, default=7, help="诊断采样种子，默认 7")
    ap.add_argument("--plan-out",
                    help="把计划写成 JSON，供 crawl --plan 直接按配额执行")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    if args.budget < 1:
        ap.error("--budget 必须 >= 1")
    if args.max_per_context < 1:
        ap.error("--max-per-context 必须 >= 1")
    if args.target_pulls < 1:
        ap.error("--target-pulls 必须 >= 1")
    if args.min_pulls < 0:
        ap.error("--min-pulls 不能为负")
    if args.prior_strength < 0:
        ap.error("--prior-strength 不能为负")
    if args.half_life < 0:
        ap.error("--half-life 不能为负")
    if args.cost_weight < 0:
        ap.error("--cost-weight 不能为负")
    if args.block_budget < 0:
        ap.error("--block-budget 不能为负")

    try:
        extra = explore.parse_arms(args.arms)
    except ValueError as e:
        ap.error(str(e))

    notes = []
    try:
        if args.trials:
            try:
                rows = evidence.read_jsonl(args.trials)
            except (OSError, ValueError) as e:
                ap.error(f"读试验行失败: {e}")
            bandit, pages, observed = explore.replay(
                rows, arms=extra, prior_strength=args.prior_strength,
                half_life=args.half_life, cost_weight=args.cost_weight,
                min_pulls=args.min_pulls, seed=args.seed,
                block_budget=args.block_budget)
            source = "trials"
        else:
            try:
                body = policy_store.load(args.policy_in)
            except ValueError as e:
                ap.error(str(e))
            bandit, pages, observed = explore.from_policy(
                body, extra_arms=extra, block_budget=args.block_budget)
            source = "policy"
            notes.append("策略档案里的 prior_strength / half_life / cost_weight "
                         "以档案为准；本轮命令行只覆盖额外臂")
            notes.append("策略档案不记录暂停状态；连续 blocked 的臂要到 "
                         "--trials 回放时才会计入安全探索")
        result = explore.plan(
            bandit, pages, observed, budget=args.budget,
            max_per_context=args.max_per_context,
            target_pulls=args.target_pulls, source=source, notes=notes)
    except ValueError as e:
        ap.error(str(e))

    result.update({"command": "explore", "ok": True})
    if args.plan_out:
        try:
            with open(args.plan_out, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
        except OSError as e:
            ap.error(f"写计划失败: {e}")
        result["plan_out"] = os.path.abspath(args.plan_out)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_text(result)
        if args.plan_out:
            print(f"计划 -> {result['plan_out']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
