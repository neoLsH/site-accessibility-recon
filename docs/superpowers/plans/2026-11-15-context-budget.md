# recon brief（Agent 上下文预算）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增只读命令 `recon brief`，把一个 recon 工作区收敛成有界、字节稳定、可按 digest 复用的 Agent 上下文包（`brief/1`）。

**Architecture:** 逻辑全部落在 `scripts/reconlib/brief.py`（纯函数：组装、digest、预算裁剪、估算、文本渲染），`scripts/brief.py` 只做文件发现、参数解析与调用既有原子（`advice.recommend` / `stats.summarize` / `report.load_profile`）。CLI 在喂给 `advice` 前按规范化行内容排序，使输出与文件行序无关。

**Tech Stack:** Python 3 标准库（argparse / json / hashlib / re / tempfile）；测试用 `unittest` + `tests/support.py` 的子进程调用；门禁为 `scripts/self-check.py` 与 `python3 -m unittest discover -s tests`。

**Spec:** [docs/superpowers/specs/2026-11-15-context-budget-design.md](../specs/2026-11-15-context-budget-design.md)

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `scripts/reconlib/brief.py`（新建） | `estimate_tokens` / `_gates` / `_gaps` / `compose` / `digest_of` / `trim` / `finalize` / `render_text`；无 IO |
| `scripts/brief.py`（新建） | 发现工作区产物 → 调原子层 → 调 `brief.*` → 输出 |
| `scripts/recon.py`（改） | `TOOLS` 加 `brief`；`to_json` 把 `brief` 当单对象命令 |
| `tests/test_brief.py`（新建） | 13 个用例，全部本地 fixture |
| `scripts/self-check.py`（改） | 新增 `check_brief_smoke()` 并挂进 `main()` |
| `SKILL.md` / `AGENTS.md` / `references/cli.md` / `references/agent.md` / `references/forms.md` / `assets/workspace/README.md` / `README.md` / `CHANGELOG.md` | 契约、命令与版本同步到 1.23.0 |

约定：所有命令都在仓库根目录执行。提交身份用 per-command 覆盖，不改 git config：

```bash
git -c user.name=neoLsH -c user.email=43921685+neoLsH@users.noreply.github.com commit -m "..."
```

---

### Task 1: `estimate_tokens` 与档案派生（gates / gaps）

**Files:**
- Create: `scripts/reconlib/brief.py`
- Test: `tests/test_brief.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_brief.py`：

```python
import json
import os
import shutil
import tempfile
import unittest

import support

from reconlib import brief  # support.py 已把 scripts/ 加入 sys.path

EXAMPLE_DIR = os.path.join(support.ROOT, "assets", "example")


def make(files):
    d = tempfile.mkdtemp(prefix="recon-brief-")
    for name, body in files.items():
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            f.write(body)
    return d


class TokenEstimateTest(unittest.TestCase):
    def test_mixed_text(self):
        self.assertEqual(brief.estimate_tokens("abcdefgh"), 2)   # 8/3.5 -> 2.29 -> 2
        self.assertEqual(brief.estimate_tokens("中文四字"), 4)   # 4 CJK -> 4
        self.assertEqual(brief.estimate_tokens(""), 0)

    def test_order_key_ignores_volatile_fields(self):
        row = {"url": "https://a/1", "outcome": "ok", "elapsed_ms": 12}
        other = {"url": "https://a/1", "outcome": "ok", "elapsed_ms": 999}
        self.assertEqual(brief.order_key(row), brief.order_key(other))
        self.assertNotEqual(brief.order_key(row),
                            brief.order_key({**row, "outcome": "blocked"}))


class GatesTest(unittest.TestCase):
    def test_first_fail_follows_document_order(self):
        profile = {"gates": [
            {"name": "网络可达", "layer": "L1", "status": "pass"},
            {"name": "请求语义", "layer": "L3", "status": "fail"},
            {"name": "运行时", "layer": "L4", "status": "fail"},
            {"name": "业务", "layer": "L5", "status": "pass"},
        ]}
        gates = brief.gates_of(profile)
        self.assertEqual(gates["first_fail"], {"name": "请求语义", "layer": "L3"})
        self.assertEqual((gates["pass"], gates["fail"], gates["unknown"]), (2, 2, 0))

    def test_empty_and_unknown(self):
        self.assertEqual(brief.gates_of(None)["first_fail"], None)
        gates = brief.gates_of({"gates": [{"name": "x", "status": "maybe"}]})
        self.assertEqual((gates["pass"], gates["fail"], gates["unknown"]), (0, 0, 1))


class GapsTest(unittest.TestCase):
    def test_missing_profile(self):
        self.assertEqual(brief.gaps_of(None, {"n": 8}),
                         ["缺 profile.yaml：没有闸门图与口径"])

    def test_profile_gaps(self):
        profile = {
            "metrics": {"baseline": None},
            "gates": [{"name": "请求语义", "status": "fail", "evidence": ""}],
            "plan": {},
        }
        gaps = brief.gaps_of(profile, {"n": 8})
        self.assertEqual(gaps, ["metrics.baseline 未测", "「请求语义」门无证据", "plan.level 未推导"])

    def test_no_evidence(self):
        gaps = brief.gaps_of({"metrics": {"baseline": 1}, "plan": {"level": "S1"}}, {"n": 0})
        self.assertEqual(gaps, ["没有证据行：任何结论都是假设"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/neolshuang/project/site-accessibility-recon && python3 -m unittest tests.test_brief -v`

Expected: FAIL / ERROR，`ModuleNotFoundError: No module named 'reconlib.brief'`（或 ImportError）。

- [ ] **Step 3: 写最小实现**

新建 `scripts/reconlib/brief.py`：

