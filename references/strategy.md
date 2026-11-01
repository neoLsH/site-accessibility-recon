# 选臂策略：上下文与成本

选臂只在**合规的客户端栈**之间做：`native`、`cffi:<profile>`、外部 `browser:<engine>`。它回答“这类页面用哪套客户端更省请求”，不回答“怎么绕过风控”；robots、节流、熔断在选臂之前照样执行。

## 上下文键

每次探测都落成 `host|路由类|响应类`：

| 维度 | 取值 | 判据 |
|---|---|---|
| `host` | 主机名 | 状态按 host 隔离，不跨站迁移 |
| 路由类 | `api` / `article` / `list` / `other` | 只看 URL 路径形状（`/api/`、`.json`、`/article/`、`/list/` 等），不用站点常量 |
| 响应类 | `content` / `shell` / `challenge` / `login` / `missing` / `noise` / `unknown` | 由首响应结局映射：ok→content、degraded→shell、blocked→challenge、auth→login、missing/noise 原样 |

首次请求前响应类未知，先用 `unknown` 占位，拿到结果后立即改写为实际响应类。**首响应即上下文**：不要求先做完整八形态判定，只粗分到“这类页面该换内核吗”够用。

样本不足时沿回退链聚合，先命中先用：

```
host|article|challenge → host|article|* → host|*|* → *
```

每级都要求所有候选臂的 pulls ≥ `--min-pulls`，否则继续回退。回退只影响**选臂读哪份统计**，更新永远写回最具体的键。

## 奖励与成本

| 结局 | 奖励 | 是否更新后验 |
|---|---|---|
| `ok` | 1.0 | 是 |
| `degraded` | 0.5 | 是 |
| `blocked` | 0.0 | 是 |
| `auth` / `missing` / `noise` | — | 否 |
| 任意限频（`rate_limited`） | — | 否 |

后三种不是客户端栈的差异，更新只会把“登录墙 / 路由不存在 / 链路抖动”学成“换个内核就好了”。

成本按 5000ms 归一化，从每级取样的 `elapsed_ms` 记录：

```
penalty = cost_weight × min(1, 平均耗时 / 5000)
得分 = 采样奖励 − penalty        # thompson 的 Beta 抽样 / ucb1 的均值项
```

`--cost-weight` 默认 `0.1`，`0` 关闭成本感知。成本只在同上下文内比较；跨上下文不比较绝对耗时。

两种惩罚差异：`degraded` 是“拿到了但不完整”，记半分仍保留选它的机会；耗时惩罚只压低慢臂，不改变它的成功率统计。二者叠加后，慢且半成功的臂会排到“快且成功”的臂之后，但仍会在其他臂被拦截时被重新选中。

## 各能力点的选臂语义

| 能力点 | 默认策略 | 语义 |
|---|---|---|
| `fetch` | `fixed` | 逐页按给定顺序；一页内逐级换臂，成功即停 |
| `crawl` | `thompson` | 按 host 在线学习；跨页累积，受预算与熔断约束 |
| `canary` | `fixed` | 整个 pass 序列**固定一条臂**，否则翻转率会被选臂差异污染 |
| `quick` | `thompson` | 每条 URL 只发**一个**请求；跨 URL 学习，先验来自前几条 |
| `matrix` | `fixed` | 默认穷举：每个 pass 跑完全部臂，用来观察翻转而非省请求 |
| `bandit` | — | 离线推演，不联网；比较策略的请求开销 |
| `advise` | — | 只读证据行，按 `context` 分组提示“先分组再换臂” |

`quick` 的一条 URL 一次请求意味着它**不能**在同一条 URL 内试探；它的选臂是“上一次快筛学到的东西”用在下一班 URL 上。要判断单条 URL 需要哪条臂，用 `fetch` 或 `matrix`。

## 读法

看三件事，缺一不下结论：

1. **样本**：每臂 pulls 是否 ≥ `--min-pulls`，不足时看回退到了哪一级（`level` 字段）。`*` 级别只说明“还没分组数据”，不代表站点没有上下文差异。
2. **成本**：`cost_ms` 为 `null` 表示该臂没有耗时样本，成本惩罚未生效。
3. **上下文差异**：同一 host 下不同 `context` 的 `best_arm` 不一致，才值得按上下文分别配置；一致时用全局结论即可，别为每个路由复制一份策略。

上下文是粗分，误分类会有偏差：`/detail/` 下的列表页会被记成 `article`，`/search/` 返回的空态会被记成 `content`。偏差只会让相似页面共享统计，不会跨 host 泄漏；发现某类路由结论反复翻转时，先核对 `route_class` 是否把两类页面混在一起，再决定是否值得扩大上下文维度。

## 推演与验证

```bash
# 单组胜率：比较策略的请求开销
recon bandit --arms native=0.4,cffi:chrome=0.7 --pages 200 --seed 7

# 分组胜率：每个上下文独立选臂
recon bandit --rates-json recon/<代号>/rates.json --pages 200 --json

# 真实取样：先快筛，再按证据行看分组
recon quick --from urls.txt --selector '#content' --seed 7
recon advise recon/<代号>/evidence.jsonl --json
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

推演不产生证据：胜率必须来自实测（`crawl --trial-out` 或 `quick` 的证据行），策略比较只回答“如果这个胜率成立，换策略值不值”。真实改善仍要用 `stats --compare` 看区间。

## 边界

- 臂是协议栈描述，不是绕过手段；不配验证码求解、账号池、凭证填充、出口轮换。
- 外部内核由适配器承担，仓库只校验契约，见 [arms.md](arms.md)。
- `auth` / `missing` 不被学成策略；遇到它们先按证据行处理问题本身，而不是换臂。
- 成本只用于同类页面的排序，不用于“用更贵的内核换更多请求”。
