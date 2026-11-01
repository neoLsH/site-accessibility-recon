#!/usr/bin/env python3
"""advise.py — 从证据行推出下一步动作，输出稳定的 JSON 契约给 Agent / LLM。

只读证据行，不联网、不发请求、不自动执行建议命令。建议绑定计数与路由，
并附停止条件；没有证据就建议补样本。

用法:
    scripts/advise.py recon/demo/evidence.jsonl
    scripts/advise.py recon/demo/evidence.jsonl --route /article/1 --json

退出码: 0 有建议；2 输入错误（含把试验行当证据行）
"""

import argparse
import json
import sys

from reconlib import advice, evidence


def print_text(body):
    ev = body["evidence"]
    print(f"证据行 {ev['n']} 条，路由 {ev['routes']} 个，噪声 {ev['outcomes']['noise']} 条"
          f"（可归因 {ev['noise_excluded_n']}）")
    print("结局: " + " ".join(f"{k}={v}" for k, v in ev["outcomes"].items() if v))
    if ev["arms"]:
        print(f"臂: {', '.join(ev['arms'])}")
    print()
    for finding in body["findings"]:
        print(f"  - [{finding['code']}] {finding['message']}（{finding['count']}）")
    nxt = body["next"]
    print()
    print(f"下一步: {nxt['action']}（{nxt['layer']} / {nxt['countermeasure']}，"
          f"置信度 {nxt['confidence']}）")
    print(f"为什么: {nxt['why']}")
    if nxt["commands"]:
        print("命令:")
        for cmd in nxt["commands"]:
            print(f"  {cmd}")
    print("停止条件:")
    for stop in nxt["stop"]:
        print(f"  - {stop}")


def main():
    ap = argparse.ArgumentParser(description="从证据行推出下一步动作（只读，不执行）")
    ap.add_argument("evidence", help="证据行 JSONL（stats 能吃的那些行）")
    ap.add_argument("--route", help="只看某个路由（按 route 字段精确匹配）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    try:
        rows = evidence.read_jsonl(args.evidence)
    except (OSError, ValueError) as e:
        ap.error(f"读证据行失败: {e}")

    if args.route:
        rows = [row for row in rows if (row.get("route") or "/") == args.route]
        if not rows:
            ap.error(f"没有路由为 {args.route} 的证据行")

    try:
        body = advice.recommend(rows, evidence_path=args.evidence)
    except ValueError as e:
        ap.error(str(e))

    body.update({"command": "advise", "ok": True, "schema": "advise/1"})
    if args.route:
        body["route"] = args.route
    if args.json:
        print(json.dumps(body, ensure_ascii=False, indent=2))
    else:
        print_text(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
