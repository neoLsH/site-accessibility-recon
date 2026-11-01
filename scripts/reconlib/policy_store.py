"""policy_store — 把选臂后验存成仓库外档案，供下一轮热启动。

档案只含策略状态（host / 上下文 / 每臂计数），不含响应体、Cookie 或凭证。
`recon/` 默认被忽略；也可用 `--policy-out` 指到任意仓库外路径。

格式 `recon-policy/1`：schema、配置快照、state（export_state 的产物）、meta。
恢复时只接受同 schema 与合法状态；档案里的臂若不在本轮臂列表里会被忽略。
"""

import json
import os
import tempfile
from datetime import datetime, timezone

SCHEMA = "recon-policy/1"
MAX_BYTES = 16 * 1024 * 1024


def dump(bandit, path, meta=None):
    """原子写入策略档案；返回写入摘要。"""
    state = bandit.export_state()
    body = {
        "schema": SCHEMA,
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "algorithm": bandit.algorithm,
        "arms": list(bandit.arms),
        "prior_strength": bandit.prior_strength,
        "half_life": bandit.half_life,
        "cost_weight": bandit.cost_weight,
        "cost_cap_ms": bandit.cost_cap_ms,
        "contexts": len(state),
        "state": state,
        "meta": dict(meta or {}),
    }
    text = json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ValueError(f"策略档案超过 {MAX_BYTES} 字节，先按 host 拆分")
    path = os.path.abspath(path)
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".recon-policy-", suffix=".tmp",
                               dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return {"path": path, "schema": SCHEMA, "contexts": len(state),
            "arms": list(bandit.arms)}


def load(path):
    """读取并做结构校验；返回整个档案对象。"""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        raise ValueError(f"读取策略档案失败: {e}")
    if size > MAX_BYTES:
        raise ValueError(f"策略档案超过 {MAX_BYTES} 字节")
    try:
        with open(path, encoding="utf-8") as f:
            body = json.load(f)
    except OSError as e:
        raise ValueError(f"读取策略档案失败: {e}")
    except ValueError as e:
        raise ValueError(f"策略档案不是合法 JSON: {e}")
    if not isinstance(body, dict):
        raise ValueError("策略档案必须是对象")
    if body.get("schema") != SCHEMA:
        raise ValueError(f"策略档案 schema 不匹配（需要 {SCHEMA}）")
    if not isinstance(body.get("state"), dict):
        raise ValueError("策略档案缺少 state 对象")
    return body


def restore(bandit, path):
    """把档案里的后验恢复到 bandit；返回摘要。"""
    body = load(path)
    try:
        contexts = bandit.import_state(body["state"])
    except ValueError as e:
        raise ValueError(f"策略档案状态无效: {e}")
    known = set(bandit.arms)
    stored = {str(a) for a in (body.get("arms") or [])}
    return {
        "path": os.path.abspath(path),
        "schema": body["schema"],
        "saved_at": body.get("saved_at"),
        "contexts": contexts,
        "arms": [a for a in bandit.arms if a in stored],
        "ignored_arms": sorted(stored - known),
    }


def context_rates(body):
    """把档案里的后验转成 `bandit --rates-json` 同构的上下文胜率。

    只取有本地样本的格；用全键 `host|route|response` 作为上下文，避免跨 host 合并。
    """
    if not isinstance(body, dict):
        raise ValueError("策略档案必须是对象")
    groups = []
    for key, row in sorted((body.get("state") or {}).items()):
        if len(str(key).split("|")) != 3:
            raise ValueError(f"上下文键格式错误: {key!r}")
        rates = {}
        pulls_total = 0.0
        for arm, stat in (row.get("arms") or {}).items():
            if not isinstance(stat, (list, tuple)) or len(stat) < 2:
                raise ValueError(f"上下文 {key} 的臂 {arm} 状态无效")
            pulls = float(stat[0])
            wins = float(stat[1])
            pulls_total += pulls
            if pulls > 0:
                rates[str(arm)] = round(min(1.0, max(0.0, wins / pulls)), 6)
        if rates:
            groups.append({
                "context": key,
                "pages": max(1, int(round(pulls_total))),
                "rates": rates,
            })
    if not groups:
        raise ValueError("策略档案里没有可用的本地样本")
    return groups