```python
"""brief — 把工作区收敛成有界、可复用的 Agent 上下文包。

只编排既有原子层：advice.recommend / stats.summarize 的结果由调用方传入，
本模块不做 IO、不发请求、不重写统计与建议逻辑。
"""

import hashlib
import json
import re

SCHEMA = "brief/1"
LOGIC_VERSION = "brief/1:r1"
DEFAULT_BUDGET = 2000
MIN_BUDGET = 200

DOC_READS = {
    "collect-more": [("evidence-matrix.md", "补样本的实验设计与非确定性")],
    "separate-noise": [("triage-and-gates.md", "链路噪声与拦截的区分判据")],
    "check-authorization": [("layers.md", "L5 凭证与签名的判据")],
    "check-routing": [("triage-and-gates.md", "路由不存在与业务空态的判据")],
    "check-extraction": [("triage-and-gates.md", "200 但内容不足的兜底判据"),
                          ("metrics.md", "内容完整率的口径")],
    "backoff": [("strategy.md", "限频冷却与节流参数")],
    "switch-arm": [("strategy.md", "选臂的上下文与奖励"),
                   ("arms.md", "臂注册表与能力过滤")],
    "differentiate": [("detection-surface.md", "客户端采集与服务端评分的判定实验")],
    "maintain": [("review-scorecard.md", "交付前自查评分卡")],
}

CJK = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]")

VOLATILE = ("ts", "timestamp", "time", "fetched_at", "elapsed_ms", "cost_ms",
            "run_id", "started_at", "finished_at")


def estimate_tokens(text):
    """粗略 token 估算：CJK 按 1 token/字，其余按 3.5 字符/token。只是量级参考。"""
    cjk = len(CJK.findall(text))
    return int(round(cjk + (len(text) - cjk) / 3.5))


def order_key(row):
    """排序键：剔除易变字段，让文件行序与耗时不影响输出。"""
    stable = {key: value for key, value in row.items() if key not in VOLATILE}
    return json.dumps(stable, sort_keys=True, ensure_ascii=False)


def gates_of(profile):
    gates = (profile or {}).get("gates") or []
    counts = {"pass": 0, "fail": 0, "unknown": 0}
    first = None
    for gate in gates:
        if not isinstance(gate, dict):
            counts["unknown"] += 1
            continue
        status = str(gate.get("status") or "").strip().lower()
        if status not in ("pass", "fail"):
            counts["unknown"] += 1
            continue
        counts[status] += 1
        if status == "fail" and first is None:
            first = gate
    first_fail = None
    if first is not None:
        first_fail = {"name": first.get("name") or "未命名",
                      "layer": first.get("layer") or "unknown"}
    return {"first_fail": first_fail, "pass": counts["pass"],
            "fail": counts["fail"], "unknown": counts["unknown"]}


def gaps_of(profile, overall):
    gaps = []
    if profile is None:
        gaps.append("缺 profile.yaml：没有闸门图与口径")
        return gaps
    metrics = profile.get("metrics") or {}
    if not metrics.get("baseline"):
        gaps.append("metrics.baseline 未测")
    for gate in profile.get("gates") or []:
        if not isinstance(gate, dict):
            continue
        if (str(gate.get("status") or "").lower() == "fail"
                and not str(gate.get("evidence") or "").strip()):
            gaps.append(f"「{gate.get('name') or '未命名'}」门无证据")
            break
    plan = profile.get("plan") or {}
    if isinstance(plan, dict) and not plan.get("level"):
        gaps.append("plan.level 未推导")
    if not (overall or {}).get("n"):
        gaps.append("没有证据行：任何结论都是假设")
    return gaps[:3]
```

注意：`gates_of` 取 `gates` 书写顺序里第一个 `fail`（档案模板按层序书写），不复制 `plan.py` 的 `layer_key`。

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m unittest tests.test_brief -v`

Expected: `OK`（7 个用例通过）。

- [ ] **Step 5: 提交**

```bash
git add scripts/reconlib/brief.py tests/test_brief.py
git -c user.name=neoLsH -c user.email=43921685+neoLsH@users.noreply.github.com commit -m "feat(brief): 档案派生闸门与缺口 + token 估算"
```

---

### Task 2: `compose`（verdict / evidence / metrics / next_reads / artifacts）

**Files:**
- Modify: `scripts/reconlib/brief.py`（追加 `compose` 与 `_worst_first`）
- Test: `tests/test_brief.py`（追加 `ComposeTest`）

- [ ] **Step 1: 写失败测试**

在 `tests/test_brief.py` 的 `GapsTest` 之后追加：

```python
ADVICE = {
    "evidence": {
        "n": 8, "routes": 3,
        "outcomes": {"ok": 4, "degraded": 2, "blocked": 1,
                     "auth": 0, "missing": 0, "noise": 1},
        "noise_excluded_n": 7,
        "arms": ["native", "cffi:chrome"],
        "contexts": {"b.example|list|content": 2, "a.example|article|challenge": 6},
    },
    "findings": [{"code": "blocked", "count": 1, "message": "出现被拦截样本"}],
    "next": {
        "action": "switch-arm", "layer": "L2", "countermeasure": "S2",
        "why": "有臂成功、有臂被拦", "confidence": "assumed",
        "commands": ["recon fetch <url> --arms cffi:chrome"],
        "stop": ["两臂各 3 次仍无差异就停"],
    },
}

SUMMARY = {
    "overall": {"n": 8, "access": {"rate": 0.857, "ci95": [0.487, 0.974]},
                "complete": {"rate": 0.667, "ci95": [0.3, 0.903]}},
    "groups": [
        {"route": "/article/1", "n": 3, "access": {"rate": 1.0}, "complete": {"rate": 1.0}},
        {"route": "/article/2", "n": 3, "access": {"rate": 1.0}, "complete": {"rate": 0.333}},
        {"route": "/article/3", "n": 2, "access": {"rate": 0.0}, "complete": {"rate": None}},
    ],
}


def doc_chars(name):
    return 8100 if name == "strategy.md" else (2990 if name == "arms.md" else 0)


class ComposeTest(unittest.TestCase):
    def compose(self, profile=None, extras=None):
        return brief.compose(
            ADVICE, SUMMARY, profile,
            extras or {"artifacts": {"profile": "profile.yaml", "evidence": "evidence.jsonl"}},
            doc_chars)

    def test_routes_worst_first_and_null_is_worst(self):
        body = self.compose()
        self.assertEqual([r["route"] for r in body["metrics"]["routes"]],
                         ["/article/3", "/article/1", "/article/2"])
        self.assertEqual(body["metrics"]["routes"][0]["complete_rate"], None)

    def test_verdict_is_verbatim_advice(self):
        self.assertEqual(self.compose()["verdict"]["action"], "switch-arm")
        self.assertEqual(self.compose()["verdict"]["confidence"], "assumed")
        self.assertEqual(self.compose()["verdict"]["commands"],
                         ["recon fetch <url> --arms cffi:chrome"])

    def test_evidence_sorted_for_order_independence(self):
        ev = self.compose()["evidence"]
        self.assertEqual(ev["arms"], ["cffi:chrome", "native"])
        self.assertEqual(list(ev["contexts"]),
                         ["a.example|article|challenge", "b.example|list|content"])

    def test_next_reads_follow_action_and_cost(self):
        reads = self.compose()["next_reads"]
        self.assertEqual([r["path"] for r in reads],
                         ["references/strategy.md", "references/arms.md"])
        self.assertEqual([r["chars"] for r in reads], [8100, 2990])

    def test_unknown_action_yields_no_reads(self):
        advice_body = json.loads(json.dumps(ADVICE))
        advice_body["next"]["action"] = "brand-new-action"
        body = brief.compose(advice_body, SUMMARY, None, {"artifacts": {}}, doc_chars)
        self.assertEqual(body["next_reads"], [])

    def test_metrics_overall_shape(self):
        overall = self.compose()["metrics"]["overall"]
        self.assertEqual(overall, {"n": 8, "access_rate": 0.857,
                                   "access_ci": [0.487, 0.974],
                                   "complete_rate": 0.667,
                                   "complete_ci": [0.3, 0.903]})
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m unittest tests.test_brief -v`

Expected: `AttributeError: module 'reconlib.brief' has no attribute 'compose'`。

- [ ] **Step 3: 写最小实现**

在 `scripts/reconlib/brief.py` 末尾追加：

```python
def _worst_first(group):
    access = group["access"]["rate"]
    complete = group["complete"]["rate"]
    return (-1 if access is None else access,
            -1 if complete is None else complete,
            group["route"])


