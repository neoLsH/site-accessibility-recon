"""policy — 多臂老虎机：在合规的客户端臂之间做选举。

臂就是已有的客户端协议栈（native / cffi:<profile>），不是“绕过手段”。奖励只有两种：
- ok 记成功
- blocked / degraded 记失败

noise / auth / missing / rate_limited 不更新：这些不是客户端栈的差异，更新只会把噪声学成策略。
同一个 Bandit 按 host 分隔状态；算法与随机种子可固定，便于复现与离线推演。

ContextualBandit 在此之上按 (host, 路由类, 响应类) 细分：degraded 记半个奖励，
成本高的臂按 5000ms 归一化扣分；样本不足时沿 host|route|response → host|route|* →
host|*|* → * 回退聚合。

分层先验把“别的路由 / 别的响应类”的经验当作伪计数，样本少的格不再从均匀先验
重新摸索；half_life 按更新次数指数遗忘，风控变化后旧结论会自然退场。快照额外给出
P(best)、置信区间与 exploit / explore 判定，供上游决定“继续用”还是“补样本”。
"""

import hashlib
import math
import random

from . import context as ctxmod

ALGORITHMS = ("thompson", "ucb1", "fixed", "round_robin")
NO_UPDATE = {"noise", "auth", "missing"}
REWARD = {"ok": 1.0, "degraded": 0.5, "blocked": 0.0}
DECISION_CONFIDENCE = 0.8
EXPLOIT_MIN_PULLS = 4
DIAG_SAMPLES = 400
STAT_LEN = 5  # pulls, successes, failures, cost_sum, cost_n



class Bandit:
    def __init__(self, arms, algorithm="thompson", seed=None, min_pulls=1, ucb_c=1.414):
        self.arms = tuple(dict.fromkeys(arms))
        if not self.arms:
            raise ValueError("至少需要一个客户端臂")
        if algorithm not in ALGORITHMS:
            raise ValueError(f"未知策略 {algorithm!r}，应为 {'/'.join(ALGORITHMS)}")
        self.algorithm = algorithm
        self.min_pulls = max(0, int(min_pulls))
        self.ucb_c = float(ucb_c)
        self._rng = random.Random(seed)
        self._state = {}

    def _host(self, host):
        return self._state.setdefault(host, {
            "arms": {a: [0, 0, 0] for a in self.arms},  # pulls, successes, failures
            "rr": 0,
        })

    def select(self, host, candidates=None):
        state = self._host(host)
        arms = [a for a in (candidates or self.arms) if a in self.arms] or list(self.arms)
        stats = state["arms"]
        if self.algorithm == "fixed":
            return arms[0]
        if self.algorithm == "round_robin":
            arm = arms[state["rr"] % len(arms)]
            state["rr"] = (state["rr"] + 1) % len(arms)
            return arm
        warm = [a for a in arms if stats[a][0] < self.min_pulls]
        if warm:
            return min(warm, key=lambda a: (stats[a][0], arms.index(a)))
        if self.algorithm == "ucb1":
            total = sum(stats[a][0] for a in arms)
            return max(arms, key=lambda a: self._ucb(stats[a], total))
        return max(arms, key=lambda a: self._thompson(stats[a]))

    def _thompson(self, stat):
        _, successes, failures = stat
        return self._rng.betavariate(1 + successes, 1 + failures)

    def _ucb(self, stat, total):
        pulls, successes, _ = stat
        if pulls == 0:
            return float("inf")
        return successes / pulls + self.ucb_c * math.sqrt(math.log(max(1, total)) / pulls)

    def update(self, host, arm, success):
        if arm not in self.arms:
            raise ValueError(f"未知客户端臂 {arm!r}")
        stat = self._host(host)["arms"][arm]
        stat[0] += 1
        stat[1 if success else 2] += 1

    def update_from_outcome(self, host, arm, outcome, rate_limited=False):
        """按证据行 outcome 更新；返回是否真的更新了后验。"""
        if rate_limited or outcome in NO_UPDATE or outcome not in ("ok", "degraded", "blocked"):
            return False
        self.update(host, arm, outcome == "ok")
        return True

    def snapshot(self, host):
        stats = self._host(host)["arms"]
        rows = [{
            "arm": arm,
            "pulls": stat[0],
            "successes": stat[1],
            "failures": stat[2],
            "mean": round(stat[1] / stat[0], 3) if stat[0] else None,
        } for arm, stat in stats.items()]
        pulled = [r for r in rows if r["pulls"]]
        best = max(pulled, key=lambda r: (r["mean"], r["pulls"]))["arm"] if pulled else None
        return {"host": host, "algorithm": self.algorithm, "best_arm": best, "arms": rows}
