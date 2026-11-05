# Agent 形态与 LLM 接口：advise

Skill 给人读，CLI 给脚本调，Agent 需要一个**稳定、可校验、不替你动手**的机器契约。这里有两层：`recon agent` 是仓库自带的**有界探索循环**，负责把站点分析、路径发现与配额执行串起来；`recon advise` 是循环末端的**只读决策契约**，输入证据行，输出下一步动作，不联网、不发请求、不执行建议里的命令。

## 默认 Agent：`recon agent`

一条命令跑完“分析 → 探索 → 计划 → 执行 → 收敛”，不是常驻调度器：轮数、页面、请求都有上限，跑完就退出并给出停止原因。

```
站点分析（每 host 1 请求，robots 先行）→ crawl 第 1 轮 → 同站链接发现
  → explore 计划 + frontier 增补 → crawl --plan → advise 收敛 → 停止原因
```

| 阶段 | 做什么 | 护栏 |
|---|---|---|
| 站点分析 | 每个 host 取一个种子判形态 | 每 host 1 请求；robots 不允许则零请求；挑战页 / 登录墙 / 链路失败 / 错误页只记录，不再请求 |
| 探索 | `crawl` 多臂选举，产出证据行与试验行 | 沿用 `--max-pages` / `--max-requests` / 节流 / 熔断 / 限频冷却 / 会话 |
| 路径发现 | 从本轮留存 HTML 提同站链接，跳过静态资源 | 只保留种子 host；`--max-discovered` 封顶；不额外发请求 |
| 规划 | `explore` 把试验行变成配额计划，`augment` 补新路由类 | 新路由按“不同 URL 数 × 每条臂一次”给额度，总额受剩余请求数封顶 |
| 执行 | `crawl --plan` 按配额跑下一轮 | 计划外路由零请求；页面记 `plan-exhausted` |
| 收敛 | `advise` 给下一步；终态动作与安全信号立即停 | 轮数、页面、请求三重上限 |

产物全部写进 `--out`：`evidence.jsonl` / `trials.jsonl`（跨轮累计）、`r<N>-evidence.jsonl` / `r<N>-trials.jsonl` / `r<N>-urls.txt`（单轮）、`plan.json`、`policy.json`、`bodies/`（`--discover` 时）与 `agent.json`。

`agent.json` 契约 `agent/1`：`seeds` / `out` / `budget`（上限与用量）/ `analysis`（逐 host 形态、robots、下一步）/ `rounds`（每轮 crawl 摘要、计划、发现数、建议动作）/ `advice`（最后一轮 `advise/1`）/ `stop` / `artifacts`。

| `stop.kind` | 含义 | 退出码 |
|---|---|---|
| `goal` | 可归因样本都已拿到内容，转防回归 | 0 |
| `business` | 多数失败是鉴权 / 路由 / 抽取口径，继续探测无收益 | 0 |
| `noise` | 可归因样本为 0，全是链路或 5xx | 0 |
| `rate-limit` | 被拦样本过半是限频信号 | 0 |
| `max-rounds` / `budget` | 轮数或请求 / 页面预算用尽 | 0 |
| `frontier-empty` / `plan-empty` | 没有可继续的 URL，或计划没有剩余动作 | 0 |
| `analysis-gated` | 所有种子都被形态或 robots 拦下 | 0 |
| `safety` | 限频冷却、熔断或全臂暂停 | 1 |
| `config` | 没有可用客户端臂 | 1 |
| `crawl-error` | crawl 超时或输出不可解析 | 1 |

常用选项：`--out`（必填）、`--max-rounds 2`、`--max-pages 20`、`--max-requests 40`、`--budget 12`、`--discover/--no-discover`、`--max-discovered 20`；其余采集参数与 `crawl` 同名同义。`--i-have-permission` 只跳过 robots 检查，不改变节流、熔断与限频。退出码 `0` 表示在护栏内跑完（含预算 / 轮数 / 终态建议 / 合规拦下），`1` 表示安全或配置原因停手，`2` 是用法错误。

`recon agent` 只调用已存在的 `shape` / `crawl` / `explore` / `advise`，不自己发请求，也不新增绕过手段：不求解验证码、不轮换出口、不绕过登录或付费墙。要改判定，改 `reconlib/` 里的规则或 `advise` 动作表；不要在 Agent 里另写一份度量或归类。

## advise 循环

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
- **有停止条件**：每个动作都带 `stop`，Agent 到达条件必须停，不能无限试错。遇到 `all-arms-suspended`（同路由所有候选臂连续 blocked 被暂停）必须停手核查 robots / 限速 / 出口，不得把 `--block-budget 0` 当继续试的理由。
- **不扩大授权**：`check-authorization` 只建议使用站点方授权的凭证或官方接口，不提供获取凭证的手段。
- **不改红线**：[SKILL.md](../SKILL.md) 的红线与 [crawl.md](crawl.md) 的护栏不因 Agent 调用而放宽。

## 边界

`advise` 是规则层，不是策略执行器：它不选臂、不发请求、不写证据、不修改档案。需要选臂用 `crawl` / `quick`，需要离线比较策略用 `bandit`，需要把后验变成下一批探测用 `explore`（只读），需要结算用 `stats`；想把这一轮学到的后验留给下一轮，用 `--policy-out` / `--policy-in`（见 [strategy.md](strategy.md)）。换臂或补样本时，`next.commands` 会给出 `--trial-out` 与 `recon explore --trials ...` 的组合；Agent 应先出计划、再执行 `crawl`。规则只覆盖本仓库已有的结局词表；新增结局要先扩 `evidence` / `stats` 词表，再扩动作表，并补测试。
