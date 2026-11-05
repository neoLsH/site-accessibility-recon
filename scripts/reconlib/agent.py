"""agent — 有界探索循环的决策层：什么时候继续、什么时候停、frontier 怎么进计划。

只做规则判断，不发请求、不写文件。CLI（scripts/agent.py）负责按这里的结论
调用 crawl / explore，并执行 robots、节流、熔断与配额。

三个判断：
    decide        下一轮是否还跑，为什么不跑；
    safety_stop   本轮 crawl 是否触发了必须停手的安全信号；
    augment       把路径发现得到的新路由类补进执行计划，避免它们 plan-exhausted。
"""

from urllib.parse import urlsplit

from . import context as ctxmod

TERMINAL = {
    "maintain": ("goal", "可归因样本已全部拿到内容；转防回归，不再加探测"),
    "check-authorization": ("business", "多数失败是鉴权要求；换栈无解，停在合规边界"),
    "check-routing": ("business", "多数失败是路由或内容不存在；继续探测没有收益"),
    "check-extraction": ("business", "内容区不足更像抽取或选择器问题；先修口径"),
    "separate-noise": ("noise", "可归因样本为 0，全是链路或 5xx 噪声；先修链路"),
    "backoff": ("rate-limit", "被拦样本过半是限频信号；继续请求只会加深限频"),
}

CONTINUE = ("collect-more", "switch-arm", "differentiate")

SAFETY_PREFIXES = ("rate-limit-cooldown", "circuit-open")


def safety_stop(summary):
    """crawl 摘要 → 停止原因；没有安全信号时返回 None。"""
    stopped = str((summary or {}).get("stopped") or "")
    if stopped.startswith(SAFETY_PREFIXES):
        return {"kind": "safety", "reason": stopped}
    if stopped == "没有可用的客户端臂":
        return {"kind": "config", "reason": stopped}
    if (summary or {}).get("suspended_pages"):
        return {"kind": "safety",
                "reason": "all-arms-suspended：该路由所有候选臂连续 blocked"}
    return None


def decide(advice_body, requests_left, rounds_left, plan_steps, have_urls,
           frontier=0):
    """下一轮是否继续；返回 {run, kind, reason}。"""
    if rounds_left <= 0:
        return {"run": False, "kind": "max-rounds", "reason": "达到轮数上限"}
    if requests_left <= 0:
        return {"run": False, "kind": "budget", "reason": "请求预算已用尽"}
    if not have_urls:
        return {"run": False, "kind": "frontier-empty",
                "reason": "本轮没有可继续的 URL（无待重试页，也没有新发现）"}
    action = str(((advice_body or {}).get("next") or {}).get("action") or "")
    if action == "maintain" and frontier > 0:
        return {"run": True, "kind": "continue",
                "reason": (f"已拿到内容，但还有 {frontier} 条路径发现链接未采样；"
                           "先补路径样本，再转防回归")}
    if action in TERMINAL:
        kind, reason = TERMINAL[action]
        return {"run": False, "kind": kind,
                "reason": f"建议 {action}：{reason}"}
    if plan_steps <= 0:
        return {"run": False, "kind": "plan-empty",
                "reason": "探索计划没有剩余动作（已判 exploit 或候选臂全部暂停）"}
    if action in CONTINUE:
        return {"run": True, "kind": "continue",
                "reason": f"建议 {action}：按计划继续，共 {plan_steps} 步"}
    return {"run": True, "kind": "continue", "reason": "按探索计划继续"}


def _steps(body):
    steps = (body or {}).get("steps")
    return steps if isinstance(steps, list) else []


def augment(plan, urls, arms, per_url=1, max_steps=64, max_requests=None):
    """给计划补上 frontier 里尚未覆盖的 (host, 路由类)。

    已有路由不动：它们的额度由 explore 决定。新路由按“该路由的不同 URL 数 ×
    每条臂 per_url 次”给额度；响应类写 unknown（请求前不可知）。
    返回新对象，不修改入参。
    """
    if not isinstance(plan, dict):
        raise ValueError("计划必须是 JSON 对象")
    arms = [str(a) for a in (arms or []) if str(a)]
    per_url = max(1, int(per_url or 1))
    steps = [dict(step) for step in _steps(plan) if isinstance(step, dict)]
    covered = set()
    for step in steps:
        text = str(step.get("context") or "")
        if text.count("|") >= 2:
            host, route, _ = text.split("|", 2)
            if host and route:
                covered.add((host, route))

    counts, order = {}, []
    for url in urls or []:
        host = urlsplit(str(url or "")).netloc
        route = ctxmod.route_class(url)
        key = (host, route)
        if not host or key in covered:
            continue
        if key not in counts:
            counts[key] = set()
            order.append(key)
        counts[key].add(str(url))

    added, total = [], 0
    for key in order:
        want = len(counts[key]) * per_url
        for arm in arms:
            if len(added) >= max_steps:
                break
            if max_requests is not None and total + want > max_requests:
                break
            added.append({
                "context": f"{key[0]}|{key[1]}|unknown",
                "arm": arm,
                "kind": "frontier",
                "requests": want,
                "why": "路径发现得到的新路由类：每个 URL 先给每条臂一次样本再比较",
                "p_best": 0.0,
                "confidence": 0.0,
            })
            total += want
        if len(added) >= max_steps:
            break

    body = dict(plan)
    body["steps"] = steps + added
    body["frontier"] = {
        "routes": len(counts),
        "steps": len(added),
        "requests": total,
    }
    return body