def compose(advice_body, summary, profile, extras, doc_chars):
    """把原子层结果拼成简报信息体；不含 digest / budget / truncated。"""
    raw_evidence = advice_body["evidence"]
    evidence = {
        "n": raw_evidence["n"],
        "noise_excluded_n": raw_evidence["noise_excluded_n"],
        "routes": raw_evidence["routes"],
        "outcomes": dict(raw_evidence["outcomes"]),
        "arms": sorted(raw_evidence.get("arms") or []),
        "contexts": dict(sorted((raw_evidence.get("contexts") or {}).items())),
    }
    for key in ("trials", "policy", "agent"):
        if extras.get(key) is not None:
            evidence[key] = extras[key]

    nxt = advice_body["next"]
    verdict = {
        "action": nxt["action"],
        "layer": nxt["layer"],
        "countermeasure": nxt["countermeasure"],
        "confidence": nxt["confidence"],
        "why": nxt["why"],
        "commands": list(nxt.get("commands") or []),
        "stop": list(nxt.get("stop") or []),
    }

    overall = summary["overall"]
    metrics = {
        "overall": {
            "n": overall["n"],
            "access_rate": overall["access"]["rate"],
            "access_ci": overall["access"]["ci95"],
            "complete_rate": overall["complete"]["rate"],
            "complete_ci": overall["complete"]["ci95"],
        },
        "routes": [
            {"route": group["route"], "n": group["n"],
             "access_rate": group["access"]["rate"],
             "complete_rate": group["complete"]["rate"]}
            for group in sorted(summary["groups"], key=_worst_first)
        ],
    }

    reads = []
    for name, why in DOC_READS.get(verdict["action"], []):
        chars = int(doc_chars(name) or 0)
        if chars:
            reads.append({"path": f"references/{name}", "chars": chars, "why": why})
    return {
        "verdict": verdict,
        "evidence": evidence,
        "metrics": metrics,
        "gates": gates_of(profile),
        "gaps": gaps_of(profile, overall),
        "next_reads": reads[:2],
        "artifacts": dict(extras.get("artifacts") or {}),
    }
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m unittest tests.test_brief -v`

Expected: `OK`（13 个用例）。

- [ ] **Step 5: 提交**

```bash
git add scripts/reconlib/brief.py tests/test_brief.py
git -c user.name=neoLsH -c user.email=43921685+neoLsH@users.noreply.github.com commit -m "feat(brief): 组装 verdict/evidence/metrics/next_reads"
```

---

### Task 3: `digest_of` 与 `finalize`

**Files:**
- Modify: `scripts/reconlib/brief.py`
- Test: `tests/test_brief.py`（追加 `DigestTest`）

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_brief.py`：

```python
class DigestTest(unittest.TestCase):
    def body(self):
        return brief.compose(ADVICE, SUMMARY, {"gates": []},
                             {"artifacts": {"profile": "profile.yaml"}}, doc_chars)

    def test_digest_format_and_stability(self):
        first = brief.digest_of(self.body())
        second = brief.digest_of(self.body())
        self.assertTrue(first.startswith("sha256:"))
        self.assertEqual(len(first), len("sha256:") + 64)
        self.assertEqual(first, second)

    def test_semantics_change_digest(self):
        changed = self.body()
        changed["evidence"]["outcomes"]["ok"] = 3
        self.assertNotEqual(brief.digest_of(self.body()), brief.digest_of(changed))

    def test_budget_does_not_change_digest(self):
        body = self.body()
        small = brief.finalize(body, 700)
        big = brief.finalize(body, 4000)
        self.assertEqual(small["digest"], big["digest"])

    def test_finalize_shape(self):
        out = brief.finalize(self.body(), 4000)
        self.assertEqual(out["schema"], "brief/1")
        self.assertEqual(out["command"], "brief")
        self.assertTrue(out["ok"])
        self.assertEqual(out["budget"]["limit"], 4000)
        self.assertEqual(out["budget"]["dropped"], [])
        self.assertFalse(out["truncated"])
        self.assertGreater(out["budget"]["tokens_est"], 0)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m unittest tests.test_brief.DigestTest -v`

Expected: `AttributeError ... 'digest_of'`。

- [ ] **Step 3: 写最小实现**

追加：

```python
def json_chars(obj):
    """预算口径：JSON 缩进序列化的字符数（与 CLI 实际输出一致）。"""
    return len(json.dumps(obj, ensure_ascii=False, indent=2))


def digest_of(body):
    """对未裁剪信息体取指纹；不含 budget / cache / digest 自身。"""
    payload = json.dumps(body, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"))
    raw = (LOGIC_VERSION + "\n" + payload).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def finalize(body, budget):
    """返回 brief/1 对象：digest + 裁剪后的信息体 + budget + truncated。"""
    digest = digest_of(body)
    view, dropped, truncated = trim(body, budget)
    used = json_chars(view)
    out = {"schema": SCHEMA, "command": "brief", "ok": True, "digest": digest}
    out.update(view)
    out["budget"] = {"limit": budget, "used": used, "dropped": dropped,
                     "tokens_est": estimate_tokens(json.dumps(view, ensure_ascii=False, indent=2))}
    out["truncated"] = truncated
    return out
```

`trim` 在 Task 4 实现；本步先加一个恒等实现让它可导入：

```python
def trim(body, budget):
    view = json.loads(json.dumps(body, ensure_ascii=False))
    return view, [], json_chars(view) > budget
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m unittest tests.test_brief -v`

Expected: `OK`（17 个用例）。

- [ ] **Step 5: 提交**

```bash
git add scripts/reconlib/brief.py tests/test_brief.py
git -c user.name=neoLsH -c user.email=43921685+neoLsH@users.noreply.github.com commit -m "feat(brief): 上下文指纹 digest 与 finalize"
```

---

### Task 4: 预算裁剪

**Files:**
- Modify: `scripts/reconlib/brief.py`（把 Task 3 的恒等 `trim` 换成真实现）
- Test: `tests/test_brief.py`（追加 `TrimTest`）

- [ ] **Step 1: 写失败测试**

追加：

