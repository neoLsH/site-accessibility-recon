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

## 分层先验（冷启动）

回退链解决“没样本时读哪一级”，但把父级证据原样搬过来会把“还没测”读成“已经测过”。v1.16 起改为**经验贝叶斯式先验**：

- 每格先算本地后验；选臂打分时叠加 `prior_strength`（默认 `4.0`）份来自父级的伪计数，均值取父级的 `(successes + 1) / (pulls + 2)`。
- 先验只参与**打分**，不改写本地计数：`snapshot` 的 `pulls` 仍是本地请求数，`n_eff` 才是叠加先验后的有效样本数；`--min-pulls` 的预热判断也看本地 pulls。
- 父级证据不重复计数：`host|route|*` 只取同 host 同 route 的**其他响应类**，`host|*|*` 取同 host 的**其他路由**，`*` 级别才用其他 host 的 pooled 统计。
- `--prior-strength 0` 退回只读本级统计；站点之间差异大、或外部内核质量参差时，先关掉先验再比较。

先验让新上下文在第一轮就有可用的排序，但**不会**把未观测的臂算成“已验证”：诊断里的 `n_eff < 4` 仍会判 `explore`。

## 遗忘窗口（规则漂移）

站点规则会变，旧胜率不该永久支配选臂。`--half-life N` 让每次更新前把所有计数乘以 `0.5 ** (1/N)`：

- `N=0`（默认）不遗忘，保留全部历史。
- 站点稳定、样本少就别开；观察到“同一臂前几轮行、最近连续失败”时再开，从 20–50 起调。
- 遗忘只作用于本地计数，不作用于父级先验；它改变的是“旧证据还有多少权重”，不是“换一条臂”。

## 诊断：继续试还是收手

`snapshot` 不只给均值，还回答“现在该继续探索，还是用当前最优”：

| 字段 | 含义 |
|---|---|
| `posterior_mean` / `n_eff` | 叠加先验后的均值与有效样本数；`n_eff` 不是请求数 |
| `utility` | 后验均值 − 成本惩罚，用于排序 |
| `ci95` | Wilson 区间，给“还差多少样本”一个量纲 |
| `p_best` | 状态哈希固定的确定性蒙特卡洛（400 次）估计“该臂最优的概率”，重复读取不抖动 |
| `confidence` / `decision` | 最优臂的 `p_best`；`>= 0.8` 且有效样本 `>= 4` 才 `exploit`，否则 `explore`；单臂恒 `exploit` |

`decision` 只是诊断提示，不改变 `select` 的探索行为：`thompson` 仍按后验抽样，`ucb1` 仍带探索项。要按诊断强制收手，用 `--policy fixed` 并显式给臂顺序。

## 跨轮热启动

```bash
# 第一轮：学到的后验落盘
recon crawl --from urls.txt --policy-out recon/<代号>/policy.json

# 第二轮：接着上一轮的后验，继续写回同一档案
recon crawl --from urls2.txt --policy-in recon/<代号>/policy.json \
    --policy-out recon/<代号>/policy.json
```

- 档案 schema `recon-policy/1`：只保存 host / 上下文键、每臂计数与配置快照；**不含响应体、Cookie 或凭证**。
- 原子写（临时文件 + fsync + rename）；恢复时只接受同 schema，未知臂忽略，损坏档案直接报用法错误。
- `quick` / `matrix` / `fetch` / `crawl` 都支持 `--policy-in` / `--policy-out`，可共用同一档案。
- 档案里的 `prior_strength` / `half_life` 只是记录，不覆盖本轮 CLI；换参数时以 CLI 为准。
- 默认写 `recon/`（已被忽略）；建议命名 `policy.json`，按目标隔离，别跨 host 复用。

热启动省的是**前几页的预热请求**，不改变合规约束：robots、节流、预算与熔断仍按本轮参数执行。

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
| `explore` | — | 只读后验，按上下文分配下一批探测请求；不联网、不发请求 |
| `advise` | — | 只读证据行，按 `context` 分组提示“先分组再换臂” |

`quick` 的一条 URL 一次请求意味着它**不能**在同一条 URL 内试探；它的选臂是“上一次快筛学到的东西”用在下一班 URL 上。要判断单条 URL 需要哪条臂，用 `fetch` 或 `matrix`。

`--policy-in` / `--policy-out` 只搬运后验，不改变上表的选臂语义：`canary` 仍固定单臂，`matrix` 仍默认穷举，`quick` 仍每条 URL 只发一个请求。

## 读法

看三件事，缺一不下结论：

1. **样本**：每臂 pulls 是否 ≥ `--min-pulls`，不足时看回退到了哪一级（`level` 字段）；再看 `n_eff` 与 `decision`。`*` 级别只说明“还没分组数据”，不代表站点没有上下文差异。
2. **成本**：`cost_ms` 为 `null` 表示该臂没有耗时样本，成本惩罚未生效；`utility` 已经把成本扣掉。
3. **上下文差异**：同一 host 下不同 `context` 的 `best_arm` 不一致，才值得按上下文分别配置；一致时用全局结论即可，别为每个路由复制一份策略。

`p_best` 高但 `decision` 仍是 `explore`，通常是有效样本不够（`n_eff < 4`）：先补几个请求，别急着把点估计写进档案。`ci95` 很宽时同理，区间宽度比小数位更能说明“还差多少证据”。

上下文是粗分，误分类会有偏差：`/detail/` 下的列表页会被记成 `article`，`/search/` 返回的空态会被记成 `content`。偏差只会让相似页面共享统计，不会跨 host 泄漏；发现某类路由结论反复翻转时，先核对 `route_class` 是否把两类页面混在一起，再决定是否值得扩大上下文维度。

## 推演与验证

```bash
# 单组胜率：比较策略的请求开销
recon bandit --arms native=0.4,cffi:chrome=0.7 --pages 200 --seed 7

# 分组胜率：每个上下文独立选臂
recon bandit --rates-json recon/<代号>/rates.json --pages 200 --json

# 上一轮的策略档案：按上下文分桶推演
recon bandit --policy-in recon/<代号>/policy.json --pages 200 --json

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

## 探索规划

诊断告诉你“该继续试”，`explore` 回答“接下来把请求花在哪”：

```bash
# 从试验行规划下一批请求
recon explore --trials recon/<代号>/trials.jsonl --budget 30 --json

# 从跨轮档案规划，并加入一条从未试过的新臂
recon explore --policy-in recon/<代号>/policy.json --arms native,cffi:chrome --json
```

- 每个上下文先看本地样本：未见臂各补到 `--target-pulls`，样本不足的臂补差额，够样本但 `P(best)` 仍低的上下文探测次优臂。
- 已判 `exploit` 的上下文进入 `hold`，不再分配请求；预算按不确定性加权，单上下文有上限。
- 输出只是计划：真正发请求的仍是 `crawl` / `quick` / `fetch`，robots、节流与熔断不变。细节见 [explore.md](explore.md)。

## 边界

- 臂是协议栈描述，不是绕过手段；不配验证码求解、账号池、凭证填充、出口轮换。
- 外部内核由适配器承担，仓库只校验契约，见 [arms.md](arms.md)。
- `auth` / `missing` 不被学成策略；遇到它们先按证据行处理问题本身，而不是换臂。
- 成本只用于同类页面的排序，不用于“用更贵的内核换更多请求”。
- 策略档案只含计数，不含响应体 / Cookie / 凭证；别把档案当“站点快照”共享，它仍带 host 与路由键。
