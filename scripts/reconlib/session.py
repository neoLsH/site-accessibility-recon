"""session — 唯一网络编排层：robots → pacing → 选臂 → 取样 → 归类 → 更新 → 行。

不打印、不写文件。调用方传 save 回调决定是否落盘，拿返回的行决定落盘位置。
所有能力点（fetch / crawl / canary / quick / matrix）共用这一层，避免每个 CLI
再写一份“先 robots、再节流、再试臂”的逻辑。
"""

from urllib.parse import urlsplit

from . import UA_DEFAULT, evidence, fetch, measure, pacing, render, robots
from .classify import classify
from .policy import ALGORITHMS, Bandit

RANK = {"ok": 3, "degraded": 2, "blocked": 1}


def host_of(url):
    return urlsplit(url).netloc or url


def fetch_observation(url, arm, timeout=20, ua=UA_DEFAULT):
    if arm.transport in ("native", "cffi"):
        return fetch.fetch_one(url, arm.name, timeout=timeout, ua=ua)
    return render.fetch(url, arm, timeout=timeout, ua=ua)


def display_arm(arm):
    if arm.transport in ("native", "cffi"):
        return fetch.display_arm(arm.name)
    return arm.name


class ArmSession:
    def __init__(self, arm_names, registry=None, policy="thompson", seed=None,
                 min_pulls=1, delay=pacing.DEFAULT_DELAY, timeout=20,
                 ua=UA_DEFAULT, max_consecutive_blocked=3, save=None,
                 require=(), permission=False, clock=None, sleep=None):
        from . import arms as armreg
        self.registry = registry or armreg.load()
        self.arms = [self.registry.get(name) for name in arm_names]
        if not self.arms:
            raise ValueError("至少需要一个客户端臂")
        if policy not in ALGORITHMS:
            raise ValueError(f"未知策略 {policy!r}")
        self.bandit = Bandit([a.name for a in self.arms], algorithm=policy,
                             seed=seed, min_pulls=min_pulls)
        kw = {"delay": delay,
              "max_consecutive_blocked": max_consecutive_blocked}
        if clock is not None:
            kw["clock"] = clock
        if sleep is not None:
            kw["sleep"] = sleep
        self.pace = pacing.Pacing(**kw)
        self.timeout = timeout
        self.ua = ua
        self.save = save
        self.require = tuple(require)
        self.permission = permission
        self.trials = []

    def snapshot(self, host):
        return self.bandit.snapshot(host)

    def probe(self, url, selector="", min_content=200, max_attempts=None,
              require=None, permission=None, warn=None, stop_on_ok=True):
        require = tuple(self.require if require is None else require)
        permission = self.permission if permission is None else permission
        host = host_of(url)
        page = {"url": url, "host": host, "tries": [], "evidence": None,
                "stopped": None, "skipped": None, "halt": None,
                "requests": 0, "robots": "skipped" if permission else None,
                "last": None}
        if not permission:
            allowed = robots.decision(url, timeout=self.timeout, ua=self.ua)
            page["robots"] = allowed
            if allowed == "deny":
                page["skipped"] = "robots-deny"
                page["stopped"] = "robots-deny"
                return page
            if allowed == "unknown" and warn:
                warn(host)
        if self.pace.aborted(host):
            reason = self.pace.snapshot(host)["reason"]
            page["skipped"] = f"circuit-open: {reason}"
            page["stopped"] = "circuit-open"
            page["halt"] = "circuit-open"
            return page

        candidates, skipped = self.registry.filter([a.name for a in self.arms],
                                                   require)

        def flush_skipped():
            for name, reason in skipped:
                page["tries"].append({"arm": name, "skipped": True,
                                      "outcome": "noise", "reason": reason})

        if not candidates:
            flush_skipped()
            page["halt"] = "no-arm"
            page["skipped"] = "no-arm"
            page["stopped"] = "no-arm"
            return page

        used = 0
        while candidates:
            if max_attempts is not None and used >= max_attempts:
                page["halt"] = "budget"
                page["stopped"] = "budget"
                break
            name = self.bandit.select(host, candidates)
            arm = self.registry.get(name)
            self.pace.wait(host)
            obs = fetch_observation(url, arm, timeout=self.timeout, ua=self.ua)
            html = measure.is_html(obs.get("ctype"))
            content = measure.content_len(obs.get("body") or b"",
                                          selector) if html else "-"
            verdict = classify(obs, selector, min_content, content=content)

            if obs.get("skipped"):
                page["tries"].append({
                    "arm": name, "skipped": True, "outcome": verdict["outcome"],
                    "reason": obs.get("error") or "skipped"})
                candidates.remove(name)
                continue

            used += 1
            page["last"] = dict(verdict)
            state = self.pace.observe(
                host, verdict["outcome"], verdict["rate_limited"],
                pacing.retry_after(obs.get("headers") or ""))
            self.bandit.update_from_outcome(host, name, verdict["outcome"],
                                            verdict["rate_limited"])
            saved = self.save(url, name, obs.get("body") or b"") if self.save else None
            trial = evidence.trial_row(
                url, name, verdict, attempt=used,
                elapsed_ms=obs.get("elapsed_ms"))
            if saved:
                trial["saved"] = saved
            self.trials.append(trial)
            page["tries"].append({
                "arm": name, "status": obs.get("status", 0),
                "bytes": obs.get("bytes", 0), "content": content,
                "outcome": verdict["outcome"],
                "attributable": verdict["attributable"],
                "rate_limited": verdict["rate_limited"], "saved": saved,
                "elapsed_ms": obs.get("elapsed_ms", 0),
                "final": obs.get("final_url") or url,
            })
            obs["content"] = content
            row = evidence.evidence_row(obs, verdict, route=evidence.route_of(url))
            if page["evidence"] is None or RANK.get(row["outcome"], 0) > \
                    RANK.get(page["evidence"]["outcome"], 0):
                page["evidence"] = row

            if verdict["outcome"] == "ok":
                page["stopped"] = "ok"
                if stop_on_ok:
                    page["requests"] = used
                    flush_skipped()
                    return page
                candidates.remove(name)
                continue
            if not verdict["attributable"]:
                page["stopped"] = verdict["outcome"]
                page["requests"] = used
                flush_skipped()
                return page
            candidates.remove(name)
            if state["aborted"]:
                page["stopped"] = "circuit-open"
                page["halt"] = "circuit-open"
                page["reason"] = state["reason"]
                page["requests"] = used
                flush_skipped()
                return page

        page["requests"] = used
        if page["evidence"] is None and page["halt"] is None:
            page["stopped"] = "no-arm"
        flush_skipped()
        return page