```python
def twelve_route_body():
    groups = [{"route": f"/p{i}", "n": 2,
               "access": {"rate": 0.5}, "complete": {"rate": 0.5}} for i in range(12)]
    groups[0]["access"]["rate"] = None
    summary = {"overall": {"n": 24, "access": {"rate": 0.5, "ci95": [0.3, 0.7]},
                           "complete": {"rate": 0.5, "ci95": [0.3, 0.7]}},
               "groups": groups}
    return brief.compose(ADVICE, summary, None, {"artifacts": {}}, doc_chars)


class TrimTest(unittest.TestCase):
    def test_trim_routes_and_keep_core(self):
        body = twelve_route_body()
        untrimmed = brief.json_chars(body)
        view, dropped, truncated = brief.trim(body, untrimmed - 1)
        self.assertFalse(truncated)
        self.assertTrue(dropped)
        self.assertLessEqual(brief.json_chars(view), untrimmed - 1)
        self.assertEqual(len(view["metrics"]["routes"]), 6)
        self.assertEqual(view["metrics"]["routes"][0]["route"], "/p0")
        self.assertEqual(view["verdict"]["action"], "switch-arm")
        self.assertEqual(view["evidence"]["n"], 8)

    def test_tiny_budget_marks_truncated(self):
        view, dropped, truncated = brief.trim(twelve_route_body(), 200)
        self.assertTrue(truncated)
        self.assertEqual(view["verdict"]["action"], "switch-arm")
        self.assertIn("metrics.routes 省略 6 条", dropped)

    def test_trim_is_deterministic(self):
        body = twelve_route_body()
        first = brief.trim(body, 800)
        second = brief.trim(body, 800)
        self.assertEqual(json.dumps(first, ensure_ascii=False),
                         json.dumps(second, ensure_ascii=False))

    def test_trim_removes_zero_outcomes_and_extra_reads(self):
        view, dropped, _ = brief.trim(twelve_route_body(), 600)
        self.assertNotIn("auth", view["evidence"]["outcomes"])
        self.assertLessEqual(len(view["next_reads"]), 1)
        self.assertTrue(any("outcomes" in item for item in dropped))
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m unittest tests.test_brief.TrimTest -v`

Expected: FAIL，`assertEqual(len(routes), 6)` 得到 12，或 `truncated` 断言失败。

- [ ] **Step 3: 实现真裁剪**

把 Task 3 的恒等 `trim` 整体替换为：

```python
def trim(body, budget):
    """按固定顺序裁剪；核心字段永不裁剪。"""
    view = json.loads(json.dumps(body, ensure_ascii=False))
    dropped = []
    if json_chars(view) <= budget:
        return view, dropped, False

    def cut_routes():
        routes = view["metrics"]["routes"]
        if len(routes) <= 6:
            return False
        cut = len(routes) - 6
        view["metrics"]["routes"] = routes[:3] + routes[-3:]
        dropped.append(f"metrics.routes 省略 {cut} 条")
        return True

    def cut_outcomes():
        outcomes = view["evidence"]["outcomes"]
        zeros = [key for key, value in outcomes.items() if not value]
        if not zeros:
            return False
        for key in zeros:
            outcomes.pop(key)
        dropped.append("evidence.outcomes 省略零值")
        return True

    def cut_verdict():
        changed = False
        for key in ("commands", "stop"):
            if len(view["verdict"][key]) > 1:
                view["verdict"][key] = view["verdict"][key][:1]
                changed = True
        if changed:
            dropped.append("verdict.commands/stop 各留 1 条")
        return changed

    def cut_gaps():
        if len(view["gaps"]) <= 1:
            return False
        view["gaps"] = view["gaps"][:1]
        dropped.append("gaps 留 1 条")
        return True

    def cut_extras():
        changed = False
        for key, keep in (("trials", "n"), ("policy", "contexts"), ("agent", "stop")):
            item = view["evidence"].get(key)
            if not isinstance(item, dict) or len(item) <= 1:
                continue
            view["evidence"][key] = {keep: item.get(keep)}
            changed = True
        if changed:
            dropped.append("evidence.trials/policy/agent 只留关键计数")
        return changed

    def cut_reads():
        if len(view["next_reads"]) <= 1:
            return False
        view["next_reads"] = view["next_reads"][:1]
        dropped.append("next_reads 留 1 条")
        return True

    for step in (cut_routes, cut_outcomes, cut_verdict, cut_gaps, cut_extras, cut_reads):
        step()
        if json_chars(view) <= budget:
            break
    return view, dropped, json_chars(view) > budget
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m unittest tests.test_brief -v`

Expected: `OK`（21 个用例）。

- [ ] **Step 5: 提交**

```bash
git add scripts/reconlib/brief.py tests/test_brief.py
git -c user.name=neoLsH -c user.email=43921685+neoLsH@users.noreply.github.com commit -m "feat(brief): 有界裁剪（固定顺序，核心字段不裁）"
```

---

### Task 5: 文本渲染

**Files:**
- Modify: `scripts/reconlib/brief.py`（追加 `render_text`）
- Test: `tests/test_brief.py`（追加 `RenderTest`）

- [ ] **Step 1: 写失败测试**

```python
class RenderTest(unittest.TestCase):
    def out(self):
        return brief.finalize(twelve_route_body(), 4000)

    def test_text_contains_decisions(self):
        text = brief.render_text(self.out())
        self.assertIn("switch-arm", text)
        self.assertIn("L2 / S2", text)
        self.assertIn("预算:", text)
        self.assertIn("references/strategy.md", text)

    def test_cache_hit_renders_short(self):
        text = brief.render_text({"schema": "brief/1", "command": "brief",
                                  "ok": True, "digest": "sha256:abc",
                                  "cache": {"hit": True}})
        self.assertIn("命中", text)
        self.assertNotIn("switch-arm", text)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m unittest tests.test_brief.RenderTest -v`

Expected: `AttributeError ... 'render_text'`。

- [ ] **Step 3: 实现**

```python
def _fmt(value):
    return "n/a" if value is None else f"{value:.3f}"


def _ci(pair):
    if not pair or pair[0] is None:
        return "ci n/a"
    return f"ci {pair[0]:.3f}-{pair[1]:.3f}"


def render_text(out):
    if out.get("cache"):
        return f"digest {out['digest']}\n复用: 命中，本轮无需重读"
    verdict = out["verdict"]
    evidence = out["evidence"]
    overall = out["metrics"]["overall"]
    lines = [f"digest {out['digest']}"]
    lines.append(f"下一步: {verdict['action']}（{verdict['layer']} / "
                 f"{verdict['countermeasure']}，置信度 {verdict['confidence']}）")
    lines.append(f"为什么: {verdict['why']}")
    lines.append(f"证据: {evidence['n']} 条，可归因 {evidence['noise_excluded_n']}；"
                 + " ".join(f"{key}={value}" for key, value in evidence["outcomes"].items() if value))
    lines.append(f"指标: 可访问 {_fmt(overall['access_rate'])} {_ci(overall['access_ci'])}，"
                 f"完整 {_fmt(overall['complete_rate'])} {_ci(overall['complete_ci'])}")
    for route in out["metrics"]["routes"][:6]:
        lines.append(f"  {route['route']}: 可访问 {_fmt(route['access_rate'])} "
                     f"完整 {_fmt(route['complete_rate'])}")
    first = out["gates"]["first_fail"]
    gate = f"{first['name']}（{first['layer']}）" if first else "无失败门"
    lines.append(f"闸门: 最早失败 {gate}；pass={out['gates']['pass']} "
                 f"fail={out['gates']['fail']} unknown={out['gates']['unknown']}")
    if out["gaps"]:
        lines.append("缺口: " + "；".join(out["gaps"]))
    if out["next_reads"]:
        lines.append("先读:")
        for doc in out["next_reads"]:
            lines.append(f"  {doc['path']}（{doc['chars']} 字符）— {doc['why']}")
    if out["artifacts"]:
        lines.append("产物: " + ", ".join(out["artifacts"].values()))
    budget = out["budget"]
    tail = "；已截断" if out["truncated"] else ""
    lines.append(f"预算: {budget['used']}/{budget['limit']} 字符"
                 f"（约 {budget['tokens_est']} tokens，estimate）{tail}")
    return "\n".join(lines)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m unittest tests.test_brief -v`

