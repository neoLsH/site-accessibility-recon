"""simulate — 离线推演选臂策略，不联网、不发请求。

输入是每臂的“单次尝试成功率”，输出是在给定页数下策略的请求开销与成功率。
胜率必须来自实测证据（例如 crawl 的试验行），推演只回答“如果这个胜率成立，
换一种选举策略值不值”，不替代真实对照。

边界：只重排已有客户端臂，不新增能力；不模拟验证码求解、出口轮换或凭证。
"""

import random

from .policy import Bandit

ATTRIBUTABLE_TRIAL = ("ok", "degraded", "blocked")


def parse_rates(spec):
    """把 `native=0.4,cffi:chrome=0.7` 解析成 {arm: rate}。"""
    rates = {}
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"臂胜率要写成 arm=rate: {part!r}")
        arm, raw = part.split("=", 1)
        arm = arm.strip()
        if not arm:
            raise ValueError("臂名为空")
        try:
            rate = float(raw)
        except ValueError:
            raise ValueError(f"胜率不是数字: {raw.strip()!r}")
        if not 0.0 <= rate <= 1.0:
            raise ValueError(f"胜率应在 0–1: {arm}={rate}")
        rates[arm] = rate
    if not rates:
        raise ValueError("至少需要一个臂胜率")
    return rates


def rates_from_trials(rows):
    """从试验行估计每臂成功率：只算可归因的 ok / degraded / blocked，限频行剔除。"""
    tally = {}
    for row in rows or []:
        if not isinstance(row, dict) or row.get("kind") != "trial":
            continue
        arm = row.get("arm")
        outcome = row.get("trial_outcome") or row.get("outcome")
        if not arm or outcome not in ATTRIBUTABLE_TRIAL:
            continue
        if row.get("rate_limited"):
            continue
        n, wins = tally.get(arm, (0, 0))
        tally[arm] = (n + 1, wins + (1 if outcome == "ok" else 0))
    return {arm: wins / n for arm, (n, wins) in tally.items() if n}


def simulate(policy, rates, pages=200, seed=None, min_pulls=1):
    """按胜率采样，模拟 `pages` 页在指定策略下的请求数与成功数。"""
    arms = list(rates)
    if not arms:
        raise ValueError("至少需要一个臂")
    if pages < 1:
        raise ValueError("pages 必须 >= 1")
    bandit = Bandit(arms, algorithm=policy, seed=seed, min_pulls=min_pulls)
    rng = random.Random(seed)
    pulls = {arm: 0 for arm in arms}
    wins = {arm: 0 for arm in arms}
    requests = successes = 0

    for _ in range(pages):
        candidates = list(arms)
        while candidates:
            arm = bandit.select("sim", candidates)
            requests += 1
            pulls[arm] += 1
            success = rng.random() < rates[arm]
            bandit.update("sim", arm, success)
            if success:
                wins[arm] += 1
                successes += 1
                break
            candidates.remove(arm)

    return {
        "policy": policy,
        "pages": pages,
        "requests": requests,
        "successes": successes,
        "access_rate": successes / pages,
        "requests_per_success": (requests / successes) if successes else None,
        "pulls": pulls,
        "wins": wins,
    }


def best_single(rates, pages=200):
    """基线：始终只用一个最强的臂、每页一次请求。"""
    if not rates:
        raise ValueError("至少需要一个臂")
    arm = max(rates, key=rates.get)
    return {
        "policy": "best-single",
        "pages": pages,
        "requests": pages,
        "successes": None,  # 期望值，不是采样值
        "expected_access_rate": rates[arm],
        "arm": arm,
    }
