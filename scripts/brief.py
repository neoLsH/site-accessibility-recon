#!/usr/bin/env python3
"""brief.py — 把 recon 工作区收敛成有界、可复用的 Agent 上下文包。

只读：不联网、不发请求、不写盘。复用 stats / advise 的原子，不重写计数。

用法:
    scripts/brief.py recon/demo
    scripts/brief.py recon/demo --json --budget 1200
    scripts/brief.py recon/demo --json --expect-digest sha256:…

退出码: 0 正常（含截断与 digest 命中）；2 输入错误
"""

import argparse
import json
import os
import sys

from reconlib import advice, brief, evidence
import stats
from report import load_profile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find(workspace, name):
    path = os.path.join(workspace, name)
    return path if os.path.isfile(path) else None


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def doc_chars(name):
    path = os.path.join(ROOT, "references", name)
    try:
        with open(path, encoding="utf-8") as f:
            return len(f.read())
    except OSError:
        return 0


def main():
    ap = argparse.ArgumentParser(description="工作区 → 有界、可复用的 Agent 上下文包（只读）")
    ap.add_argument("workspace", help="recon/<代号>/ 目录")
    ap.add_argument("--budget", type=int, default=brief.DEFAULT_BUDGET,
                    help=f"JSON 输出字符上限（默认 {brief.DEFAULT_BUDGET}，最小 {brief.MIN_BUDGET}）")
    ap.add_argument("--expect-digest", help="上轮简报的 digest；命中时只回 digest")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    if args.budget < brief.MIN_BUDGET:
        ap.error(f"--budget 不能小于 {brief.MIN_BUDGET}")
    if not os.path.isdir(args.workspace):
        ap.error(f"工作区不存在: {args.workspace}")

    paths = {name: find(args.workspace, f"{name}.{ext}")
             for name, ext in (("profile", "yaml"), ("evidence", "jsonl"),
                               ("trials", "jsonl"), ("policy", "json"),
                               ("agent", "json"))}

    profile = None
    if paths["profile"]:
        try:
            profile = load_profile(paths["profile"])
        except SystemExit:
            ap.error("profile.yaml 解析失败")
        if not isinstance(profile, dict):
            ap.error("profile.yaml 顶层不是对象")

    rows, stats_rows = [], []
    if paths["evidence"]:
        try:
            rows = evidence.read_jsonl(paths["evidence"])
            stats_rows = stats.read_rows([paths["evidence"]])
        except (OSError, ValueError) as e:
            ap.error(str(e))
    rows.sort(key=brief.order_key)

    try:
        advice_body = advice.recommend(rows)
        summary = stats.summarize(stats_rows)
    except ValueError as e:
        ap.error(str(e))

    extras = {"artifacts": {key: os.path.basename(path)
                            for key, path in paths.items() if path}}
    if paths["trials"]:
        try:
            extras["trials"] = {"n": len(evidence.read_jsonl(paths["trials"]))}
        except (OSError, ValueError) as e:
            ap.error(str(e))
    if paths["policy"]:
        try:
            body = load_json(paths["policy"])
        except (OSError, ValueError) as e:
            ap.error(f"policy.json 解析失败: {e}")
        contexts = (body.get("meta") or {}).get("contexts") if isinstance(body, dict) else None
        extras["policy"] = {
            "contexts": contexts if isinstance(contexts, int) else len(body.get("state") or {}),
            "arms": len(body.get("arms") or []),
        }
    if paths["agent"]:
        try:
            body = load_json(paths["agent"])
        except (OSError, ValueError) as e:
            ap.error(f"agent.json 解析失败: {e}")
        if isinstance(body, dict):
            stop = body.get("stop")
            extras["agent"] = {
                "stop": stop.get("kind") if isinstance(stop, dict) else stop,
                "rounds": len(body.get("rounds") or []),
            }

    body = brief.compose(advice_body, summary, profile, extras, doc_chars)

    if args.expect_digest:
        digest = brief.digest_of(body)
        if digest == args.expect_digest:
            hit = {"schema": brief.SCHEMA, "command": "brief", "ok": True,
                   "digest": digest, "cache": {"hit": True}}
            print(json.dumps(hit, ensure_ascii=False) if args.json else brief.render_text(hit))
            return 0

    out = brief.finalize(body, args.budget)
    if args.expect_digest:
        out["cache"] = {"hit": False}
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(brief.render_text(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
