"""evidence — 证据行与试验行的落盘契约。

证据行（outcome）直接喂给 stats：ok | degraded | blocked | auth | missing | noise。
试验行（kind: trial）记录探索过程，故意用 trial_outcome 字段、不带 outcome——
这样误把试验日志喂给 stats 会直接报错，而不是悄悄污染指标分母。
"""

import json
import os
from urllib.parse import urlsplit

OUTCOMES = ("ok", "degraded", "blocked", "auth", "missing", "noise")
TRIAL_OUTCOMES = OUTCOMES


def route_of(url):
    return urlsplit(str(url or "")).path or "/"


def evidence_row(observation, classification, route=None, context=None):
    outcome = classification.get("outcome")
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome 非法 {outcome!r}，应为 {'/'.join(OUTCOMES)}")
    url = observation.get("final_url") or observation.get("url") or ""
    content = observation.get("content")
    row = {
        "route": route or route_of(url),
        "final_url": url,
        "arm": observation.get("arm") or "",
        "outcome": outcome,
        "content_len": content if isinstance(content, int) else None,
        "status": int(observation.get("status") or 0),
        "waf_no": int(classification.get("waf_no") or 0),
        "attributable": bool(classification.get("attributable")),
    }
    if context:
        row["context"] = context
    return row


def trial_row(url, arm, classification, attempt=1, elapsed_ms=None,
              route=None, context=None, cost_ms=None):
    outcome = classification.get("outcome")
    if outcome not in TRIAL_OUTCOMES:
        raise ValueError(f"outcome 非法 {outcome!r}，应为 {'/'.join(TRIAL_OUTCOMES)}")
    row = {
        "kind": "trial",
        "route": route or route_of(url),
        "url": url,
        "arm": arm,
        "attempt": attempt,
        "trial_outcome": outcome,
        "rate_limited": bool(classification.get("rate_limited")),
    }
    if elapsed_ms is not None:
        row["elapsed_ms"] = int(elapsed_ms)
    if context:
        row["context"] = context
    if cost_ms is not None:
        row["cost_ms"] = int(cost_ms)
    return row


def _write(path, rows, mode):
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    with open(path, mode, encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_jsonl(path, rows):
    _write(path, rows, "w")


def append_jsonl(path, rows):
    _write(path, rows, "a")


def read_jsonl(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError as e:
                raise ValueError(f"{path}:{lineno}: JSON 解析失败: {e}")
    return rows
