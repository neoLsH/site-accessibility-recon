# CLI 速查

统一入口：`scripts/recon.py <命令> [参数...] [--json]`。参数原样透传给底层脚本，`--json` 把输出收敛成**一个**对象（含 `command` 字段），退出码不变。任何子命令加 `--help` 看完整选项。

## 命令总表

| 命令 | 底层脚本 | 回答什么 | 关键选项 |
|---|---|---|---|
| `doctor` | `preflight.sh` | 环境缺什么、缺了会降级成什么 | — |
| `quick` | `quick.py` | 每条 URL 值不值得继续测 | `--from` `--selector` `--min-content` `--delay` `--arms` `--policy` `--cost-weight` `--prior-strength` `--half-life` `--block-budget` `--policy-in` `--policy-out` `--skip-robots` `--no-cookies` |
| `shape` | `shape.py` | 这一份响应是什么形态 | `--html` `--signals` `--explain` `--save-html` `--min-content` |
| `matrix` | `matrix.py` | 同 UA 换客户端栈是否翻转 | `--selector` `--passes` `--clients` `--arms` `--stop-on-ok` `--cost-weight` `--prior-strength` `--half-life` `--block-budget` `--policy-in` `--policy-out` `--save-html` `--delay` `--cookies` |
| `canary` | `canary.py` | 是否概率门；改前改后是否回归 | `--selector` `--passes` `--delay` `--save` `--compare` `--arms` `--policy` `--arms-file` |
| `stats` | `stats.py` | 按路由拆分的双指标、区间与改前改后差值 | `--compare` `--min-den`；位置参数为 JSONL / canary 基线 JSON |
| `fetch` | `fetch-page.py` | 分层取内容；失败时 WAF 归类 | `--selector` `--impersonate` `--rung` `--arms` `--arms-file` `--require` `--policy` `--cost-weight` `--prior-strength` `--half-life` `--block-budget` `--policy-in` `--policy-out` `--out-dir` `--no-cookies` |
| `crawl` | `crawl.py` | 批量取内容：多臂选举 + 预算 / 熔断护栏 | `--from` `--arms` `--arms-file` `--require` `--policy` `--cost-weight` `--prior-strength` `--half-life` `--block-budget` `--policy-in` `--policy-out` `--seed` `--evidence-out` `--trial-out` `--no-cookies` |
| `bandit` | `bandit.py` | 离线推演选臂策略：请求开销与成功率 | `--arms` / `--trials` / `--rates-json` / `--policy-in` `--pages` `--policies` `--seed` |
| `explore` | `explore.py` | 只读探索规划：后验 → 下一批探测与停止规则 | `--trials` / `--policy-in` `--arms` `--budget` `--max-per-context` `--target-pulls` `--prior-strength` `--half-life` `--cost-weight` `--block-budget` `--seed` |
| `advise` | `advise.py` | 证据行 → 下一步动作（只读，不执行） | `--route` `--json` |
| `surface` | `js-surface.py` | JS 采集面：采了什么、往哪报 | `--html` `--js` `--max-scripts` `--all-origins` |
| `fp` | `fp-echo.py` | 网络栈指纹快照与逐字段 diff | `collect` / `diff` `--via` `--impersonate` `--out` |
| `plan` | `plan.py` | 档案 lint + 从最早失败门推导执行计划 | 档案路径（位置参数） |
| `report` | `report.py` | 从档案与证据表生成 Markdown 报告 | `--evidence` `--out` |
| `check` | `self-check.py` | 仓库结构、脱敏、脚本语法与冒烟 | —（`--tests` 一并跑行为测试） |

`quick` 只在全部 URL 都判为 `可行·HTTP` 时退出 0；出现待定、需浏览器、拦截、授权、不可达等任一结论即退出 1。`matrix` 在全部尝试都没有可行行、或提前 halted 时退出 1。

## 通用约定

- `--json` 只改变输出形态：顶层 `{"command", "ok", ...}`，`ok` 取自退出码，字段名以各脚本为准。
- 退出码：`0` 成功 / `1` 有结论但结论是“有问题”/ `2` 用法或数据格式错误。`shape` 的 `1` 只表示没拿到响应（`unreachable`），其余形态即使不理想也算成功分类；`stats --compare` 的 `1` 表示至少一个指标判为回归。
- 节流是硬约束：`canary` 上限 10 pass、间隔 ≥2s；`quick`/`matrix`/`fetch`/`crawl` 的 URL 间隔默认 5s（`crawl` 回环地址自动降为 0）。连续探测会触发按出口的频率降权，把限频污染成“指纹被识别”。
- 记录写 `<项目根>/recon/<代号>/`，见 [site-profile.md](site-profile.md) 与 [assets/workspace/README.md](../assets/workspace/README.md)。

