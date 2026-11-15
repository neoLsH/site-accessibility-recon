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


def _worst_first(group):
    access = group["access"]["rate"]
    complete = group["complete"]["rate"]
    return (-1 if access is None else access,
            -1 if complete is None else complete,
            group["route"])


def compose(advice_body, summary, profile, extras, doc_chars):
    """把原子层结果拼成简报信息体；不含 digest / budget / truncated。"""
    raw_evidence = advice_body["evidence"]
    evidence = {
        "n": raw_evidence["n"],
        "noise_excluded_n": raw_evidence["noise_excluded_n"],
        "routes": raw_evidence["routes"],
        "outcomes": dict(raw_evidence["outcomes"]),
        "arms": sorted(raw_evidence.get("arms") or []),
        "contexts": dict(sorted((raw_evidence.get("contexts") or {}).items())),
    }
    for key in ("trials", "policy", "agent"):
        if extras.get(key) is not None:
            evidence[key] = extras[key]

    nxt = advice_body["next"]
    verdict = {
        "action": nxt["action"],
        "layer": nxt["layer"],
        "countermeasure": nxt["countermeasure"],
        "confidence": nxt["confidence"],
        "why": nxt["why"],
        "commands": list(nxt.get("commands") or []),
        "stop": list(nxt.get("stop") or []),
    }

    overall = summary["overall"]
    metrics = {
        "overall": {
            "n": overall["n"],
            "access_rate": overall["access"]["rate"],
            "access_ci": overall["access"]["ci95"],
            "complete_rate": overall["complete"]["rate"],
            "complete_ci": overall["complete"]["ci95"],
        },
        "routes": [
            {"route": group["route"], "n": group["n"],
             "access_rate": group["access"]["rate"],
             "complete_rate": group["complete"]["rate"]}
            for group in sorted(summary["groups"], key=_worst_first)
        ],
    }

    reads = []
    for name, why in DOC_READS.get(verdict["action"], []):
        chars = int(doc_chars(name) or 0)
        if chars:
            reads.append({"path": f"references/{name}", "chars": chars, "why": why})
    return {
        "verdict": verdict,
        "evidence": evidence,
        "metrics": metrics,
        "gates": gates_of(profile),
        "gaps": gaps_of(profile, overall),
        "next_reads": reads[:2],
        "artifacts": dict(extras.get("artifacts") or {}),
    }


def json_chars(obj):
    """预算口径：JSON 缩进序列化的字符数（与 CLI 实际输出一致）。"""
    return len(json.dumps(obj, ensure_ascii=False, indent=2))


def digest_of(body):
    """对未裁剪信息体取指纹；不含 budget / cache / digest 自身。"""
    payload = json.dumps(body, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"))
    raw = (LOGIC_VERSION + "\n" + payload).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def finalize(body, budget):
    """返回 brief/1 对象：digest + 裁剪后的信息体 + budget + truncated。"""
    digest = digest_of(body)
    view, dropped, truncated = trim(body, budget)
    used = json_chars(view)
    out = {"schema": SCHEMA, "command": "brief", "ok": True, "digest": digest}
    out.update(view)
    out["budget"] = {"limit": budget, "used": used, "dropped": dropped,
                     "tokens_est": estimate_tokens(json.dumps(view, ensure_ascii=False, indent=2))}
    out["truncated"] = truncated
    return out


def trim(body, budget):
    """按固定顺序裁剪；核心字段永不裁剪。"""
    view = json.loads(json.dumps(body, ensure_ascii=False))
    dropped = []
    if json_chars(view) <= budget:
        return view, dropped, False

    def cut_routes():
        routes = view["metrics"]["routes"]
        if len(routes) <= 6:
            return False
        cut = len(routes) - 6
        view["metrics"]["routes"] = routes[:3] + routes[-3:]
        dropped.append(f"metrics.routes 省略 {cut} 条")
        return True

    def cut_outcomes():
        outcomes = view["evidence"]["outcomes"]
        zeros = [key for key, value in outcomes.items() if not value]
        if not zeros:
            return False
        for key in zeros:
            outcomes.pop(key)
        dropped.append("evidence.outcomes 省略零值")
        return True

    def cut_verdict():
        changed = False
        for key in ("commands", "stop"):
            if len(view["verdict"][key]) > 1:
                view["verdict"][key] = view["verdict"][key][:1]
                changed = True
        if changed:
            dropped.append("verdict.commands/stop 各留 1 条")
        return changed

    def cut_gaps():
        if len(view["gaps"]) <= 1:
            return False
        view["gaps"] = view["gaps"][:1]
        dropped.append("gaps 留 1 条")
        return True

    def cut_extras():
        changed = False
        for key, keep in (("trials", "n"), ("policy", "contexts"), ("agent", "stop")):
            item = view["evidence"].get(key)
            if not isinstance(item, dict) or len(item) <= 1:
                continue
            view["evidence"][key] = {keep: item.get(keep)}
            changed = True
        if changed:
            dropped.append("evidence.trials/policy/agent 只留关键计数")
        return changed

    def cut_reads():
        if len(view["next_reads"]) <= 1:
            return False
        view["next_reads"] = view["next_reads"][:1]
        dropped.append("next_reads 留 1 条")
        return True

    for step in (cut_routes, cut_outcomes, cut_verdict, cut_gaps, cut_extras, cut_reads):
        step()
        if json_chars(view) <= budget:
            break
    return view, dropped, json_chars(view) > budget

