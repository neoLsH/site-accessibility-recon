"""brief — 把工作区收敛成有界、可复用的 Agent 上下文包。

只编排既有原子层：advice.recommend / stats.summarize 的结果由调用方传入，
本模块不做 IO、不发请求、不重写统计与建议逻辑。
"""

import hashlib
import json
import re

SCHEMA = "brief/1"
LOGIC_VERSION = "brief/1:r1"
DEFAULT_BUDGET = 2000
MIN_BUDGET = 200

DOC_READS = {
    "collect-more": [("evidence-matrix.md", "补样本的实验设计与非确定性")],
    "separate-noise": [("triage-and-gates.md", "链路噪声与拦截的区分判据")],
    "check-authorization": [("layers.md", "L5 凭证与签名的判据")],
    "check-routing": [("triage-and-gates.md", "路由不存在与业务空态的判据")],
    "check-extraction": [("triage-and-gates.md", "200 但内容不足的兜底判据"),
                          ("metrics.md", "内容完整率的口径")],
    "backoff": [("strategy.md", "限频冷却与节流参数")],
    "switch-arm": [("strategy.md", "选臂的上下文与奖励"),
                   ("arms.md", "臂注册表与能力过滤")],
    "differentiate": [("detection-surface.md", "客户端采集与服务端评分的判定实验")],
    "maintain": [("review-scorecard.md", "交付前自查评分卡")],
}

CJK = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]")

VOLATILE = ("ts", "timestamp", "time", "fetched_at", "elapsed_ms", "cost_ms",
            "run_id", "started_at", "finished_at")


def estimate_tokens(text):
    """粗略 token 估算：CJK 按 1 token/字，其余按 3.5 字符/token。只是量级参考。"""
    cjk = len(CJK.findall(text))
    return int(round(cjk + (len(text) - cjk) / 3.5))


def order_key(row):
    """排序键：剔除易变字段，让文件行序与耗时不影响输出。"""
    stable = {key: value for key, value in row.items() if key not in VOLATILE}
    return json.dumps(stable, sort_keys=True, ensure_ascii=False)


def gates_of(profile):
    gates = (profile or {}).get("gates") or []
    counts = {"pass": 0, "fail": 0, "unknown": 0}
    first = None
    for gate in gates:
        if not isinstance(gate, dict):
            counts["unknown"] += 1
            continue
        status = str(gate.get("status") or "").strip().lower()
        if status not in ("pass", "fail"):
            counts["unknown"] += 1
            continue
        counts[status] += 1
        if status == "fail" and first is None:
            first = gate
    first_fail = None
    if first is not None:
        first_fail = {"name": first.get("name") or "未命名",
                      "layer": first.get("layer") or "unknown"}
    return {"first_fail": first_fail, "pass": counts["pass"],
            "fail": counts["fail"], "unknown": counts["unknown"]}


def gaps_of(profile, overall):
    gaps = []
    if profile is None:
        gaps.append("缺 profile.yaml：没有闸门图与口径")
        return gaps
    metrics = profile.get("metrics") or {}
    if not metrics.get("baseline"):
        gaps.append("metrics.baseline 未测")
    for gate in profile.get("gates") or []:
        if not isinstance(gate, dict):
            continue
        if (str(gate.get("status") or "").lower() == "fail"
                and not str(gate.get("evidence") or "").strip()):
            gaps.append(f"「{gate.get('name') or '未命名'}」门无证据")
            break
    plan = profile.get("plan") or {}
    if isinstance(plan, dict) and not plan.get("level"):
        gaps.append("plan.level 未推导")
    if not (overall or {}).get("n"):
        gaps.append("没有证据行：任何结论都是假设")
    return gaps[:3]
