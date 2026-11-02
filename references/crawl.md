# 批量取内容：crawl

`fetch` 回答“这一页用哪条臂能拿到”，`crawl` 回答“一批页面，把有限的请求预算花在哪条臂上，并且留下能结算的证据”。两者共用 `ArmSession`（robots → 节流 → 选臂 → 取样 → 归类 → 更新），`crawl` 只负责预算、跨页循环与落盘。

## 它做什么

```
读 URL → robots 闸门 → 按 host + 上下文取策略 → 选臂 → 节流 → 取一次 → 归类
      → 更新策略与节奏 → 试下一臂 / 停手 → 落证据行 + 试验行
```

- **按 host 与上下文分别学习**：不同站点的拦截策略不同，臂的胜率不能跨站共享；同一站内还会按 `host|路由类|响应类` 再分开统计，样本不足时沿回退链聚合（见 [strategy.md](strategy.md)）。
- **臂只有已有客户端协议栈**：`native`、`cffi:<profile>`，或外接的渲染服务（`browser:<engine>`，需仓库外配置）。不是“更多绕过手段”，而是把既有手段的选举做好；注册表见 [arms.md](arms.md)。
- **失败可归因才换臂**：限频、鉴权、内容不存在、链路噪声换栈救不了，直接停手并如实记录。

## 选举规则

| 结局 | 对策略 | 对节奏 | 下一步 |
|---|---|---|---|
| `ok` | 记成功（1.0） | 重置退避 | 该页结束 |
| `degraded` | 记半个奖励（0.5） | 重置退避 | 试下一臂 |
| `blocked`（可归因） | 记失败 | 连续计数 +1，可能熔断 | 试下一臂 |
| `blocked` + 限频 | 不更新 | 按 `Retry-After` 或指数退避；连续超预算则 host 冷却 | 该页结束，**不自动重试** |
| `auth` / `missing` | 不更新 | 重置 | 该页结束 |
| `noise`（5xx / 链路） | 不更新 | 指数退避 | 该页结束 |
| `blocked`（不可归因，未归类） | 不更新 | 计数 +1 | 该页结束 |

`thompson` 是默认策略：每臂维护 Beta 后验，按采样值选臂，冷启动用 `--min-pulls` 各试一次。也可选 `ucb1`、`fixed`（按给定顺序）、`round_robin`；`--seed` 固定后 `thompson` 可复现。慢臂按 5000ms 归一化扣分，`--cost-weight` 默认 `0.1`，`0` 关闭。`--prior-strength` 默认 `4`，从相邻上下文借先验解决冷启动；`--half-life` 默认 `0`（不遗忘），站点规则漂移时再开。`--block-budget` 默认 `3`：同一 `host + 路由类 + 臂` 连续可归因 `blocked` 达阈值后，该臂本轮暂停；同一上下文所有候选臂都暂停时，该页记 `all-arms-suspended` 并停手，不再发请求。未配置或缺少 `--require` 能力的臂会记成 `skipped`，**不计请求预算、不触发熔断**，也不参与策略更新。

## 护栏（默认值即红线）

| 约束 | 默认 | 说明 |
|---|---|---|
| `--max-pages` | 50 | 一次运行的 URL 上限 |
| `--max-requests` | 150 | 不含 robots 的请求上限 |
| `--delay` | 5s | 每 host 请求间隔；回环地址自动降为 0 |
| 连续 blocked | 3 | 触发该 host 熔断，余下同 host URL 记为 `circuit-open` |
| `--rate-limit-budget` | 2 | 同一 host 连续限频达到预算即冷却；`0` 关闭 |
| `--rate-limit-cooldown` | 30s | 冷却时长，取它与 `Retry-After` 的较长者；冷却期该 host 零请求 |
| `--block-budget` | 3 | 同一 `host + 路由类 + 臂` 连续可归因 blocked 后暂停该臂；全暂停记 `all-arms-suspended` |
| 会话状态 | 开启 | 按 `(臂, host)` 复用本轮服务端下发的状态，临时目录、结束即删；`--no-cookies` 关闭 |
| `--plan` | 无 | 按 `explore --plan-out` 的计划执行：只发计划内 `(host + 路由类, 臂)` 配额内的请求，计划外 / 用尽零请求 |

熔断只封当前 host：其他 host 仍可在预算内继续。臂暂停只封一条路由上的一条臂，其他路由仍可用别的臂；`--block-budget 0` 关闭暂停。限频冷却同样只封当前 host，冷却期连 robots.txt 都不取，被跳过的页记 `rate-limit-cooldown` 且 `requests: 0`；限频样本不更新选臂后验。预算、熔断或冷却让运行提前结束时退出码为 1。

