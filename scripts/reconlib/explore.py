"""explore — 只读探索规划：把后验变成下一批探测请求。

输入是 `crawl --trial-out` 的试验行或 `recon-policy/1` 策略档案，输出是
“哪些上下文、哪条臂、还差几个请求、为什么”。不联网、不发请求、不写证据行。

规划顺序固定：
1. 未见臂：该上下文里一次都没试过的臂各 1 个请求；
2. 补样本：本地 pulls 未到目标值的臂，补到目标值；
3. 探索：已补够样本但 P(best) 仍不足的上下文，优先探测次优臂；
4. 其余上下文判定 exploit，列入 hold，不再花请求。

预算按“权重 /（已分配 + 1）”贪心分配，同权重按页数多、上下文名、臂顺序打破平局；
单上下文最多 `max_per_context` 个请求。auth / missing / noise / 限频行不进入统计。
"""

import math
from urllib.parse import urlsplit

from . import context as ctxmod
from .policy import (ContextualBandit, DECISION_CONFIDENCE,
                     EXPLOIT_MIN_PULLS)
from .simulate import ATTRIBUTABLE_TRIAL

SCHEMA = "recon-explore/1"


def parse_arms(spec):
    """解析逗号分隔的臂名；explore 只收臂名，不收胜率。"""
    names = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            raise ValueError("explore 的 --arms 只列臂名，不给胜率（胜率用 --trials / --policy-in）")
        if part not in names:
            names.append(part)
    return names


def _context_of(row):
    """优先用试验行自带的完整上下文；老行按 url + route 推导为 unknown。"""
    ctx = row.get("context")
    if ctx:
        parts = str(ctx).split("|")
        if len(parts) == 3 and all(parts):
            return ctx
        return None
    url = row.get("url") or row.get("final_url")
    if not url:
        return None
    host = urlsplit(str(url)).netloc or "unknown"
    return f"{host}|{ctxmod.route_class(url)}|unknown"


def _usable(row):
    if not isinstance(row, dict) or row.get("kind") != "trial":
        return False
    if not row.get("arm"):
        return False
    outcome = row.get("trial_outcome") or row.get("outcome")
    if outcome not in ATTRIBUTABLE_TRIAL:
        return False
    if row.get("rate_limited"):
        return False
    return _context_of(row) is not None


def replay(rows, arms=(), prior_strength=4.0, half_life=0.0, cost_weight=0.1,
           min_pulls=1, seed=7):
    """把试验行按原顺序回放进 ContextualBandit，返回 (bandit, pages, observed)。"""
    kept = [r for r in (rows or []) if _usable(r)]
    if not kept:
        raise ValueError("试验行里没有可归因的尝试（ok / degraded / blocked 且非限频）")
    seen = []
    for row in kept:
        if row["arm"] not in seen:
            seen.append(row["arm"])
    order = list(dict.fromkeys(list(arms) + seen))
    bandit = ContextualBandit(
        order, seed=seed, min_pulls=min_pulls, cost_weight=cost_weight,
        prior_strength=prior_strength, half_life=half_life)
    urls = {}
    derived = 0
    successes = 0
    for row in kept:
        ctx = _context_of(row)
        if not row.get("context"):
            derived += 1
        host, route, response = str(ctx).split("|")
        outcome = row.get("trial_outcome") or row.get("outcome")
        bandit.update_from_outcome(
            host, f"{route}|{response}", row["arm"], outcome,
            elapsed_ms=row.get("cost_ms") or row.get("elapsed_ms") or 0)
        if outcome == "ok":
            successes += 1
        url = row.get("url") or row.get("final_url")
        if url:
            urls.setdefault(ctx, set()).add(url)
    pages = {ctx: len(v) for ctx, v in urls.items()}
    observed = {
        "attempts": len(kept),
        "successes": successes,
        "skipped": len(rows or []) - len(kept),
        "context_derived": derived,
        "attempt_success_rate": round(successes / len(kept), 3),
    }
    return bandit, pages, observed