Expected: `OK`（23 个用例）。

- [ ] **Step 5: 提交**

```bash
git add scripts/reconlib/brief.py tests/test_brief.py
git -c user.name=neoLsH -c user.email=43921685+neoLsH@users.noreply.github.com commit -m "feat(brief): 人读渲染（含命中短路）"
```

---

### Task 6: CLI `scripts/brief.py` 与 `recon.py` 接线

**Files:**
- Create: `scripts/brief.py`
- Modify: `scripts/recon.py`（`TOOLS`、`to_json`、模块 docstring）
- Test: `tests/test_brief.py`（追加 `BriefCliTest`）

- [ ] **Step 1: 写失败测试**

```python
def copy_example():
    d = tempfile.mkdtemp(prefix="recon-brief-")
    for name in ("profile.yaml", "evidence.jsonl", "trials.jsonl"):
        src = os.path.join(EXAMPLE_DIR, name)
        if os.path.isfile(src):
            shutil.copy(src, os.path.join(d, name))
    return d


class BriefCliTest(unittest.TestCase):
    def workspace(self, files=None):
        d = copy_example() if files is None else make(files)
        self.addCleanup(shutil.rmtree, d, True)
        return d

    def test_empty_workspace(self):
        r, data = support.run_json("brief", self.workspace({}), "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(data["verdict"]["action"], "collect-more")
        self.assertFalse(data["truncated"])
        self.assertTrue(data["digest"].startswith("sha256:"))
        self.assertIn("缺 profile.yaml：没有闸门图与口径", data["gaps"])

    def test_example_matches_stats_and_routing(self):
        ws = self.workspace()
        r, data = support.run_json("brief", ws, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        _, stats_body = support.run_json(
            "stats", os.path.join(ws, "evidence.jsonl"), "--json")
        self.assertEqual(data["metrics"]["overall"]["access_rate"],
                         stats_body["overall"]["access"]["rate"])
        self.assertEqual(data["metrics"]["overall"]["complete_rate"],
                         stats_body["overall"]["complete"]["rate"])
        self.assertEqual(data["next_reads"][0]["path"], "references/evidence-matrix.md")
        self.assertEqual(data["artifacts"]["profile"], "profile.yaml")

    def test_byte_stable_across_runs(self):
        ws = self.workspace()
        first = support.run("brief", ws, "--json")
        second = support.run("brief", ws, "--json")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, second.stdout)

    def test_row_order_irrelevant(self):
        ws = self.workspace()
        with open(os.path.join(EXAMPLE_DIR, "evidence.jsonl"), encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
        with open(os.path.join(EXAMPLE_DIR, "profile.yaml"), encoding="utf-8") as f:
            profile_text = f.read()
        with open(os.path.join(EXAMPLE_DIR, "trials.jsonl"), encoding="utf-8") as f:
            trials_text = f.read()
        reversed_ws = self.workspace({
            "profile.yaml": profile_text,
            "trials.jsonl": trials_text,
            "evidence.jsonl": "".join(json.dumps(row, ensure_ascii=False) + "\n"
                                      for row in reversed(rows)),
        })
        _, a = support.run_json("brief", ws, "--json")
        _, b = support.run_json("brief", reversed_ws, "--json")
        self.assertEqual(a["digest"], b["digest"])
        self.assertEqual(a["verdict"], b["verdict"])
        self.assertEqual(a["metrics"], b["metrics"])

    def test_semantic_change_changes_digest(self):
        ws = self.workspace()
        _, before = support.run_json("brief", ws, "--json")
        path = os.path.join(ws, "evidence.jsonl")
        with open(path, encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        for row in lines:
            if row.get("outcome") == "ok":
                row["outcome"] = "blocked"
                break
        with open(path, "w", encoding="utf-8") as f:
            f.write("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in lines))
        _, after = support.run_json("brief", ws, "--json")
        self.assertNotEqual(before["digest"], after["digest"])

    def test_volatile_field_ignored(self):
        ws = self.workspace()
        _, before = support.run_json("brief", ws, "--json")
        path = os.path.join(ws, "evidence.jsonl")
        with open(path, encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        lines[0]["elapsed_ms"] = 123456
        with open(path, "w", encoding="utf-8") as f:
            f.write("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in lines))
        _, after = support.run_json("brief", ws, "--json")
        self.assertEqual(before["digest"], after["digest"])

    def test_budget_trimming(self):
        ws = self.workspace()
        _, full = support.run_json("brief", ws, "--json", "--budget", "4000")
        used = full["budget"]["used"]
        _, tight = support.run_json("brief", ws, "--json", "--budget", str(used - 1))
        self.assertFalse(tight["truncated"])
        self.assertLessEqual(tight["budget"]["used"], used - 1)
        self.assertTrue(tight["budget"]["dropped"])
        self.assertEqual(full["digest"], tight["digest"])

    def test_tiny_budget_truncated(self):
        _, data = support.run_json("brief", self.workspace(), "--json", "--budget", "200")
        self.assertTrue(data["truncated"])
        self.assertEqual(data["verdict"]["action"], "collect-more")

    def test_expect_digest_hit(self):
        ws = self.workspace()
        _, full = support.run_json("brief", ws, "--json")
        r, hit = support.run_json("brief", ws, "--json",
                                  "--expect-digest", full["digest"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(hit["cache"]["hit"])
        self.assertNotIn("verdict", hit)
        self.assertLessEqual(len(r.stdout), 200)

    def test_expect_digest_miss(self):
        _, data = support.run_json("brief", self.workspace(), "--json",
                                   "--expect-digest", "sha256:" + "0" * 64)
        self.assertFalse(data["cache"]["hit"])
        self.assertIn("verdict", data)

    def test_bad_inputs(self):
        r = support.run("brief", "/nonexistent-workspace", "--json")
        self.assertEqual(r.returncode, 2)
        ws = self.workspace({"evidence.jsonl": json.dumps(
            {"kind": "trial", "trial_outcome": "ok"}) + "\n"})
        self.assertEqual(support.run("brief", ws, "--json").returncode, 2)
        self.assertEqual(support.run("brief", self.workspace({}), "--budget", "199").returncode, 2)

    def test_profile_without_gates(self):
        with open(os.path.join(EXAMPLE_DIR, "evidence.jsonl"), encoding="utf-8") as f:
            evidence_text = f.read()
        ws = self.workspace({"profile.yaml": "host: example.test\nconfidence: observed\n",
                             "evidence.jsonl": evidence_text})
        r, data = support.run_json("brief", ws, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNone(data["gates"]["first_fail"])
        self.assertEqual(data["gates"]["unknown"], 0)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m unittest tests.test_brief.BriefCliTest -v`

Expected: 全部失败，`未知命令: brief` 或 `AttributeError`。