## 形态判据

`shape` 默认读 [assets/shape-signals.json](../assets/shape-signals.json)，只放通用文案；`--signals <file>` 整体替换，用于站点特有文案（如自研挑战页的固定短语）。

判据文件必须是 JSON，键为 `challenge` / `login_path` / `login_phrase` / `mounts` / `csr_globals`，每项非空字符串数组。前三个与 `csr_globals` 按正则解释（大小写不敏感，`csr_globals` 区分大小写），`mounts` 按字面量匹配元素 id。

`--explain` 打印命中的具体判据与门序，用于回答“为什么判成这个形态”，也方便把误判固化成回归用例。

## 概率门与回归基线

```bash
# 改前：落基线（同 URL、选择器、min-content）
recon canary <url> --selector '#content' --passes 5 --save recon/<代号>/canary.json

# 改后：对同一份基线复测
recon canary <url> --selector '#content' --passes 5 --compare recon/<代号>/canary.json
```

回归判定（任一命中即 `regressed`，退出码 1）：

| 信号 | 阈值 |
|---|---|
| 结论 | 基线 `deterministic-pass` → 现状非 pass |
| 翻转率 | 相对基线上升 > 0.2 |
| 内容中位数 | 相对基线下降 > 20% |
| 噪声占比 | 相对基线上升 > 0.2 |

基线文件含 `schema`、`saved_at`、逐 pass 行与汇总，格式不认识时直接退出 2，不做猜测。

## 双指标结算

`stats` 接受两种输入：JSONL 行，或 `canary --save` 的 JSON（取其 `rows`）。

```json
{"route": "/article/1", "outcome": "ok", "content_len": 812}
```

`outcome` 取值 `ok | degraded | blocked | auth | missing | noise`；缺 `route` 时从 `final_url` / `url` 的路径推断。

| 指标 | 公式 | 说明 |
|---|---|---|
| 可访问性成功率 | `(ok + degraded) / (总数 - noise)` | 发生概率门时会被系统性高估或低估，必须配区间 |
| 内容完整率 | `ok / (ok + degraded)` | 分母是可访问成功的子集，与上一个不同 |
| 噪声 | 单列 | 5xx 与连接失败不计入可访问性分母 |

区间是 Wilson 95%。样本越小越宽，不要引用单 pass 点估计。

### 改前 / 改后

```bash
# 位置参数放改前证据行，--compare 放改后
recon stats recon/<代号>/evidence-before.jsonl --compare recon/<代号>/evidence-after.jsonl
```

同一路由只比两边都出现的样本；只在一侧出现的路由列进 `only_before` / `only_after`，不参与差值。每个指标给改前、改后、差值 `after − before` 与 Newcombe hybrid score 区间（两个独立比例的差），再按区间落判：

| 状态 | 条件 | 含义 |
|---|---|---|
| `improved` | 区间下界 > 0 | 改善，方向与量级都有区间支撑 |
| `regressed` | 区间上界 < 0 | 回归，退出码 1 |
| `inconclusive` | 区间跨 0 | 样本量够但还看不出差别 |
| `insufficient` | 任一侧分母 < `--min-den`（默认 10）或为 0 | 不判方向，`reason` 说明差多少 |

点估计好看不等于改善：区间跨 0 就写“未见显著变化”，别把 +0.13 写成“提升 13 个百分点”。

```json
{"mode": "compare", "baseline": {...}, "current": {...},
 "compare": {"min_den": 10, "overall": {"complete": {"delta": 0.163, "ci95": [0.022, 0.29], "status": "improved"}},
             "routes": [{"route": "/article/1", "access": {...}, "complete": {...}}],
             "only_before": [], "only_after": [], "regressed_routes": [], "regressed": false}}
```

单份输入时 `mode` 为 `single`，结构与此前一致。

## 执行计划

`plan` 先 lint 档案，再取**最早**的失败门（没有失败门则取假设门），按层映射到对策级：

