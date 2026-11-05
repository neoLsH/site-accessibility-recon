# 一套能力，四种形态

同一组原子（[reconlib](atoms.md)）对外有四个交付面：**Skill / CLI / Agent / LLM**。它们不各写一套口径，只回答不同的问题、服务不同的调用方。

```
reconlib 原子（度量 / 归类 / robots / 取样 / 节奏 / 证据 / 上下文 / 选举 / 推演 / 建议）
   ├─ Skill  → 告诉 Agent 何时用哪个命令、怎么读结果
   ├─ CLI    → recon <命令>，人读表 + --json 单对象
   ├─ Agent  → recon agent（分析 → 探索 → 计划 → 执行）或 crawl → stats → advise 循环
   └─ LLM    → 把 advise/1 当事实层，负责解释、排序与档案
```

| 形态 | 入口 | 产物 | 适合 | 不负责 |
|---|---|---|---|---|
| Skill | [SKILL.md](../SKILL.md) + `references/` | 流程、判据、案例与红线 | Agent 激活、人按需阅读 | 发请求、跑批 |
| CLI | `scripts/recon.py <命令>` | 人读表；`--json` 单对象 | 脚本、CI、手工排查 | 自己决定下一步 |
| Agent | `recon agent` / `recon advise --json` | `agent/1` 有界循环报告；`advise/1` 只读动作契约 | 一键探索与自动编排 | 常驻调度、绕过护栏 |
| LLM | `advise/1` + 提示骨架 | 解释、路由排序、档案草稿 | 汇报与决策辅助 | 改写计数、下因果结论 |

## 同一条链路

1. **Skill** 决定先做什么：可行性快筛 → 分诊 → 分层隔离 → 对策与回归。
2. **CLI** 执行单点：`quick` / `shape` / `matrix` / `canary` / `fetch` / `crawl` 产出观测与证据行。
3. **Agent** 循环：`recon agent` 串起站点分析、`crawl`、路径发现、`explore` 计划与 `crawl --plan`；也可自己用 `stats` → `advise` → 执行 → 回写证据行。
4. **LLM** 解释：只引用 `advise/1` 里的计数与动作，说明假设与验证方式，不替代规则层。

`bandit` 与 `bandit.md` 是这条链路的离线旁支：只比较选举策略，不产生证据行。

## 共享契约

四种形态都遵守同一组契约，换形态不改口径：

| 契约 | 定义处 | 规则 |
|---|---|---|
| 结局词表 | [atoms.md](atoms.md) | `ok / degraded / blocked / auth / missing / noise`，与 `stats` 一致 |
| 证据行 | [crawl.md](crawl.md) | 只有可取内容的行能喂 `stats`；试验行（`kind: trial`）单列 |
| 上下文 | [strategy.md](strategy.md) | `host|路由类|响应类`；回退链与成本惩罚在各能力点一致 |
| 噪声 | [metrics.md](metrics.md) | 5xx / 链路失败不进可访问性分母 |
| 置信度 | [SKILL.md](../SKILL.md) | observed / mechanism / efficacy；假设不伪装成机制 |
| 停止条件 | [agent.md](agent.md) | 每个建议都带 `stop`，到达即停 |
| 红线 | [SKILL.md](../SKILL.md) | 授权、robots、限速、不绕鉴权——任何形态都不放宽 |

## 扩展一处，四处受益

- **加客户端臂**：内置臂在 `reconlib/arms.py` 注册；外部内核写进仓库外 `arms.json`，`fetch` / `crawl` / `canary` / `quick` / `matrix` / `bandit` 自动可用。
- **加上下文维度**：只改 `reconlib/context.py`，回退链与选臂统计自动跟上；不要在 CLI 里另写一套键。
- **加结局**：先扩 `evidence` / `stats` 词表，再扩 `advice` 动作表，最后补行为测试与自检；跳过前两步会让计数对不上。
- **加动作**：只改 `reconlib/advice.py` 与动作表，CLI 与 LLM 契约自动带出；不要让 CLI 自己写判断。
- **加形态**：新形态只做编排，复用现有原子；出现第二份度量或归类实现即视为缺陷。

## 怎么选

| 你的处境 | 用哪个 |
|---|---|
| 第一次排查，需要判据与顺序 | Skill |
| 已有脚本 / CI，要可结算的输出 | CLI + `--json` |
| 想一键跑完分析、探索与配额执行 | `recon agent`（有界循环） |
| 想按证据自动决定下一步 | Agent（`advise`） |
| 要把结果讲给人听、写进报告 | LLM（读 `advise/1`） |
| 只是想比较选举策略 | `bandit`（离线旁支） |
| 想把这一轮学到的选臂后验留给下一轮 | `crawl` / `quick` / `matrix` / `fetch` 的 `--policy-out`，下一轮 `--policy-in` |

默认 Agent、`agent/1` 契约与 LLM 接入细节见 [agent.md](agent.md)；命令与退出码见 [cli.md](cli.md)。