- [ ] **Step 3: 写 CLI**

新建 `scripts/brief.py`：

```python
#!/usr/bin/env python3
"""brief.py — 把 recon 工作区收敛成有界、可复用的 Agent 上下文包。

只读：不联网、不发请求、不写盘。复用 stats / advise 的原子，不重写计数。
"""

import argparse
import json
import os
import sys

from reconlib import advice, brief, evidence
import stats
from report import load_profile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find(workspace, name):
    path = os.path.join(workspace, name)
    return path if os.path.isfile(path) else None


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def doc_chars(name):
    path = os.path.join(ROOT, "references", name)
    try:
        with open(path, encoding="utf-8") as f:
            return len(f.read())
    except OSError:
        return 0


def main():
    ap = argparse.ArgumentParser(description="工作区 → 有界、可复用的 Agent 上下文包（只读）")
    ap.add_argument("workspace", help="recon/<代号>/ 目录")
    ap.add_argument("--budget", type=int, default=brief.DEFAULT_BUDGET,
                    help=f"JSON 输出字符上限（默认 {brief.DEFAULT_BUDGET}，最小 {brief.MIN_BUDGET}）")
    ap.add_argument("--expect-digest", help="上轮简报的 digest；命中时只回 digest")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    if args.budget < brief.MIN_BUDGET:
        ap.error(f"--budget 不能小于 {brief.MIN_BUDGET}")
    if not os.path.isdir(args.workspace):
        ap.error(f"工作区不存在: {args.workspace}")

    paths = {name: find(args.workspace, f"{name}.{ext}")
             for name, ext in (("profile", "yaml"), ("evidence", "jsonl"),
                               ("trials", "jsonl"), ("policy", "json"),
                               ("agent", "json"))}

    profile = None
    if paths["profile"]:
        try:
            profile = load_profile(paths["profile"])
        except SystemExit:
            ap.error("profile.yaml 解析失败")
        if not isinstance(profile, dict):
            ap.error("profile.yaml 顶层不是对象")

    rows, stats_rows = [], []
    if paths["evidence"]:
        try:
            rows = evidence.read_jsonl(paths["evidence"])
            stats_rows = stats.read_rows([paths["evidence"]])
        except (OSError, ValueError) as e:
            ap.error(str(e))
    rows.sort(key=brief.order_key)

    try:
        advice_body = advice.recommend(rows)
        summary = stats.summarize(stats_rows)
    except ValueError as e:
        ap.error(str(e))

    extras = {"artifacts": {key: os.path.basename(path)
                            for key, path in paths.items() if path}}
    if paths["trials"]:
        try:
            extras["trials"] = {"n": len(evidence.read_jsonl(paths["trials"]))}
        except (OSError, ValueError) as e:
            ap.error(str(e))
    if paths["policy"]:
        try:
            body = load_json(paths["policy"])
        except (OSError, ValueError) as e:
            ap.error(f"policy.json 解析失败: {e}")
        contexts = (body.get("meta") or {}).get("contexts") if isinstance(body, dict) else None
        extras["policy"] = {
            "contexts": contexts if isinstance(contexts, int) else len(body.get("state") or {}),
            "arms": len(body.get("arms") or []),
        }
    if paths["agent"]:
        try:
            body = load_json(paths["agent"])
        except (OSError, ValueError) as e:
            ap.error(f"agent.json 解析失败: {e}")
        if isinstance(body, dict):
            stop = body.get("stop")
            extras["agent"] = {
                "stop": stop.get("kind") if isinstance(stop, dict) else stop,
                "rounds": len(body.get("rounds") or []),
            }

    body = brief.compose(advice_body, summary, profile, extras, doc_chars)

    if args.expect_digest:
        digest = brief.digest_of(body)
        if digest == args.expect_digest:
            hit = {"schema": brief.SCHEMA, "command": "brief", "ok": True,
                   "digest": digest, "cache": {"hit": True}}
            print(json.dumps(hit, ensure_ascii=False) if args.json else brief.render_text(hit))
            return 0

    out = brief.finalize(body, args.budget)
    if args.expect_digest:
        out["cache"] = {"hit": False}
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(brief.render_text(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

修改 `scripts/recon.py`：

1. `TOOLS` 字典中 `"advise"` 之后加一行：

```python
    "brief": ("python", "brief.py", "工作区 → 有界、可复用的 Agent 上下文包"),
```

2. `to_json()` 的单对象命令元组里加 `"brief"`：

```python
    elif command in ("surface", "shape", "canary", "stats", "plan", "crawl",
                     "bandit", "explore", "advise", "agent", "brief"):
```

3. 模块 docstring 的子命令映射表加一行：

```
    advise → advise.py       brief → brief.py         agent → agent.py
```

- [ ] **Step 4: 跑单测与入口**

Run:

```bash
python3 -m unittest tests.test_brief -v
python3 scripts/recon.py brief assets/example --json | head -20
```

Expected: 单测 `OK`（35 个用例）；命令输出含 `"schema": "brief/1"`。

- [ ] **Step 5: 提交**

```bash
git add scripts/brief.py scripts/recon.py tests/test_brief.py
git -c user.name=neoLsH -c user.email=43921685+neoLsH@users.noreply.github.com commit -m "feat(brief): CLI 入口与 recon 接线"
```

---

### Task 7: self-check 冒烟门禁

**Files:**
- Modify: `scripts/self-check.py`（新增 `check_brief_smoke`，挂进 `main()`）

- [ ] **Step 1: 写门禁（先看它失败）**

在 `check_advice_smoke()` 附近新增：

```python
def check_brief_smoke():
    """brief 的门禁：预算内、字节稳定、digest 可复用。"""
    example = os.path.join(ROOT, "assets/example")
    cmd = [sys.executable, os.path.join(ROOT, "scripts/recon.py"), "brief", example, "--json"]
    first = subprocess.run(cmd, capture_output=True, text=True)
    if first.returncode != 0:
        fail(f"brief 入口异常: {(first.stderr or first.stdout).strip()[:200]}")
        return
    try:
        body = json.loads(first.stdout)
    except ValueError:
        fail("brief --json 不是合法 JSON")
        return
    used = (body.get("budget") or {}).get("used")
    if body.get("schema") != "brief/1" or not str(body.get("digest", "")).startswith("sha256:"):
        fail(f"brief 契约字段异常: schema={body.get('schema')!r}")
        return
    if used is None or used > (body.get("budget") or {}).get("limit", 0):
        fail(f"brief 超出预算: used={used} limit={(body.get('budget') or {}).get('limit')}")
        return
    if len(body["digest"]) != len("sha256:") + 64:
        fail(f"brief digest 长度异常: {body['digest']!r}")
        return
    second = subprocess.run(cmd, capture_output=True, text=True)
    if second.stdout != first.stdout:
        fail("brief 两次运行输出不一致（字节稳定性被破坏）")
        return
    hit = subprocess.run(cmd + ["--expect-digest", body["digest"]],
                         capture_output=True, text=True)
    try:
        hit_body = json.loads(hit.stdout)
    except ValueError:
        fail("brief --expect-digest 输出不是合法 JSON")
        return
    if not (hit_body.get("cache") or {}).get("hit") or "verdict" in hit_body:
        fail("brief --expect-digest 未短路")
        return
    ok(f"brief 有界且可复用（used={used}，digest 稳定）")
