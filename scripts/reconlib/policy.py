"""policy — 多臂老虎机：在合规的客户端臂之间做选举。

臂就是已有的客户端协议栈（native / cffi:<profile>），不是“绕过手段”。奖励只有两种：
- ok 记成功
- blocked / degraded 记失败

noise / auth / missing / rate_limited 不更新：这些不是客户端栈的差异，更新只会把噪声学成策略。
同一个 Bandit 按 host 分隔状态；算法与随机种子可固定，便于复现与离线推演。

ContextualBandit 在此之上按 (host, 路由类, 响应类) 细分：degraded 记半个奖励，
成本高的臂按 5000ms 归一化扣分；样本不足时沿 host|route|response → host|route|* →
host|*|* → * 回退聚合。
"""

import math
import random

from . import context as ctxmod

ALGORITHMS = ("thompson", "ucb1", "fixed", "round_robin")
NO_UPDATE = {"noise", "auth", "missing"}


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


REWARD = {"ok": 1.0, "degraded": 0.5, "blocked": 0.0}


class ContextualBandit:
    """按 (host, route, response) 维护后验；样本不足时沿回退链聚合。"""

    def __init__(self, arms, algorithm="thompson", seed=None, min_pulls=1,
                 ucb_c=1.414, cost_weight=0.1, cost_cap_ms=5000):
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
        self._rng = random.Random(seed)
        self._state = {}
        self._level = {}

    def _state_of(self, key_text):
        return self._state.setdefault(key_text, {
            # pulls, successes, failures, cost_sum, cost_n
            "arms": {a: [0, 0.0, 0.0, 0.0, 0] for a in self.arms},
            "rr": 0,
        })

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
        stats = {a: [0, 0.0, 0.0, 0.0, 0] for a in candidates}
        for k in self._keys_for(full):
            for arm, v in self._state[k]["arms"].items():
                if arm not in stats:
                    continue
                for i in range(5):
                    stats[arm][i] += v[i]
        return stats

    def _penalty(self, stat):
        if not self.cost_weight or not stat[4]:
            return 0.0
        return self.cost_weight * min(1.0, stat[3] / stat[4] / self.cost_cap_ms)

    def _level_stats(self, host, context, candidates):
        full = f"{host}|{context}"
        levels = ctxmod.fallbacks(full)
        for level in levels[:-1]:
            stats = self._aggregate(level, candidates)
            if all(stats[a][0] >= self.min_pulls for a in candidates):
                return level, stats
        return levels[-1], self._aggregate(levels[-1], candidates)

    def select(self, host, context, candidates=None):
        arms = [a for a in (candidates or self.arms) if a in self.arms]
        if not arms:
            arms = list(self.arms)
        full = f"{host}|{context}"
        if self.algorithm == "fixed":
            self._level[full] = full
            return arms[0]
        if self.algorithm == "round_robin":
            state = self._state_of(full)
            arm = arms[state["rr"] % len(arms)]
            state["rr"] = (state["rr"] + 1) % len(arms)
            self._level[full] = full
            return arm

        level, stats = self._level_stats(host, context, arms)
        self._level[full] = level
        warm = [a for a in arms if stats[a][0] < self.min_pulls]
        if warm:
            return min(warm, key=lambda a: (stats[a][0], arms.index(a)))
        if self.algorithm == "ucb1":
            total = sum(stats[a][0] for a in arms)
            return max(arms, key=lambda a: self._ucb(stats[a], total))
        return max(arms, key=lambda a: self._thompson(stats[a]))

    def _thompson(self, stat):
        _, successes, failures, _, _ = stat
        return self._rng.betavariate(1 + successes, 1 + failures) - self._penalty(stat)

    def _ucb(self, stat, total):
        pulls, successes, _, _, _ = stat
        if pulls == 0:
            return float("inf")
        return (successes / pulls + self.ucb_c * math.sqrt(math.log(max(1, total)) / pulls)
                - self._penalty(stat))

    def update(self, host, context, arm, outcome, cost_ms=0, rate_limited=False):
        if arm not in self.arms:
            raise ValueError(f"未知客户端臂 {arm!r}")
        if rate_limited or outcome not in REWARD:
            return False
        reward = REWARD[outcome]
        stat = self._state_of(f"{host}|{context}")["arms"][arm]
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
        return self._level.get(f"{host}|{context}")

    def _rows(self, stats):
        rows = [{
            "arm": arm,
            "pulls": int(v[0]),
            "successes": round(v[1], 3),
            "failures": round(v[2], 3),
            "mean": round(v[1] / v[0], 3) if v[0] else None,
            "cost_ms": round(v[3] / v[4]) if v[4] else None,
        } for arm, v in stats.items()]
        pulled = [r for r in rows if r["pulls"]]
        best = max(pulled, key=lambda r: (r["mean"], r["pulls"]))["arm"] if pulled else None
        return rows, best

    def snapshot(self, host, context=None):
        if context is not None:
            full = f"{host}|{context}"
            stats = self._aggregate(full, list(self.arms))
            rows, best = self._rows(stats)
            return {"host": host, "context": context,
                    "algorithm": self.algorithm,
                    "level": self._level.get(full), "best_arm": best,
                    "arms": rows}
        stats = self._aggregate(f"{host}|*|*", list(self.arms))
        rows, best = self._rows(stats)
        return {"host": host, "algorithm": self.algorithm,
                "best_arm": best, "arms": rows,
                "contexts": sum(1 for k in self._state
                                if ctxmod.split(k)[0] == host)}
