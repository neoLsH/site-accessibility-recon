# CLI 速查

统一入口：`scripts/recon.py <命令> [参数...] [--json]`。参数原样透传给底层脚本，`--json` 把输出收敛成**一个**对象（含 `command` 字段），退出码不变。任何子命令加 `--help` 看完整选项。

## 命令总表

| 命令 | 底层脚本 | 回答什么 | 关键选项 |
|---|---|---|---|
| `doctor` | `preflight.sh` | 环境缺什么、缺了会降级成什么 | — |
| `quick` | `quick-check.sh` | 每条 URL 值不值得继续测 | `--from` `--selector` `--min-content` `--delay` `--skip-robots` |
| `shape` | `shape.py` | 这一份响应是什么形态 | `--html` `--signals` `--explain` `--save-html` `--min-content` |
| `matrix` | `client-matrix.sh` | 同 UA 换客户端栈是否翻转 | `--selector` `--passes` `--clients` `--save-html` `--delay` |
| `canary` | `canary.py` | 是否概率门；改前改后是否回归 | `--selector` `--passes` `--delay` `--save` `--compare` |
| `stats` | `stats.py` | 按路由拆分的双指标、区间与改前改后差值 | `--compare` `--min-den`；位置参数为 JSONL / canary 基线 JSON |
| `fetch` | `fetch-page.py` | 分层取内容；失败时 WAF 归类 | `--selector` `--impersonate` `--no-browser-stack` `--out-dir` |
| `surface` | `js-surface.py` | JS 采集面：采了什么、往哪报 | `--html` `--js` `--max-scripts` `--all-origins` |
| `fp` | `fp-echo.py` | 网络栈指纹快照与逐字段 diff | `collect` / `diff` `--via` `--impersonate` `--out` |
| `plan` | `plan.py` | 档案 lint + 从最早失败门推导执行计划 | 档案路径（位置参数） |
| `report` | `report.py` | 从档案与证据表生成 Markdown 报告 | `--evidence` `--out` |
| `check` | `self-check.py` | 仓库结构、脱敏、脚本语法与冒烟 | —（`--tests` 一并跑行为测试） |

## 通用约定

- `--json` 只改变输出形态：顶层 `{"command", "ok", ...}`，`ok` 取自退出码，字段名以各脚本为准。
- 退出码：`0` 成功 / `1` 有结论但结论是“有问题”/ `2` 用法或数据格式错误。`shape` 的 `1` 只表示没拿到响应（`unreachable`），其余形态即使不理想也算成功分类；`stats --compare` 的 `1` 表示至少一个指标判为回归。
- 节流是硬约束：`canary` 上限 10 pass、间隔 ≥2s；`quick`/`matrix`/`fetch` 的 URL 间隔默认 5s。连续探测会触发按出口的频率降权，把限频污染成“指纹被识别”。
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

## 常见组合

```bash
recon quick --from urls.txt --selector '#content'     # 先分流死路
recon shape <url> --explain                          # 认形态
recon canary <url> --passes 5                        # 是否概率门
recon matrix <url> --selector '#content' --passes 3  # 换栈是否翻转
recon fetch <url> --selector '#content'              # 分层取内容
recon plan recon/<代号>/profile.yaml                 # 档案 → 计划
recon stats recon/<代号>/evidence.jsonl              # 双指标
recon report recon/<代号>/profile.yaml --out recon/<代号>/report.md
```

环境要求与降级路径见 [toolbox.md](toolbox.md)；完整走查见 [walkthrough.md](walkthrough.md)。