def from_policy(body, extra_arms=()):
    """从策略档案恢复后验；返回 (bandit, pages, observed)。"""
    if not isinstance(body, dict):
        raise ValueError("策略档案必须是对象")
    if body.get("schema") != "recon-policy/1":
        raise ValueError("策略档案 schema 不匹配（需要 recon-policy/1）")
    state = body.get("state")
    if not isinstance(state, dict):
        raise ValueError("策略档案缺少 state 对象")
    stored = [str(a) for a in (body.get("arms") or [])]
    if not stored:
        stored = sorted({str(arm) for row in state.values()
                         for arm in ((row or {}).get("arms") or {})})
    order = list(dict.fromkeys(list(extra_arms) + stored))
    bandit = ContextualBandit(
        order, algorithm=body.get("algorithm", "thompson"),
        seed=7, cost_weight=body.get("cost_weight", 0.1),
        prior_strength=body.get("prior_strength", 4.0),
        half_life=body.get("half_life", 0.0))
    bandit.import_state(state)
    pages = {}
    attempts = successes = 0
    for key, row in state.items():
        arms = (row or {}).get("arms") or {}
        pulls = sum(float((stat or [0])[0]) for stat in arms.values())
        pages[key] = max(1, int(round(pulls)))
        attempts += int(round(pulls))
        successes += int(round(sum(float((stat or [0, 0])[1]) for stat in arms.values())))
    observed = {
        "attempts": attempts,
        "successes": successes,
        "skipped": 0,
        "context_derived": 0,
        "attempt_success_rate": round(successes / attempts, 3) if attempts else 0.0,
    }
    return bandit, pages, observed


def _arm_rank(rows, arm):
    for i, row in enumerate(rows):
        if row["arm"] == arm:
            return i
    return len(rows)


def _context_plan(key, snap, target_pulls, max_per_context):
    """单个上下文的动作：未见 → 补样本 → 探索 → hold。"""
    rows = snap["arms"]
    actions = [{
        "context": key, "arm": r["arm"], "kind": "unseen",
        "want": max(1, min(target_pulls, max_per_context)), "weight": 1.0,
        "why": f"该臂在此上下文还没有本地样本，补到 {target_pulls} 次再比较",
        "p_best": r["p_best"], "confidence": snap["confidence"],
    } for r in rows if r["pulls"] <= 0]

    thin = [r for r in rows if 0 < r["pulls"] < target_pulls]
    if thin:
        arm = min(thin, key=lambda r: (r["pulls"], _arm_rank(rows, r["arm"])))
        deficit = int(math.ceil(target_pulls - arm["pulls"]))
        actions.append({
            "context": key, "arm": arm["arm"], "kind": "warmup",
            "want": max(1, min(deficit, max_per_context)), "weight": 1.0,
            "why": (f"本地只有 {arm['pulls']:g} 次可归因尝试，"
                    f"补到 {target_pulls} 次再判断"),
            "p_best": arm["p_best"], "confidence": snap["confidence"],
        })
        return actions, None

    seen = [r for r in rows if r["pulls"] > 0]
    if snap["decision"] == "explore" and seen:
        others = [r for r in seen if r["arm"] != snap["best_arm"]] or seen
        arm = max(others, key=lambda r: (r["utility"], r["p_best"]))
        actions.append({
            "context": key, "arm": arm["arm"], "kind": "explore",
            "want": max_per_context,
            "weight": max(0.05, 1.0 - snap["confidence"]),
            "why": (f"P(best)={snap['confidence']:.2f} 未到 "
                    f"{DECISION_CONFIDENCE:.1f}，探测次优臂以降低不确定性"),
            "p_best": arm["p_best"], "confidence": snap["confidence"],
        })
        return actions, None

    if actions:
        return actions, None

    return [], {
        "context": key,
        "arm": snap["best_arm"],
        "confidence": snap["confidence"],
        "why": f"P(best)={snap['confidence']:.2f}，已有足够样本，保持当前选臂",
    }