| 层 | 级 | 含义 |
|---|---|---|
| L1 | — | 先修链路，不进入对策 |
| L2 | S2 | 换客户端栈 |
| L3 | S1 | 修请求语义 |
| L4 | S3 | 运行时卫生 |
| L4.5 | S6 | 行为拟人 |
| L5 | S7 | 合法凭证或换数据源 |
| L6 | 按维度 | 服务端策略，按 `dimension` 选 |
| L7 | — | 补样本，不下结论 |

输出固定五步：复现对照 → 概率门定性 → 单变量 → 回归 → 回写档案，并附停止条件。档案里 `plan.level` 与推导不一致只是 WARN：更高成本的一级要等低级失效。

## 客户端臂阶梯

`fetch` 默认按 `native → cffi` 逐级取内容；`--rung both|native|cffi` 可只跑一级（`--no-browser-stack` 仍是 `--rung native` 的别名）。`--arms` 可直接给臂列表（含 `browser:<engine>`），`--policy` 默认 `fixed` 按给定顺序；`--require js` 会硬过滤掉没有 JS 运行时的臂。

- 每级记录 `http / bytes / 内容区 / final_url / outcome / attributable / rate_limited`。
- 成功即停；`attributable: false` 的失败（限频、鉴权、内容不存在、链路噪声）也停——换栈救不了，先处理那个问题。
- 只有可归因的 `blocked` / `degraded` 才继续试下一级；两级都拦且高阶成功时归为 `2 协议栈白名单`。
- 选臂按 `host|路由类|响应类` 累计；`--cost-weight`（默认 0.1，0 关闭）按 5000ms 归一化压低慢臂；`--prior-strength`（默认 4，0 关闭）从相邻上下文借先验，`--half-life`（默认 0）按更新次数遗忘旧样本，`--block-budget`（默认 3，0 关闭）在连续可归因 blocked 后暂停该臂。规则见 [strategy.md](strategy.md)。
- 度量与归类的口径由 `scripts/reconlib/` 原子层提供，见 [atoms.md](atoms.md)。
- 外部浏览器内核不在仓库内。把适配器写进仓库外的 `arms.json`，用 `--arms-file` / `RECON_ARMS_FILE` / `~/.config/site-recon/arms.json` 接入；契约与安全边界见 [arms.md](arms.md)。未配置的臂以 `skipped` 出现且不计请求预算。

## 会话状态

HTTP 臂默认保留本轮目标站点下发的会话状态，按 `(臂, host)` 隔离，存临时目录并在命令结束时删除。

- `crawl` / `fetch` / `quick` 默认开启，`--no-cookies` 关闭；`matrix` 默认关闭，`--cookies` 开启；`canary` 不持有会话。
- 会话值不进证据行、试验行、策略档案与表格输出；不跨进程、不读外部 cookie 或凭证。
- 怀疑“第一页被拦、第二页通过”来自会话而非客户端栈时，用 `--no-cookies` 复跑做对照。
- 机制与各能力点默认值见 [sessions.md](sessions.md)。

## 批量采集

`crawl` 用 `ArmSession` 在客户端臂之间选举，按 host 学习胜率，并强制预算 / 节流 / 熔断：

```bash
recon crawl --from urls.txt --selector '#content' \
    --arms native,cffi:chrome --policy thompson --seed 7 \
    --arms-file ~/.config/site-recon/arms.json --cost-weight 0.1 \
    --evidence-out recon/<代号>/evidence.jsonl --trial-out recon/<代号>/trials.jsonl
```

- 默认护栏：`--max-pages 50`、`--max-requests 150`、`--delay 5`、连续 blocked 3 次熔断。
- `--block-budget`（默认 3，0 关闭）按 `host + 路由类 + 臂` 统计连续可归因 blocked，达阈值暂停该臂；同一上下文候选臂全暂停时该页记 `all-arms-suspended`，不再发请求。
- `--policy` 取 `thompson|ucb1|fixed|round_robin`；`--min-pulls` 控制每臂预热次数；`fixed` / `round_robin` 不受 `--block-budget` 裁剪。
- `--policy-out <path>` 把后验写成 `recon-policy/1` 档案（原子写）；`--policy-in` 先恢复再继续学，四个采集入口可共用同一档案。档案里只有计数，没有响应体 / Cookie / 凭证。
- 证据行与试验行都带 `context`（`host|路由类|响应类`），试验行另带 `cost_ms`。
- 只有可取内容的 `--evidence-out` 能喂 `stats`；`--trial-out` 是 `kind: trial` 的复盘日志，`stats` 会拒绝。
- 退出码 `1` 表示预算或熔断导致提前停止，不是“所有页都可访问”。

