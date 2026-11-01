# 探索规划：explore

`crawl` 边跑边学，`bandit` 比较策略，`explore` 回答**下一批请求该花在哪**：哪些上下文还缺样本、哪条臂从没试过、预算够不够、什么时候该停。它只读已有试验行或策略档案，不联网、不发请求，也不产生证据行。

## 输入

两种来源二选一：

```bash
# 1) crawl 的试验行（只取可归因、非限频的尝试）
recon explore --trials recon/<代号>/trials.jsonl --budget 30 --json

# 2) 上一轮的策略档案（recon-policy/1）
recon explore --policy-in recon/<代号>/policy.json --json

# 加入档案里没有的新臂，触发“未见臂”探测
recon explore --policy-in recon/<代号>/policy.json \
    --arms native,cffi:chrome,browser:chromium
```

- 试验行没有 `context` 时，按 `url` + 路由形状推导为 `host|路由类|unknown`；没有 `url` 的行无法定位 host，直接剔除。
- `auth` / `missing` / `noise` / 限频行不进入统计——它们不是客户端栈的差异。
- `--arms` 只列臂名，不接受 `arm=rate`；胜率必须来自试验行或档案。
- 策略档案里的 `prior_strength` / `half_life` / `cost_weight` 以档案为准，命令行只追加新臂。

## 输出

| 字段 | 含义 |
|---|---|
| `contexts` | 每个上下文的页数权重、`best_arm`、`confidence`、`decision`、每臂后验 |
| `steps` | 本轮建议：上下文、臂、请求数、`kind`（`unseen` / `warmup` / `explore`）、理由 |
| `hold` | 已判 `exploit` 的上下文：保持当前选臂，不再花请求 |
| `unseen_arms` | 全局一次都没试过的臂 |
| `allocated` / `remaining` | 已分配 / 剩余预算；不超过 `--budget` |
| `observed` | 尝试数、成功数、单次成功率、被剔除行数、上下文推导行数 |
| `notes` / `stop` | 预算未用尽、未覆盖动作等提示与停止规则 |

`pages` 是样本权重，不是精确页数：试验行按 `url` 去重，策略档案按 `pulls` 合计。它只用于排序与平局，不参与胜率计算。

## 规划顺序

1. **未见臂**：该上下文里本地 `pulls == 0` 的臂，各补到 `--target-pulls`（默认 4）。
2. **补样本**：有少量样本但未到目标值的臂，补齐差额。
3. **探索**：已补够样本、但 `P(best)` 未到 0.8 的上下文，优先探测次优臂。
4. **保持**：`P(best)` 达标且样本足够的上下文进入 `hold`。

分配按“权重 /（已分配 + 1）”贪心：未见与补样本权重 1.0，探索权重 `1 − confidence`；同权重按页数多、上下文名、臂顺序打破平局。单上下文最多 `--max-per-context` 个请求，总请求不超过 `--budget`。

## 怎么读

- **先看 `steps` 是否覆盖了最不确定的上下文**，再看 `allocated`。预算没用尽是正常现象：动作都满足目标值或单上下文上限。
- **`unseen` 不是“新手段”**，只是这条合规客户端臂还没在这个上下文里出现过；先各试一次，再谈胜率。
- **`warmup` 的请求数来自差额**，不是拍脑袋的重复请求；补到目标值就停。
- **`explore` 探测次优臂**，是为了把“谁更好”的不确定性降下来；不确定度高时分配更多，不确定度低时自然退回 `hold`。
- **`hold` 不等于永久结论**。规则漂移、路由变化或新臂加入后，重新跑 `explore` 再看。
- 多个种子/多轮结果不一致时，先补样本，不要直接把某条臂写死进配置。

## 工作流

```bash
# 第一轮：采集并留下试验行与证据行
recon crawl --from urls.txt --selector '#content' --max-pages 20 \
    --evidence-out recon/demo/evidence.jsonl \
    --trial-out recon/demo/trials.jsonl \
    --policy-out recon/demo/policy.json

# 看下一批请求怎么分
recon explore --trials recon/demo/trials.jsonl --arms native,cffi:chrome --budget 30

# 第二轮：按计划补样本，继续累积后验
recon crawl --from urls2.txt --selector '#content' --max-pages 30 \
    --policy-in recon/demo/policy.json --policy-out recon/demo/policy.json \
    --trial-out recon/demo/trials2.jsonl

# 用累积后的档案再规划
recon explore --policy-in recon/demo/policy.json --budget 20
```

`explore` 只给计划，不执行它给的请求；真正的请求仍由 `crawl` / `quick` / `fetch` 发出，robots、节流与熔断照常生效。

## 偏差与限制

- **来源偏差**：试验行来自已经在选臂的 `crawl`，弱臂样本更少；样本不足时宁可用 `--policy-in` 的完整后验，或先跑一轮 `crawl`。
- **点估计**：每臂只有成功率与成本均值，不建模概率门、时间漂移与并发。
- **不模拟耗时**：预算单位是请求数；`--delay`、退避与 robots 请求不在内。
- **上下文是粗分**：推导出的 `unknown` 响应类会把不同结局混在一起；发现结论反复翻转时，先核对试验行是否带 `context`。
- **不产生证据**：规划结果不能喂 `stats`，也不该写进站点档案的 `metrics`。

## 边界

只在已有合规客户端臂之间做请求分配。不提供验证码求解、账号池、凭证填充、出口轮换，也不绕过付费墙 / 登录鉴权 / 访问控制。规划层不改变 [crawl.md](crawl.md) 的护栏与合规约束。