def _allocate(actions, budget, max_per_context):
    """按“权重 /（已分配 + 1）”贪心分配整数请求，尊重单上下文上限。"""
    assigned = [0] * len(actions)
    used = {}
    remaining = budget
    while remaining > 0:
        best = None
        for i, action in enumerate(actions):
            if assigned[i] >= action["want"]:
                continue
            if used.get(action["context"], 0) >= max_per_context:
                continue
            score = action["weight"] / (assigned[i] + 1)
            key = (score, -action["_ctx_order"], -action["_order"])
            if best is None or key > best[0]:
                best = (key, i)
        if best is None:
            break
        i = best[1]
        assigned[i] += 1
        used[actions[i]["context"]] = used.get(actions[i]["context"], 0) + 1
        remaining -= 1
    return assigned


def plan(bandit, pages, observed, budget=30, max_per_context=6,
         target_pulls=EXPLOIT_MIN_PULLS, source="trials", notes=None):
    """把后验转成预算化的探测计划。"""
    if budget < 1:
        raise ValueError("budget 必须 >= 1")
    if max_per_context < 1:
        raise ValueError("max_per_context 必须 >= 1")
    if target_pulls < 1:
        raise ValueError("target_pulls 必须 >= 1")

    state = bandit.export_state()
    contexts = []
    actions = []
    hold = []
    order = {key: i for i, key in enumerate(sorted(state))}
    for key in sorted(state):
        host, route, response = str(key).split("|")
        snap = bandit.snapshot(host, f"{route}|{response}")
        pages_n = int(pages.get(key, 0))
        ctx = {
            "context": key, "host": host, "route": route, "response": response,
            "pages": pages_n, "level": snap["level"],
            "best_arm": snap["best_arm"], "confidence": snap["confidence"],
            "decision": snap["decision"], "arms": snap["arms"],
        }
        contexts.append(ctx)
        found, held = _context_plan(key, snap, target_pulls, max_per_context)
        for action in found:
            action["_ctx_order"] = -pages_n
            action["_order"] = order[key] * 1000 + _arm_rank(snap["arms"],
                                                             action["arm"])
            actions.append(action)
        if held:
            hold.append(held)

    actions.sort(key=lambda a: (a["_ctx_order"], a["context"], a["_order"]))
    assigned = _allocate(actions, budget, max_per_context)
    steps = []
    for action, n in zip(actions, assigned):
        if not n:
            continue
        steps.append({
            "context": action["context"], "arm": action["arm"],
            "kind": action["kind"], "requests": n, "why": action["why"],
            "p_best": action["p_best"], "confidence": action["confidence"],
        })
    allocated = sum(s["requests"] for s in steps)

    all_arms = list(bandit.arms)
    unseen = [a for a in all_arms
              if all(not any(r["arm"] == a and r["pulls"] > 0 for r in c["arms"])
                     for c in contexts)]
    notes = list(notes or [])
    if allocated < budget:
        notes.append("预算未用尽：当前动作已满足目标样本数或单上下文上限")
    uncovered = [a["context"] for i, a in enumerate(actions) if assigned[i] == 0]
    if uncovered:
        notes.append(f"预算不足，{len(uncovered)} 个动作本轮未覆盖；下一轮优先")

    stop = [
        "预算用尽后把新试验行喂回 recon explore；不要在同一批 URL 上无节制重试",
        "auth / missing / noise / 限频行不进入统计，它们不是选臂差异",
        "连续 blocked 达 3 的 host 由 crawl 熔断；explore 不绕过节流与 robots",
        "新臂只在有权限、robots 允许的路由上试，且一次只改一个变量",
    ]
    if all(c["decision"] == "exploit" for c in contexts) and not actions:
        stop.insert(0, "全部上下文已判 exploit：停止加探测，改用 canary 做防回归")

    summary = {
        "contexts": len(contexts),
        "explore_contexts": sum(1 for c in contexts if c["decision"] != "exploit"),
        "exploit_contexts": sum(1 for c in contexts if c["decision"] == "exploit"),
        "steps": len(steps),
        "unseen_arms": unseen,
    }
    return {
        "schema": SCHEMA,
        "source": source,
        "budget": budget,
        "allocated": allocated,
        "remaining": budget - allocated,
        "arms": all_arms,
        "contexts": contexts,
        "unseen_arms": unseen,
        "steps": steps,
        "hold": hold,
        "stop": stop,
        "notes": notes,
        "observed": observed,
        "summary": summary,
    }
