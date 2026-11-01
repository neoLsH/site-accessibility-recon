# 原子层：reconlib

`scripts/reconlib/` 把“量内容、认归类、查 robots、取一次样本、控节奏、落证据、选臂、推下一步”拆成互不依赖的原子。上层只做编排：`fetch` 是一条**客户端臂阶梯**，`crawl` 是**多臂选举 + 预算护栏**，`advise` 把证据行翻译成下一步动作；它们都只从这些原子取数，不另写一套口径。网络编排本身收敛到 `session`，它不是原子。

## 为什么先拆原子

- **口径只有一份**：内容区怎么量、什么算 blocked、哪些失败不能归因给客户端栈，只在这里定义。否则 `fetch`、`crawl`、`simulate` 各算各的，指标必然对不上。
- **可单测**：纯函数不联网，本地 fixture 即可验证；网络部分只集中在 `fetch` / `robots`。
- **可替换**：换渲染服务、换协议栈，只加一个客户端臂，不动归类和度量。

## 十一个原子

| 模块 | 回答什么 | 关键接口 | 副作用 |
|---|---|---|---|
| `measure` | 正文到底有多少 | `content_len(body, selector)` · `is_html(ctype)` | 无 |
| `classify` | 这次响应算哪种结局、能不能归因给客户端 | `classify(observation, selector, min_content)` | 无 |
| `robots` | 这个 URL 允许抓吗 | `decision(url, timeout, ua)` → `allow / deny / unknown` | 调 `curl` |
| `fetch` | 用某套协议栈取一次 | `parse_arm` · `fetch_one` · `save_html` | 调 `curl` / `curl_cffi` |
| `arms` | 这条臂是什么、现在可用吗 | `load` · `parse_list` · `parse_require` · `Registry.filter` | 读仓库外 JSON |
| `render` | 外部内核怎么取一次 | `fetch(url, arm, timeout)` | 调外部 `cmd` / `http` 适配器 |
| `pacing` | 现在能不能发、下次等多久、何时熔断 | `Pacing.wait/observe` · `retry_after` · `default_delay` | 只睡，不发请求 |
| `evidence` | 这页记成什么行 | `evidence_row` · `trial_row` · `write_jsonl` | 写文件（调用方给路径） |
| `policy` | 下一步该用哪条臂 | `Bandit.select/update/snapshot` | 无（内存态） |
| `simulate` | 这个胜率下换策略值不值 | `parse_rates` · `rates_from_trials` · `simulate` | 无（纯离线） |
| `advice` | 证据行说下一步该做什么 | `recommend(rows)` | 无（只读） |

`measure` 只量选择器命中的容器，并跳过 `script` / `style`；整页字节会把导航和页脚算进来，是最常见的指标虚高来源。`content_len` 的返回可能是数字，也可能是 `missing` / `?unsupported` / `-`，调用方不要假定一定是整数。

`pacing` 的时钟与 sleep 可注入，测试不真的等待；`policy` 的随机种子可复现。三者都不联网，可单测。`simulate` 只消费胜率与策略，不发请求，也不产生证据行。`advice` 只消费证据行，输出动作与停止条件，不执行命令；契约见 [agent.md](agent.md)。

## 观测与归类契约

`fetch_one` 返回一个普通字典，除 `body` 外都可直接 JSON 化：

```
arm, status, bytes, final_url, ctype, headers, body, error, elapsed_ms, skipped
```

`classify` 只吃这份观测，返回固定六个键：

```
outcome         ok | degraded | blocked | auth | missing | noise   （与 stats 词表一致）
waf_no          0–6，0 表示不是 WAF
waf_class       机制名称（只描述机制，不指认厂商）
evidence        判据列表
attributable    这次差异能否归到客户端栈
rate_limited    是否速率/配额信号
```

两条编排规则直接写在这里，后续 CLI 不要再各写一遍：

- `attributable: false` 时**不要换客户端臂**。限频、鉴权、内容不存在、链路噪声换栈救不了，先处理那个问题。
- 只有 `blocked` / `degraded` 且可归因时，才值得试下一臂。

## 客户端臂的命名

```
native            curl 原生 TLS/H2 栈
cffi:<profile>    curl_cffi 仿真协议栈，profile 即 impersonate 目标
browser:<engine>  外部浏览器内核（需在仓库外配置 cmd / http 适配器）
```

真实浏览器内核同样是一种客户端臂，但由外部渲染服务承担，不在 `reconlib` 里伪装；注册表、双传输契约与安全边界见 [arms.md](arms.md)。不做验证码求解、不轮换出口、不伪造挑战 cookie、不绕鉴权——这些不因多了一个编排层而改变。

## 编排层：session

`session.ArmSession` 是唯一网络编排：robots → 节流 → 选臂 → 取样 → 归类 → 更新策略 → 产出证据行 / 试验行。它不打印、不写文件，落盘由调用方传 `save` 回调决定。`fetch` / `crawl` / `canary` 共用这一层；能力点只决定“选臂语义”（逐页、按 host 学习、整段固定），不重写合规与节流。

## 加一个新原子

1. 先写 `tests/test_atoms.py` 的用例，明确输入输出，不联网。
2. 新模块只做一件事；纯计算与副作用分开，网络调用集中到 `fetch` / `robots`。
3. 输出用普通字典，字段名与现有契约对齐；不要引入站点常量、厂商名或内部标识。
4. 在 `scripts/self-check.py` 的必备文件与原子契约检查里加一条，否则发布门禁看不见它。

## 边界

- 只测有权测的目标；robots 为 `deny` 时请求根本不发。
- `robots: unknown` 不当作 allow：至少要让“合规状态未知”可见。
- 原子层不保存站点数据；落盘由调用方决定，工作区路径见 [assets/workspace/README.md](../assets/workspace/README.md)。
