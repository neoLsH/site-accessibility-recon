# 离线推演：bandit

`crawl` 在真实请求上学习，`bandit` 在**不上网**的前提下回答下一个问题：*如果每臂胜率就是这样，换一种选举策略能省多少请求？*

它只重排已有客户端臂，不新增能力，也不产生新证据。推演结果不能当成“已经验证有效”。

## 输入

胜率必须来自观测，三种来源三选一：

```bash
# 1) 直接给每臂单次尝试成功率
recon bandit --arms native=0.4,cffi:chrome=0.75 --pages 200 --seed 7

# 2) 从 crawl 的试验行估计（只取可归因、非限频的尝试）
recon bandit --trials recon/<代号>/trials.jsonl --pages 200 --json

# 3) 按上下文分组；每组独立选臂、独立更新后验
recon bandit --rates-json recon/<代号>/rates.json --pages 200 --json
```

`--rates-json` 的格式：

```json
{"contexts": [
  {"context": "example.com|article|challenge", "pages": 80,
   "rates": {"native": 0.35, "cffi:chrome": 0.8}},
  {"context": "example.com|list|content", "pages": 40,
   "rates": {"native": 0.95, "cffi:chrome": 0.7}}
]}
```

- 来自试验行时，`ok` 记成功，`degraded` / `blocked` 记失败；`noise` / `auth` / `missing` 与 `rate_limited` 剔除——这些不是客户端栈的差异。
- 同一臂至少要有一次可归因尝试，否则该臂不参与推演；一个都没有则报用法错误。
- 臂名沿用 [arms.md](arms.md) 的命名：`native` / `cffi:<profile>` / `browser:<engine>`。

## 输出

| 字段 | 含义 |
|---|---|
| `requests` | 模拟期间发出的总请求数（不含 robots） |
| `successes` | 拿到内容的页数 |
| `access_rate` | `successes / pages` |
| `requests_per_success` | 每成功一页要花多少请求；一次都没成功时为 `null` |
| `pulls` / `wins` | 每臂被选次数与贡献的成功数 |
| `contexts` | 仅 `--rates-json`：每个上下文的页数、被选次数与胜出次数 |
| `best-single` | 基线：始终只用最强臂、每页一次请求；成功率是**期望值**，标 `*` |

`best-single` 的 `requests` 恒等于页数，适合当“最少请求”的下界；它的成功率不是采样值，不能与其它行的小数位直接比精度。

## 怎么读

- **先看 `requests_per_success`，再看 `access_rate`。** 页数固定时成功率会饱和，请求开销才是策略差异。
- **`thompson` 通常前期略贵、后期更省**：预热期会试每条臂，页数太少（几十页）时它可能输给 `fixed`，这不代表策略无效。
- **跑多个种子再下结论。** 单个 `--seed` 只是可复现，不是置信区间；看 `--seed 1/2/3` 下结论是否稳定。
- **胜率越接近，选举收益越小。** 两臂都在 0.7 附近时，`fixed` 已经够用；差距大才值得让策略自己学。
- **`best-single` 不是可执行策略**：它假设你已经知道哪条臂最强。它只回答“上限大概在哪”。
- **分组推演看的是“该不该分上下文”**：同一 seed 下，若每个上下文的最优臂不同，分组策略才会优于全局单臂；最优臂一致时别为每个路由复制策略。
- **成本不进推演**：`bandit` 只算请求数，不模拟 `--cost-weight` 的耗时惩罚；成本差异用真实试验行的 `cost_ms` 看。

## 偏差与限制

- **自适应采样偏差**：`--trials` 里的胜率来自一个已经在选臂的 `crawl`，弱臂被选得少、样本更少，估计更不稳。样本不足时宁可手给 `--arms`。
- **点估计**：每臂只用一个概率，不建模概率门、时间漂移、缓存与并发；这些要用 `canary`、多 pass 与真实对照去测。
- **不模拟节流与熔断**：请求开销只算“尝试次数”，实际耗时还要加上 `crawl` 的间隔与退避。
- **不产生证据行**：推演结果不能喂 `stats`，也不该写进站点档案的 `metrics`。
- **上下文仍须实测**：`--rates-json` 的分组只是输入；分组胜率本身要来自 `crawl` / `quick` 的证据行，不能凭路由名猜。

## 边界

只用于在已有客户端臂之间做成本比较。不提供验证码求解、账号池、凭证填充、出口轮换，也不绕过付费墙 / 登录鉴权 / 访问控制；推演层不改变 [crawl.md](crawl.md) 的护栏与合规约束。