```

在 `main()` 的调用列表里，`check_advice_smoke()` 后加：

```python
    check_brief_smoke()
```

- [ ] **Step 2: 确认新门禁被调用**

Run: `python3 scripts/self-check.py | grep brief`

Expected: 输出含 `brief 有界且可复用`；若出现 `FAIL brief`，按提示修到通过再进下一步。

- [ ] **Step 3: 跑全量自检**

Run: `python3 scripts/self-check.py`

Expected: 末尾 `0 failed`（或脚本既有成功汇总），且输出包含 `brief 有界且可复用`。

- [ ] **Step 4: 提交**

```bash
git add scripts/self-check.py
git -c user.name=neoLsH -c user.email=43921685+neoLsH@users.noreply.github.com commit -m "chore(self-check): brief 预算与复用冒烟门禁"
```

---

### Task 8: 文档与版本同步到 1.23.0

**Files:**
- Modify: `SKILL.md`、`AGENTS.md`、`references/cli.md`、`references/agent.md`、`references/forms.md`、`assets/workspace/README.md`、`README.md`、`CHANGELOG.md`
- Modify: `docs/superpowers/specs/2026-11-15-context-budget-design.md`（`used` 口径措辞）

- [ ] **Step 1: `SKILL.md`（必须仍是 ≤150 行）**

1. `metadata.version: 1.22.0` → `1.23.0`。
2. 「按需加载」表：把现有两行合并为一行（省 1 行）：

```markdown
| 跑默认探索 Agent，或把证据行接给 LLM / Agent | [references/agent.md](../../../references/agent.md) |
```

替换原来的「跑默认探索 Agent…」与「接 Agent / LLM…」两行。
3. 同一张表新增一行：

```markdown
| 想知道这一轮该读多少、和上一轮比变没变 | `recon brief`：有界、可复用的上下文包（[references/agent.md](../../../references/agent.md)） |
```

4. 「常用脚本」代码块新增一行（放在 `recon advise` 之后）：

```bash
recon brief recon/<代号> --budget 1200          # 有界上下文包；--expect-digest 命中即复用
```

- [ ] **Step 2: `AGENTS.md`**

「脚本」代码块 `recon advise` 后加：

```bash
scripts/recon.py brief recon/<代号> --json      # 有界上下文包（brief/1），可带 --expect-digest 复用
```

「按需读取」列表 `advise` 那条之前加：

```markdown
- 上下文预算：工作区 → 有界、可复用的简报与复用循环：[references/agent.md](../../../references/agent.md)
```

- [ ] **Step 3: `references/cli.md`**

命令总表 `advise` 行后加：

```markdown
| `brief` | `brief.py` | 工作区 → 有界、字节稳定的 Agent 上下文包（`brief/1`） | `--budget` `--expect-digest` `--json` |
```

在「通用约定」之后新增一节：

```markdown
## 上下文包 brief

```bash
recon brief recon/<代号>                      # 人读
recon brief recon/<代号> --json               # 机器读，单对象
recon brief recon/<代号> --budget 1200        # 收紧预算
recon brief recon/<代号> --expect-digest sha256:…   # 命中只回 digest
```

只读：不联网、不发请求、不写盘；`verdict` / `evidence` / `metrics` 分别复用 `advise` 与 `stats` 的结果，不产生第二份口径。

- `--budget` 按 JSON 缩进输出的字符数计（不含 `budget` 节），默认 2000、最小 200。
- 裁剪顺序：`metrics.routes` 留最差/最好各 3 → `outcomes` 删零值 → `verdict.commands` / `stop` 各留 1 → `gaps` 留 1 → `trials` / `policy` / `agent` 只留关键计数 → `next_reads` 留 1。核心字段永不裁剪；仍超预算时 `truncated: true`。
- 同一工作区两次运行输出逐字节一致；证据行顺序不影响输出（CLI 先按规范化行内容排序）。
- `--expect-digest` 命中时只输出 `digest` 与 `cache.hit`（单行，≤200 字符）。
- 退出码：`0` 正常（含截断与命中）；`2` 输入错误。
```

- [ ] **Step 4: `references/agent.md`**

在「契约 `advise/1`」之后新增：

```markdown
## 上下文预算 `brief/1`

`recon brief <工作区>` 把 profile / evidence / trials / policy / agent.json 收敛成一个有界包，字段：`digest` / `verdict` / `evidence` / `metrics` / `gates` / `gaps` / `next_reads` / `artifacts` / `budget` / `truncated`。

- `verdict` / `evidence` 复用 `advise/1`，`metrics` 复用 `stats`，`gaps` / `gates` 从档案派生；不重写计数。
- `digest` 覆盖未裁剪的信息体（含 `brief` 逻辑版本），**不受 `--budget` 与证据行顺序影响**；语义字段变化必然改变 digest。
- 复用循环：下一轮先带 `--expect-digest <上轮>`；`cache.hit=true` 时只回 digest，不必重读工作区。这是调用方持有的判据，仓库不落任何缓存状态。
- 输出无时间戳、无绝对路径、无随机序，可作为 prompt cache 的稳定前缀。
- `next_reads` 按 `verdict.action` 只给 ≤2 份必需文档并附实测字符数；未知动作退化为空列表，不猜。
```

- [ ] **Step 5: `references/forms.md`、`assets/workspace/README.md`**

`forms.md` 的分工表补一行（放在 Agent 形态相关行之后）：

```markdown
| 上下文预算 | `brief`：工作区 → 有界、可复用的包 | `brief/1`；digest 复用 |
```

`assets/workspace/README.md` 的「怎么用」代码块末尾加：

```bash
scripts/recon.py brief recon/<代号> --json     # 有界上下文包；下一轮用 --expect-digest 复用
```

- [ ] **Step 6: `README.md` 与 `CHANGELOG.md`**

`README.md`：

1. 「CLI 入口」表 `advise` 行后加：

```markdown
| `brief` | `brief.py` | 工作区 → 有界、字节稳定、可复用的 Agent 上下文包（`brief/1`） |
```

2. 「目录结构」若有 `advise.py` 一行，其后加 `brief.py`；没有则跳过。
3. 「质量校验」段落末尾追加一句：`**上下文预算**（`brief` 预算内、两次运行字节一致、digest 复用短路、证据行序无关）。`
4. 「状态」段落开头插入 1.23.0 条目（见下），保留旧内容。

`CHANGELOG.md` 在 `## [1.22.0]` 之前插入：