规则与误用见 [crawl.md](crawl.md)。

## 离线推演

`bandit` 不联网，用每臂胜率比较策略：

```bash
recon bandit --arms native=0.4,cffi:chrome=0.75 --pages 200 --seed 7
recon bandit --trials recon/<代号>/trials.jsonl --json
recon bandit --rates-json recon/<代号>/rates.json --pages 200 --json
recon bandit --policy-in recon/<代号>/policy.json --pages 200 --json
```

- `--arms` / `--trials` / `--rates-json` / `--policy-in` 四选一；`--trials` 只取可归因、非限频的试验行。
- `--rates-json` 按上下文分组，每组独立选臂、独立更新后验；格式见 [strategy.md](strategy.md)。
- `--policy-in` 直接读上一轮的策略档案，按 `host|路由类|响应类` 分组推演；输出 `source: policy-in`。
- `--policies` 默认跑 `thompson,ucb1,fixed,round_robin`，另附 `best-single` 期望基线。
- 退出码 `0` 表示推演完成，不代表任何策略“已验证有效”。

读法与偏差见 [bandit.md](bandit.md)。

## 探索规划

`explore` 只读试验行或策略档案，把后验变成预算内的下一批探测：

```bash
recon explore --trials recon/<代号>/trials.jsonl --budget 30 --json
recon explore --policy-in recon/<代号>/policy.json --arms native,cffi:chrome --json
```

- `--trials` / `--policy-in` 二选一；`--arms` 只列臂名，用来加入档案里没有的新臂。
- `--budget` 是请求数上限（默认 30），`--max-per-context` 限制单上下文（默认 6），`--target-pulls` 是每臂目标样本（默认 4）。
- `--block-budget`（默认 3）在回放试验行时重演暂停；被暂停的臂不进 `steps`，整段上下文全暂停时进 `hold` 并给出核查提示。策略档案只有累计计数，无法重建连续序列，档案来源不会产生暂停。
- 输出契约 `recon-explore/1`：`contexts`（`best_arm` / `confidence` / `decision` / `suspended_arms`）→ `steps`（`unseen` / `warmup` / `explore`）→ `hold` / `stop` / `notes`。
- 不联网、不发请求、不产生证据行；它只给计划，执行仍走 `crawl` / `quick` / `fetch`。
- 规划顺序、预算算法与偏差见 [explore.md](explore.md)。

## 下一步动作

`advise` 只读证据行，输出一个动作与停止条件，供 Agent / LLM 消费：

```bash
recon advise recon/<代号>/evidence.jsonl --json
recon advise recon/<代号>/evidence.jsonl --route /article/1
```

- 输出契约 `advise/1`：`evidence`（计数快照，含按 `context` 汇总）→ `findings` → `next`（`action` / `layer` / `countermeasure` / `why` / `confidence` / `commands` / `stop`）。
- 同一 host 下 `ok` 与 `blocked` 落在不同上下文时，会给出 `context-split`，提示先分组再换臂。
- 不联网、不发请求、不执行命令；`commands` 只是待人工确认的字符串。
- 只吃证据行；试验行（`kind: trial`）与未知路由退出 2。
- 动作与优先级见 [agent.md](agent.md)。

## 常见组合

```bash
recon quick --from urls.txt --selector '#content'     # 先分流死路
recon shape <url> --explain                          # 认形态
recon canary <url> --passes 5                        # 是否概率门
recon matrix <url> --selector '#content' --passes 3  # 换栈是否翻转
recon fetch <url> --selector '#content'              # 分层取内容
recon crawl --from urls.txt --selector '#content'    # 批量：多臂选举 + 预算护栏
recon bandit --arms native=0.4,cffi:chrome=0.75      # 离线推演选臂策略
recon bandit --rates-json recon/<代号>/rates.json    # 按上下文分组推演
recon explore --trials recon/<代号>/trials.jsonl     # 后验 → 下一批探测预算
recon advise recon/<代号>/evidence.jsonl --json      # 证据行 → 下一步动作
recon plan recon/<代号>/profile.yaml                 # 档案 → 计划
recon stats recon/<代号>/evidence.jsonl              # 双指标
recon report recon/<代号>/profile.yaml --out recon/<代号>/report.md
```

环境要求与降级路径见 [toolbox.md](toolbox.md)；完整走查见 [walkthrough.md](walkthrough.md)。
