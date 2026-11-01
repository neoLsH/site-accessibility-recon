# Agent 形态与 LLM 接口：advise

Skill 给人读，CLI 给脚本调，Agent 需要一个**稳定、可校验、不替你动手**的机器契约。`recon advise` 就是这个交接点：输入证据行，输出下一步动作，不联网、不发请求、不执行建议里的命令。

## 循环

```
采集（fetch / crawl）──► 证据行 ──► 结算（stats）──► 建议（advise）──► 人 / Agent 执行 ──┐
        ▲                                                                                 │
        └───────────────────────────── 新证据行回写 ◄──────────────────────────────────────┘
```

`advise` 只负责“下一步做什么、为什么、什么时候停”。执行仍由调用方承担，并在结束后产出新证据行；没有新证据就不算闭环。

## 契约 `advise/1`

```bash
recon advise recon/<代号>/evidence.jsonl            # 人读
recon advise recon/<代号>/evidence.jsonl --json     # 机器读
recon advise recon/<代号>/evidence.jsonl --route /article/1 --json
```

```json
{
  "schema": "advise/1",
  "command": "advise",
  "ok": true,
  "evidence": {
    "n": 8, "routes": 3,
    "outcomes": {"ok": 4, "degraded": 2, "blocked": 1, "auth": 0,
                 "missing": 0, "noise": 1},
    "noise_excluded_n": 7,
    "arms": ["native", "cffi:chrome"],
    "contexts": ["example.com|article|challenge", "example.com|list|content"]
  },
  "findings": [{"code": "degraded", "count": 2, "message": "拿到响应但内容不足"}],
  "next": {
    "action": "collect-more",
    "layer": "L7", "countermeasure": "S0",
    "why": "最大一类只占 29%，结局分散，先补样本再归因",
    "confidence": "observed",
    "commands": ["recon canary <url> --passes 5", "..."],
    "stop": ["样本达到至少 3 pass 或 20 页后再看主导结局"]
  }
}
```

| 字段 | 说明 |
|---|---|
| `evidence` | 计数快照；`n` 是总行数，`noise_excluded_n` 是可归因行数，噪声不进分母；`contexts` 是出现过的 `host|路由类|响应类` |
| `findings` | 观察到的结局，按 auth / missing / blocked / degraded / noise 排列，只报计数；`context-split` 表示同一 host 的不同上下文结局相反 |
| `next` | 唯一一个动作，绑定层、S 级、理由、置信度、可选命令与停止条件 |
| `route` | 仅当用 `--route` 过滤时出现 |
| `ok: false` | 不出现；输入错误走退出码 2 |

退出码：`0` 有建议；`2` 输入错误（读不了文件、路由不存在、把试验行当证据行）。试验行（`kind: trial`）永远不能喂进来——它和证据行口径不同，混用会污染归因。

## 动作表

| `action` | 触发条件 | 层 / S 级 | 置信度 |
|---|---|---|---|
| `collect-more` | 没有证据，或最大一类占比 < 50% | L7 / S0 | observed |
| `separate-noise` | 可归因行为 0（全是链路 / 5xx） | L1 / S0 | observed |
| `check-authorization` | 多数失败是鉴权要求 | L5 / S7 | observed |
| `check-routing` | 多数失败是内容 / 路由不存在 | L0 / S0 | observed |
| `check-extraction` | 多数失败是响应可达但内容不足 | L0 / S0 | observed |
| `backoff` | 被拦样本里过半是限频信号 | L6 / S4 | observed |
| `switch-arm` | 有臂成功、有臂被拦，差异可归因 | L2 / S2 | assumed |
| `differentiate` | 被拦但尚无臂间差异 | L6 / S1 | assumed |
| `maintain` | 可归因行全部拿到内容 | L7 / S0 | observed |

优先级是**先分诊**：业务状态（auth / missing）先于反爬；反爬里先分限频，再看换臂；噪声单列，不参与归因。`switch-arm` 与 `differentiate` 是 `assumed`——它们描述“最便宜的候选”，不是已验证的因果。证据里出现 `context-split`（同一 host 下 `ok` 与 `blocked` 落在不同上下文）时，先按上下文分组再换臂，否则会把“某类页面本来就能取”读成“换臂有效”。

## 接 LLM 的方式

把 `advise --json` 的输出当作**事实层**，LLM 只做解释与编排，不得改写计数。一个可用的提示骨架：

```
你是可访问性诊断助手。规则：
1. 只依据输入的 advise/1 JSON 说话，不得新增计数、路由或臂名。
2. 先复述 evidence.n 与 noise_excluded_n，再给出 next.action 与它的层。
3. confidence=assumed 时，必须说明这是假设，并写出验证方式（next.commands）。
4. 命令不自动执行；先列出命令与 stop，等人工确认。
5. 不提供验证码求解、账号池、凭证填充、出口轮换或鉴权绕过。
若用户要的动作与 next 冲突，先指出冲突与证据，再让用户选。
```

LLM 的价值在于把 `why` 讲清楚、把多条路由的建议排序、把结果写进档案；不在于替规则层猜结局。

## 护栏

- **只读**：`advise.py` 不导入 `fetch` / `robots`，不调用 `subprocess`，建议命令只是字符串。
- **不猜**：没有证据就 `collect-more`，散点就 `collect-more`，不把小样本读成方向。
- **有停止条件**：每个动作都带 `stop`，Agent 到达条件必须停，不能无限试错。
- **不扩大授权**：`check-authorization` 只建议使用站点方授权的凭证或官方接口，不提供获取凭证的手段。
- **不改红线**：[SKILL.md](../SKILL.md) 的红线与 [crawl.md](crawl.md) 的护栏不因 Agent 调用而放宽。

## 边界

`advise` 是规则层，不是策略执行器：它不选臂、不发请求、不写证据、不修改档案。需要选臂用 `crawl` / `quick`，需要离线比较策略用 `bandit`，需要把后验变成下一批探测用 `explore`（只读），需要结算用 `stats`；想把这一轮学到的后验留给下一轮，用 `--policy-out` / `--policy-in`（见 [strategy.md](strategy.md)）。换臂或补样本时，`next.commands` 会给出 `--trial-out` 与 `recon explore --trials ...` 的组合；Agent 应先出计划、再执行 `crawl`。规则只覆盖本仓库已有的结局词表；新增结局要先扩 `evidence` / `stats` 词表，再扩动作表，并补测试。
