"""pacing — 节流、退避与熔断。

三条硬约束：
- 每个 host 至少间隔 delay（默认 5s；本机回环可用 0）
- 429 / 5xx 之后指数退避，只推迟“下一次请求”，不自动重试同一 URL
- 连续 blocked 达阈值就熔断：继续打只会把限频与封禁越打越深

时钟与 sleep 可注入，测试不需要真的等待。
"""

import re
import time
from email.utils import parsedate_to_datetime

DEFAULT_DELAY = 5.0
MAX_BACKOFF = 60.0
LOOPBACK = {"localhost", "127.0.0.1", "::1", "[::1]"}


def default_delay(host):
    host = (host or "").lower()
    if host in LOOPBACK or host.startswith("127."):
        return 0.0
    return DEFAULT_DELAY


def retry_after(headers, max_wait=MAX_BACKOFF, now=None):
    """从原始响应头取 Retry-After 秒数（支持秒数与 HTTP-date），取不到返回 None。"""
    if not headers:
        return None
    m = re.search(r"(?im)^retry-after:\s*(\S+)", headers)
    if not m:
        return None
    value = m.group(1).strip()
    if value.isdigit():
        seconds = float(value)
    else:
        try:
            seconds = parsedate_to_datetime(value).timestamp() - (now or time.time())
        except (TypeError, ValueError):
            return None
    return max(0.0, min(seconds, max_wait))


class Pacing:
    def __init__(self, delay=DEFAULT_DELAY, max_consecutive_blocked=3,
                 backoff_base=2.0, max_backoff=MAX_BACKOFF,
                 clock=time.monotonic, sleep=time.sleep):
        self.delay = max(0.0, float(delay))
        self.max_consecutive_blocked = max(1, int(max_consecutive_blocked))
        self.backoff_base = max(1.0, float(backoff_base))
        self.max_backoff = max(0.0, float(max_backoff))
        self._clock = clock
        self._sleep = sleep
        self.hosts = {}

    def _state(self, host):
        return self.hosts.setdefault(host, {
            "last": None, "backoff": 0.0, "blocked": 0, "noise": 0,
            "aborted": False, "reason": None,
        })

    def wait(self, host):
        """在真正发请求前调用；返回本次实际等待的秒数。"""
        st = self._state(host)
        now = self._clock()
        if st["last"] is None:
            st["last"] = now
            return 0.0
        gap = self.delay + st["backoff"]
        remain = st["last"] + gap - now
        if remain > 0:
            self._sleep(remain)
            now += remain
            st["last"] = now
            return remain
        st["last"] = now
        return 0.0

    def observe(self, host, outcome, rate_limited=False, retry_after=None):
        """记录一次结局；返回该 host 的最新状态（含 aborted / reason）。"""
        st = self._state(host)
        if st["aborted"]:
            return self.snapshot(host)
        if outcome == "blocked":
            st["blocked"] += 1
            if rate_limited:
                hint = retry_after
                if hint is None:
                    hint = self.backoff_base * (2 ** (st["blocked"] - 1))
                st["backoff"] = min(self.max_backoff, max(st["backoff"], float(hint)))
            if st["blocked"] >= self.max_consecutive_blocked:
                st["aborted"] = True
                st["reason"] = f"连续 {st['blocked']} 次 blocked"
        elif outcome == "noise":
            st["noise"] += 1
            st["backoff"] = min(self.max_backoff,
                                self.backoff_base * (2 ** (st["noise"] - 1)))
        else:
            # ok / degraded / auth / missing 不是封禁信号，重置熔断与退避
            st["blocked"] = 0
            st["noise"] = 0
            st["backoff"] = 0.0
        return self.snapshot(host)

    def aborted(self, host):
        return bool(self._state(host)["aborted"])

    def snapshot(self, host):
        st = self._state(host)
        return {"host": host, "delay": self.delay, "backoff": st["backoff"],
                "consecutive_blocked": st["blocked"], "noise_streak": st["noise"],
                "aborted": st["aborted"], "reason": st["reason"]}
