# 批量取内容：crawl

`fetch` 回答“这一页用哪条臂能拿到”，`crawl` 回答“一批页面，把有限的请求预算花在哪条臂上，并且留下能结算的证据”。

## 它做什么

```
读 URL → robots 闸门 → 按 host 取策略 → 选臂 → 节流 → 取一次 → 归类
      → 更新策略与节奏 → 试下一臂 / 停手 → 落证据行 + 试验行
```

- **按 host 分别学习**：不同站点的拦截策略不同，臂的胜率不能跨站共享。
- **臂只有已有客户端协议栈**：`native`、`cffi:<profile>`，或外接的渲染服务。不是“更多绕过手段”，而是把既有手段的选举做好。
- **失败可归因才换臂**：限频、鉴权、内容不存在、链路噪声换栈救不了，直接停手并如实记录。

## 选举规则

| 结局 | 对策略 | 对节奏 | 下一步 |
|---|---|---|---|
| `ok` | 记成功 | 重置退避 | 该页结束 |
| `degraded` | 记失败 | 重置退避 | 试下一臂 |
| `blocked`（可归因） | 记失败 | 连续计数 +1，可能熔断 | 试下一臂 |
| `blocked` + 限频 | 不更新 | 按 `Retry-After` 或指数退避 | 该页结束，**不自动重试** |
| `auth` / `missing` | 不更新 | 重置 | 该页结束 |
| `noise`（5xx / 链路） | 不更新 | 指数退避 | 该页结束 |
| `blocked`（不可归因，未归类） | 不更新 | 计数 +1 | 该页结束 |

`thompson` 是默认策略：每臂维护 Beta 后验，按采样值选臂，冷启动用 `--min-pulls` 各试一次。也可选 `ucb1`、`fixed`（按给定顺序）、`round_robin`；`--seed` 固定后 `thompson` 可复现。

## 护栏（默认值即红线）

| 约束 | 默认 | 说明 |
|---|---|---|
| `--max-pages` | 50 | 一次运行的 URL 上限 |
| `--max-requests` | 150 | 不含 robots 的请求上限 |
| `--delay` | 5s | 每 host 请求间隔；回环地址自动降为 0 |
| 连续 blocked | 3 | 触发该 host 熔断，余下同 host URL 记为 `circuit-open` |

熔断只封当前 host：其他 host 仍可在预算内继续。预算或熔断让运行提前结束时退出码为 1。

## 两种行，别混

| 文件 | 行类型 | 用途 | 能否进 stats |
|---|---|---|---|
| `--evidence-out` | 每页一行，含 `outcome` | 双指标结算 | 能 |
| `--trial-out` | 每次尝试一行，`kind: trial` + `trial_outcome` | 复盘“试了哪条臂、为什么换” | **不能**，`stats` 会直接报错 |

一页最多一行证据：取最好的一次结局（`ok` > `degraded` > `blocked`）。试验行则保留每次尝试，用来解释选举过程；两者的分母不同，合并会让成功率虚高。

## 用法

```bash
recon crawl --from urls.txt --selector '#content' \
    --arms native,cffi:chrome --policy thompson --seed 7 \
    --evidence-out recon/demo/evidence.jsonl \
    --trial-out recon/demo/trials.jsonl

recon stats recon/demo/evidence.jsonl          # 跑完直接结算
```

`--i-have-permission` 只用于已获站点方明确许可的场景；默认先查 robots，`deny` 的 URL 一个请求都不发，记为跳过而不是 `blocked`。

## 常见误用

- 把 `crawl` 当“并发下载器”：它没有并发，只有节流与预算；并发会把限频信号变成假阳性。
- 把试验行喂给 `stats`：分母会变成“尝试次数”，不是“页面数”。
- 用 `crawl` 反复重试同一批被 blocked 的 URL：熔断后应换数据源或降级口径，而不是继续加请求。
- 在多个 host 之间共享一个策略：胜率会被平均掉，选臂失去意义。

## 边界

只测有权测的目标；遵守 robots、ToS 与站点限速。不提供验证码求解、账号池、凭证填充、出口轮换，也不绕过付费墙 / 登录鉴权 / 访问控制。`crawl` 只是把已有客户端臂的选举与节流做可靠，不改变这些边界。