class ContextualBandit:
    """按 (host, route, response) 维护后验；样本不足时沿回退链聚合。

    prior_strength 把兄弟上下文（别的响应类 / 别的路由 / 别的 host）的经验
    当作伪计数；half_life 以更新次数为单位指数遗忘，0 表示不遗忘。
    """

    def __init__(self, arms, algorithm="thompson", seed=None, min_pulls=1,
                 ucb_c=1.414, cost_weight=0.1, cost_cap_ms=5000,
                 prior_strength=4.0, half_life=0.0):
        self.arms = tuple(dict.fromkeys(arms))
        if not self.arms:
            raise ValueError("至少需要一个客户端臂")
        if algorithm not in ALGORITHMS:
            raise ValueError(f"未知策略 {algorithm!r}")
        self.algorithm = algorithm
        self.min_pulls = max(0, int(min_pulls))
        self.ucb_c = float(ucb_c)
        self.cost_weight = max(0.0, float(cost_weight))
        self.cost_cap_ms = max(1.0, float(cost_cap_ms))
        self.prior_strength = max(0.0, float(prior_strength))
        self.half_life = max(0.0, float(half_life))
        self._rng = random.Random(seed)
        self._state = {}
        self._level = {}

    # ---- 状态 -------------------------------------------------------------
    @staticmethod
    def _empty_stat():
        return [0.0] * STAT_LEN

    def _state_of(self, key_text):
        return self._state.setdefault(key_text, {
            "arms": {a: self._empty_stat() for a in self.arms},
            "rr": 0,
        })

    def _decay(self, state):
        if not self.half_life:
            return
        factor = 0.5 ** (1.0 / self.half_life)
        for stat in state["arms"].values():
            for i in range(STAT_LEN):
                stat[i] *= factor

    # ---- 聚合 -------------------------------------------------------------
    def _keys_for(self, full):
        if full == "*":
            return list(self._state)
        host, route, response = ctxmod.split(full)
        keys = []
        for k in self._state:
            h, r, resp = ctxmod.split(k)
            if h != host:
                continue
            if route != "*" and r != route and r != "*":
                continue
            if response != "*" and resp != response and resp != "*":
                continue
            keys.append(k)
        return keys

    def _aggregate(self, full, candidates):
        stats = {a: self._empty_stat() for a in candidates}
        for k in self._keys_for(full):
            for arm, v in self._state[k]["arms"].items():
                if arm not in stats:
                    continue
                for i in range(STAT_LEN):
                    stats[arm][i] += v[i]
        return stats

    def _sum_keys(self, keys, candidates):
        stats = {a: self._empty_stat() for a in candidates}
        for k in keys:
            state = self._state.get(k)
            if state is None:
                continue
            for arm, v in state["arms"].items():
                if arm not in stats:
                    continue
                for i in range(STAT_LEN):
                    stats[arm][i] += v[i]
        return stats

    def _level_stats(self, host, context, candidates):
        full = f"{host}|{context}"
        levels = ctxmod.fallbacks(full)
        for level in levels[:-1]:
            stats = self._aggregate(level, candidates)
            if all(stats[a][0] >= self.min_pulls for a in candidates):
                return level, stats
        return levels[-1], self._aggregate(levels[-1], candidates)

    # ---- 分层先验 ---------------------------------------------------------
    def _sibling_keys(self, host, route, response, level):
        if level == "*":
            return []
        keys = list(self._state)
        full = f"{host}|{route}|{response}"
        if response != "*" and level == full:
            siblings = [k for k in keys
                        if (lambda p: p[0] == host and p[1] == route
                            and p[2] != response)(ctxmod.split(k))]
            if siblings:
                return siblings
        if route != "*" and level in (full, f"{host}|{route}|*"):
            siblings = [k for k in keys
                        if (lambda p: p[0] == host and p[1] != route)(ctxmod.split(k))]
            if siblings:
                return siblings
        return [k for k in keys if ctxmod.split(k)[0] != host]

    def _prior_for(self, host, route, response, level, candidates):
        out = {a: (0.5, 0.0, False) for a in candidates}
        if not self.prior_strength or level == "*":
            return out
        keys = self._sibling_keys(host, route, response, level)
        if not keys:
            return out
        totals = self._sum_keys(keys, candidates)
        for arm, (pulls, successes, _failures, cost_sum, cost_n) in totals.items():
            mean = (successes + 1.0) / (pulls + 2.0) if pulls > 0 else 0.5
            cost_mean = cost_sum / cost_n if cost_n else 0.0
            out[arm] = (mean, cost_mean, cost_n > 0)
        return out

    def _effective(self, level, host, route, response, candidates):
        local = self._aggregate(level, candidates)
        prior = self._prior_for(host, route, response, level, candidates)
        eff = {}
        for arm in candidates:
            stat = list(local[arm])
            mean, cost_mean, has_cost = prior[arm]
            pseudo_success = self.prior_strength * mean
            pseudo_failure = self.prior_strength * (1.0 - mean)
            stat[0] += pseudo_success + pseudo_failure
            stat[1] += pseudo_success
            stat[2] += pseudo_failure
            if has_cost and self.prior_strength:
                stat[3] += cost_mean * self.prior_strength
                stat[4] += self.prior_strength
            eff[arm] = stat
        return eff

    # ---- 选举 -------------------------------------------------------------
    @staticmethod
    def _context_parts(context):
        route, _, response = str(context).partition("|")
        return route or "other", response or "unknown"

    def select(self, host, context, candidates=None):
        arms = [a for a in (candidates or self.arms) if a in self.arms]
        if not arms:
            arms = list(self.arms)
        route, response = self._context_parts(context)
        full = f"{host}|{route}|{response}"
        if self.algorithm == "fixed":
            self._level[full] = full
            return arms[0]
        if self.algorithm == "round_robin":
            state = self._state_of(full)
            arm = arms[state["rr"] % len(arms)]
            state["rr"] = (state["rr"] + 1) % len(arms)
            self._level[full] = full
            return arm

        level, local = self._level_stats(host, f"{route}|{response}", arms)
        self._level[full] = level
        warm = [a for a in arms if local[a][0] < self.min_pulls]
        if warm:
            return min(warm, key=lambda a: (local[a][0], arms.index(a)))
        eff = self._effective(level, host, route, response, arms)
        if self.algorithm == "ucb1":
            total = sum(eff[a][0] for a in arms)
            return max(arms, key=lambda a: self._ucb(eff[a], total))
        return max(arms, key=lambda a: self._thompson(eff[a]))

    def _penalty(self, stat):
        if not self.cost_weight or not stat[4]:
            return 0.0
        return self.cost_weight * min(1.0, stat[3] / stat[4] / self.cost_cap_ms)

    def _thompson(self, stat):
        _, successes, failures, _, _ = stat
        return self._rng.betavariate(1 + successes, 1 + failures) - self._penalty(stat)

    def _ucb(self, stat, total):
        pulls, successes, _, _, _ = stat
        if pulls <= 0:
            return float("inf")
        return (successes / pulls + self.ucb_c * math.sqrt(math.log(max(1, total)) / pulls)
                - self._penalty(stat))

    def update(self, host, context, arm, outcome, cost_ms=0, rate_limited=False):
        if arm not in self.arms:
            raise ValueError(f"未知客户端臂 {arm!r}")
        if rate_limited or outcome not in REWARD:
            return False
        reward = REWARD[outcome]
        state = self._state_of(f"{host}|{context}")
        self._decay(state)
        stat = state["arms"][arm]
        stat[0] += 1
        stat[1] += reward
        stat[2] += 1.0 - reward
        stat[3] += min(self.cost_cap_ms, max(0.0, float(cost_ms or 0)))
        stat[4] += 1
        return True

    def update_from_outcome(self, host, context, arm, outcome, elapsed_ms=0,
                            rate_limited=False):
        return self.update(host, context, arm, outcome, elapsed_ms, rate_limited)

    def last_level(self, host, context):
        route, response = self._context_parts(context)
        return self._level.get(f"{host}|{route}|{response}")

    # ---- 诊断 -------------------------------------------------------------
    @staticmethod
    def _wilson(successes, pulls, z=1.96):
        if pulls <= 0:
            return [0.0, 1.0]
        p = successes / pulls
        denom = 1.0 + z * z / pulls
        center = (p + z * z / (2.0 * pulls)) / denom
        half = z * math.sqrt(max(0.0, p * (1.0 - p) / pulls
                                 + z * z / (4.0 * pulls * pulls))) / denom
        return [round(max(0.0, center - half), 3),
                round(min(1.0, center + half), 3)]

    @staticmethod
    def _mean(stat):
        return stat[1] / stat[0] if stat[0] > 0 else 0.5

    def _diagnostics(self, eff, arms, key_text):
        utility = {a: self._mean(eff[a]) - self._penalty(eff[a]) for a in arms}
        seed_text = key_text + "|" + "|".join(
            f"{a}:{eff[a][1]:.6f}/{eff[a][2]:.6f}" for a in arms)
        rng = random.Random(int.from_bytes(
            hashlib.blake2b(seed_text.encode("utf-8"), digest_size=8).digest(), "big"))
        wins = {a: 0 for a in arms}
        for _ in range(DIAG_SAMPLES):
            best = max(arms, key=lambda a: (
                rng.betavariate(1.0 + eff[a][1], 1.0 + eff[a][2])
                - self._penalty(eff[a])))
            wins[best] += 1
        p_best = {a: round(wins[a] / DIAG_SAMPLES, 3) for a in arms}
        best_arm = max(arms, key=lambda a: (utility[a], p_best[a]))
        confidence = p_best[best_arm]
        enough = len(arms) == 1 or eff[best_arm][0] >= EXPLOIT_MIN_PULLS
        decision = ("exploit" if confidence >= DECISION_CONFIDENCE and enough
                    else "explore")
        return {"best_arm": best_arm, "confidence": confidence,
                "decision": decision, "p_best": p_best, "utility": utility}

    def _rows(self, candidates, local, eff, diag):
        rows = []
        for arm in candidates:
            stat = local[arm]
            est = eff[arm]
            rows.append({
                "arm": arm,
                "pulls": round(stat[0], 3),
                "successes": round(stat[1], 3),
                "failures": round(stat[2], 3),
                "mean": round(stat[1] / stat[0], 3) if stat[0] else None,
                "cost_ms": round(stat[3] / stat[4]) if stat[4] else None,
                "posterior_mean": round(self._mean(est), 3),
                "n_eff": round(est[0], 2),
                "utility": round(diag["utility"][arm], 3),
                "ci95": self._wilson(est[1], est[0]),
                "p_best": diag["p_best"][arm],
            })
        return rows

    def snapshot(self, host, context=None):
        arms = list(self.arms)
        if context is not None:
            route, response = self._context_parts(context)
            full = f"{host}|{route}|{response}"
            local = self._aggregate(full, arms)
            level, _ = self._level_stats(host, f"{route}|{response}", arms)
            eff = self._effective(level, host, route, response, arms)
            diag = self._diagnostics(eff, arms, full)
            return {"host": host, "context": context, "algorithm": self.algorithm,
                    "level": level, "prior_strength": self.prior_strength,
                    "half_life": self.half_life,
                    "min_effective_pulls": EXPLOIT_MIN_PULLS,
                    "best_arm": diag["best_arm"],
                    "confidence": diag["confidence"],
                    "decision": diag["decision"],
                    "arms": self._rows(arms, local, eff, diag)}
        full = f"{host}|*|*"
        local = self._aggregate(full, arms)
        level, _ = self._level_stats(host, "*|*", arms)
        eff = self._effective(level, host, "*", "*", arms)
        diag = self._diagnostics(eff, arms, full)
        return {"host": host, "algorithm": self.algorithm, "level": level,
                "prior_strength": self.prior_strength,
                "half_life": self.half_life,
                "min_effective_pulls": EXPLOIT_MIN_PULLS,
                "best_arm": diag["best_arm"],
                "confidence": diag["confidence"],
                "decision": diag["decision"],
                "arms": self._rows(arms, local, eff, diag),
                "contexts": sum(1 for k in self._state
                                if ctxmod.split(k)[0] == host)}

    # ---- 持久化 -----------------------------------------------------------
    def export_state(self):
        return {
            key: {
                "arms": {a: [round(float(x), 9) for x in stat]
                         for a, stat in sorted(state["arms"].items())},
                "rr": int(state.get("rr", 0)),
            }
            for key, state in sorted(self._state.items())
        }

    def import_state(self, state):
        if not isinstance(state, dict):
            raise ValueError("策略状态必须是对象")
        parsed = {}
        for key, row in state.items():
            if len(str(key).split("|")) != 3:
                raise ValueError(f"上下文键格式错误: {key!r}")
            if not isinstance(row, dict) or not isinstance(row.get("arms", {}), dict):
                raise ValueError(f"上下文 {key} 的状态必须是对象")
            arms = {}
            for arm, raw in (row.get("arms") or {}).items():
                if arm not in self.arms:
                    continue
                if not isinstance(raw, (list, tuple)) or len(raw) != STAT_LEN:
                    raise ValueError(f"臂 {arm} 的状态必须是 {STAT_LEN} 个数字")
                vals = []
                for value in raw:
                    try:
                        num = float(value)
                    except (TypeError, ValueError):
                        raise ValueError(f"臂 {arm} 的状态含非数字: {value!r}")
                    if not math.isfinite(num) or num < 0:
                        raise ValueError(f"臂 {arm} 的状态必须是非负有限数")
                    vals.append(num)
                arms[arm] = vals
            try:
                rr = int(row.get("rr", 0))
            except (TypeError, ValueError):
                raise ValueError(f"上下文 {key} 的 rr 必须是整数")
            if rr < 0:
                raise ValueError(f"上下文 {key} 的 rr 不能为负")
            parsed[key] = {"arms": arms, "rr": rr}
        for key, row in parsed.items():
            target = self._state_of(key)
            target["rr"] = row["rr"]
            for arm, vals in row["arms"].items():
                target["arms"][arm] = vals
        return len(parsed)