带 `--plan` 时，配额按 `host + 路由类` 聚合（同一路由的不同响应类共享额度），只允许计划内的臂；计划外或额度用尽的路由一个请求都不发（连 robots.txt 都不取），页面记 `plan-exhausted`，摘要给出 `plan` 与 `plan_exhausted_pages`。计划不强制某次请求用哪条臂，选臂仍在额度内按策略决定；`--max-requests`、robots、节流、熔断与限频冷却全部照旧。计划里的臂与 `--arms` 无交集时 stderr 会警告。

默认会保留本轮目标站点下发的会话状态：第一页被拦、同一臂第二页通过时，先用 `--no-cookies` 复跑，确认结论来自客户端栈而不是会话。会话值不进证据行与试验行，机制见 [sessions.md](sessions.md)。

## 两种行，别混

| 文件 | 行类型 | 用途 | 能否进 stats |
|---|---|---|---|
| `--evidence-out` | 每页一行，含 `outcome` / `context` | 双指标结算 | 能 |
| `--trial-out` | 每次尝试一行，`kind: trial` + `trial_outcome` + `context` / `cost_ms` | 复盘“试了哪条臂、为什么换” | **不能**，`stats` 会直接报错 |

一页最多一行证据：取最好的一次结局（`ok` > `degraded` > `blocked`）。试验行则保留每次尝试，用来解释选举过程；两者的分母不同，合并会让成功率虚高。

## 用法

```bash
recon crawl --from urls.txt --selector '#content' \
    --arms native,cffi:chrome,browser:chromium --policy thompson --seed 7 \
    --arms-file ~/.config/site-recon/arms.json --cost-weight 0.1 \
    --prior-strength 4 --half-life 0 --block-budget 3 \
    --evidence-out recon/demo/evidence.jsonl \
    --trial-out recon/demo/trials.jsonl \
    --policy-out recon/demo/policy.json

# 下一轮接着学：先恢复上一轮后验，再写回同一档案
recon crawl --from urls-b.txt --selector '#content' \
    --policy-in recon/demo/policy.json --policy-out recon/demo/policy.json

recon stats recon/demo/evidence.jsonl          # 跑完直接结算
```

`--i-have-permission` 只用于已获站点方明确许可的场景；默认先查 robots，`deny` 的 URL 一个请求都不发，记为跳过而不是 `blocked`。

## 常见误用

- 把 `crawl` 当“并发下载器”：它没有并发，只有节流与预算；并发会把限频信号变成假阳性。
- 把试验行喂给 `stats`：分母会变成“尝试次数”，不是“页面数”。
- 用 `crawl` 反复重试同一批被 blocked 的 URL：熔断后应换数据源或降级口径，而不是继续加请求。
- 把 `--block-budget 0` 当“提高成功率”：关掉暂停只增加撞墙请求，不改变臂本身；要更多样本应调高阈值或在合规范围内换路由。
- 在多个 host 之间共享一个策略：胜率会被平均掉，选臂失去意义。
- 把策略档案当跨站通用配置：档案里带 host 与路由键，换目标要另存一份；恢复时未知臂会被忽略，但已知臂的旧胜率仍会被带进来。

## 跑完之后

`--trial-out` 的试验行可以直接喂离线推演，比较 `thompson` 与 `fixed` 在同样胜率下的请求开销：

```bash
recon bandit --trials recon/demo/trials.jsonl --pages 200
```

推演只回答“换策略值不值”，不产生新证据；偏差与读法见 [bandit.md](bandit.md)。要决定下一批请求花在哪，用只读规划器：

```bash
recon explore --trials recon/demo/trials.jsonl --budget 30 \
    --plan-out recon/demo/plan.json

# 严格按计划发请求
recon crawl --from urls-b.txt --selector '#content' \
    --plan recon/demo/plan.json --trial-out recon/demo/trials2.jsonl
```

它按上下文列出未见臂、补样本与探索动作，并给出停止规则；`--plan-out` 的产物交给 `crawl --plan` 就按配额执行，见 [explore.md](explore.md)。要按上下文分开推演，把各组胜率写进 `--rates-json`，格式见 [strategy.md](strategy.md)。

## 边界

只测有权测的目标；遵守 robots、ToS 与站点限速。不提供验证码求解、账号池、凭证填充、出口轮换，也不绕过付费墙 / 登录鉴权 / 访问控制。`crawl` 只是把已有客户端臂的选举与节流做可靠，不改变这些边界。
