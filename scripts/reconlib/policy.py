"""policy — 多臂老虎机：在合规的客户端臂之间做选举。

臂就是已有的客户端协议栈（native / cffi:<profile>），不是“绕过手段”。奖励只有两种：
- ok 记成功
- blocked / degraded 记失败

noise / auth / missing / rate_limited 不更新：这些不是客户端栈的差异，更新只会把噪声学成策略。
同一个 Bandit 按 host 分隔状态；算法与随机种子可固定，便于复现与离线推演。
"""

import math
import random

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