```markdown
## [1.23.0] — 2026-11-15

给 Agent 一个**有界、可复用**的上下文包：新增只读命令 `recon brief <工作区>`，把 profile / evidence / trials / policy / agent.json 收敛成 `brief/1`（`verdict` / `evidence` / `metrics` / `gates` / `gaps` / `next_reads` / `artifacts` / `budget`）。

### 新增

- `scripts/reconlib/brief.py`：`compose` / `digest_of` / `trim` / `finalize` / `estimate_tokens` / `render_text`，纯函数、无 IO；`verdict` 与 `evidence` 复用 `advice`，`metrics` 复用 `stats`，不重写计数。
- `scripts/brief.py` 与 `recon brief` 子命令：文件发现、参数解析、输出；`--budget`（默认 2000、最小 200）、`--expect-digest`、`--json`。
- 上下文指纹 `digest`：覆盖未裁剪信息体与 brief 逻辑版本，不受预算与证据行顺序影响；`--expect-digest` 命中时只回单行 digest（≤200 字符）。
- `next_reads`：按 `advise` 动作只给 ≤2 份必需文档，字符数运行时实测。
- `tests/test_brief.py`（35 个）与自检 `brief` 冒烟门禁。

### 变更

- `SKILL.md` 版本升到 1.23.0；`AGENTS.md`、`references/cli.md`、`references/agent.md`、`references/forms.md`、`assets/workspace/README.md`、`README.md` 同步。

### 已知限制

- `budget.used` 不含 `budget` 节，且 `tokens_est` 是启发式估算（CJK 1 token/字，其余 3.5 字符/token），只作量级参考，验收以字符数为准。
- `gates.first_fail` 取档案书写顺序里的第一个 `fail`（模板按层序书写），不重新按 `layer` 排序。
- 只发现约定的五个文件名；单轮 `r<N>-evidence.jsonl` 不参与。
```

- [ ] **Step 7: 修 spec 的 `used` 口径措辞**

把 `docs/superpowers/specs/2026-11-15-context-budget-design.md` 里：

```markdown
| `budget` | `used` 按 JSON 序列化字符数；`tokens_est` 为估算 |
```

改成：

```markdown
| `budget` | `used` 为**不含 `budget` 节**的 JSON 缩进输出字符数（避免自引用）；`tokens_est` 为估算 |
```

- [ ] **Step 8: 跑门禁**

Run:

```bash
python3 scripts/self-check.py
python3 -m unittest discover -s tests
```

Expected: self-check 无 `FAIL`；unittest `OK`（既有用例 + 新增 29 个全通过）。

- [ ] **Step 9: 提交**

```bash
git add SKILL.md AGENTS.md references/cli.md references/agent.md references/forms.md \
        assets/workspace/README.md README.md CHANGELOG.md \
        docs/superpowers/specs/2026-11-15-context-budget-design.md
git -c user.name=neoLsH -c user.email=43921685+neoLsH@users.noreply.github.com commit -m "docs: brief 契约与 1.23.0 版本同步"
```

---

### Task 9: 体积对照与总验收

**Files:**
- Modify: `CHANGELOG.md`（把实测数字补进 1.23.0 条目）

- [ ] **Step 1: 量改造前后**

Run:

```bash
cd /Users/neolshuang/project/site-accessibility-recon
python3 - <<'PY'
import json, os, subprocess, sys
root = os.getcwd()
ex = os.path.join(root, "assets", "example")
def chars(p):
    with open(p, encoding="utf-8") as f:
        return len(f.read())
raw = sum(chars(os.path.join(ex, n)) for n in ("profile.yaml", "evidence.jsonl")
          if os.path.exists(os.path.join(ex, n)))
stats_out = subprocess.run([sys.executable, "scripts/recon.py", "stats",
                            os.path.join(ex, "evidence.jsonl"), "--json"],
                           capture_output=True, text=True).stdout
advise_out = subprocess.run([sys.executable, "scripts/recon.py", "advise",
                             os.path.join(ex, "evidence.jsonl"), "--json"],
                            capture_output=True, text=True).stdout
brief_out = subprocess.run([sys.executable, "scripts/recon.py", "brief", ex, "--json"],
                           capture_output=True, text=True).stdout
before = raw + len(stats_out) + len(advise_out)
print("before chars:", before)
print("brief json chars:", len(brief_out))
print("reduction:", f"{(1 - len(brief_out) / before) * 100:.1f}%")
body = json.loads(brief_out)
print("used:", body["budget"]["used"], "tokens_est:", body["budget"]["tokens_est"])
PY
```

Expected: `before chars` ≈ 5 100（示例工作区），`reduction` ≥ 60%；`used` ≤ 2000。

- [ ] **Step 2: 把实测值写进 CHANGELOG**

在 1.23.0 条目的「新增」末尾加一行：

```markdown
- 示例工作区实测：`profile.yaml + evidence.jsonl + stats --json + advise --json` 共 <before> 字符 → `brief --json` <brief> 字符（降幅 <pct>%，`used` <used> 字符，`tokens_est` <est>）。
```

（把 `<before>` / `<brief>` / `<pct>` / `<used>` / `<est>` 换成 Step 1 的真实输出，不要留占位符。）

- [ ] **Step 3: 全量门禁**

Run:

```bash
python3 scripts/self-check.py
python3 -m unittest discover -s tests -v 2>&1 | tail -5
```

Expected: self-check 无 `FAIL`；unittest 末尾 `OK`。

- [ ] **Step 4: 提交**

```bash
git add CHANGELOG.md
git -c user.name=neoLsH -c user.email=43921685+neoLsH@users.noreply.github.com commit -m "docs(changelog): brief 体积对照实测"
```

---

## 交付

本地全绿后（由用户已授权）：

```bash
git push -u origin feat/context-budget
gh pr create --repo neoLsH/site-accessibility-recon \
  --base main --head feat/context-budget \
  --title "feat: recon brief —— 有界、可复用的 Agent 上下文包" \
  --body "$(cat <<'EOF'
## 摘要

新增只读命令 `recon brief <工作区>`：把 profile / evidence / trials / policy / agent.json 收敛成 `brief/1` 有界上下文包，复用 `advise` / `stats` 的原子层，不重写计数。

- 有界：`--budget` 按 JSON 字符裁剪，报告 `used` / `dropped` / `truncated`
- 可复用：`digest` 覆盖未裁剪信息体，`--expect-digest` 命中只回单行
- 稳定：无时间戳 / 绝对路径 / 随机序；证据行序与耗时字段不影响输出
- 省上下文：示例工作区原料 → brief 的实测降幅见 CHANGELOG 1.23.0

## 验证

- `python3 scripts/self-check.py` 无 FAIL
- `python3 -m unittest discover -s tests` 全绿（新增 35 个 `tests/test_brief.py` 用例）
- 全程本地示例站，不触外网

## 范围

只读命令；不改既有调用路径与输出契约；无新增依赖。
EOF
)"
gh pr checks --watch
gh pr merge --repo neoLsH/site-accessibility-recon --merge   # 不提交 review：YOLO 尝试
gh pr view --repo neoLsH/site-accessibility-recon --json mergedBy,reviews,state
```

合并后复核 `mergedBy=neoLsH`、`reviews=[]`；24–48h 后查 `https://github.com/neoLsH?tab=achievements` 是否出现 YOLO。若仍未触发，退路是让 Neo-driod 从 fork 开 PR、由 neoLsH 合并。
