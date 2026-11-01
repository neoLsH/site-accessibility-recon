# 更新记录

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；版本号用 `主.次.修订`。

标签与提交不一定一一对应：v1.3.0 与 v1.4.0 的工作合并在同一个提交里，所以仓库里只有 v1.4.0 的标签。

## [1.11.0] — 2026-11-02

把批量取内容做成可结算的编排：多臂老虎机在已有客户端臂之间选举，节流与熔断写进代码，产出能直接喂 `stats` 的证据行。

### 新增

- `recon crawl`（`scripts/crawl.py`）：按 host 学习臂的胜率，逐页选臂直到拿到内容、遇到不可归因失败、臂用完或熔断；支持 `thompson` / `ucb1` / `fixed` / `round_robin`，`--seed` 可复现。
- 护栏：`--max-pages 50`、`--max-requests 150`、每 host `--delay 5s`（回环自动 0）、连续 3 次 blocked 熔断；`429` 按 `Retry-After` 或指数退避推迟下一次请求，**不自动重试**同一 URL。
- 两种产物：`--evidence-out` 每页一行（可直接 `stats`）；`--trial-out` 每次尝试一行（`kind: trial` + `trial_outcome`，`stats` 显式拒绝）。
- 原子层扩到七个：`pacing`（节流 / 退避 / 熔断，时钟可注入）、`evidence`（证据行 / 试验行契约与 JSONL 读写）、`policy`（MAB 选举与更新规则）。
- `tests/test_pacing.py`（11 个）、`tests/test_evidence.py`（8 个）、`tests/test_policy.py`（10 个）、`tests/test_crawl.py`（9 个）。
- `references/crawl.md`：选举规则、护栏、两种行的分工与常见误用；`atoms.md` / `cli.md` / `SKILL.md` / `README.md` / `AGENTS.md` 同步。
- 自检新增批量采集护栏：常量上限、`crawl` 复用原子、试验行被 `stats` 拒绝。

### 变更

- `stats.py` 遇到 `kind: trial` 的行显式报错，不再落入“没有 rows 数组”的泛化错误。
- 熔断只封当前 host：余下同 host 的 URL 记为 `circuit-open` 跳过，其他 host 仍可在预算内继续；运行结束退出码为 1。
- `robots: deny` 的 URL 一个请求都不发，记为跳过而非 `blocked`，不污染可访问性分母。

### 已知限制

- 策略只在进程内学习，不跨运行持久化；跨次运行的稳定偏置尚未沉淀。
- 没有并发：并发会把限频信号变成假阳性，暂不引入。
- 排演（离线推演选臂收益）尚未提供，见后续 `bandit` 计划。

## [1.10.0] — 2026-11-02

把单点能力拆成原子：量测、归类、robots、取样各只有一份口径，`fetch` 退化为编排层，为后续批量采集与多臂选举打底。

### 新增

- `scripts/reconlib/`：`measure`（内容区字符数）、`classify`（响应 → 结局与可归因性）、`robots`（合规判定）、`fetch`（客户端臂取样）。除 `fetch` / `robots` 外不联网，便于单测。
- `classify` 固定输出 `outcome` / `waf_no` / `waf_class` / `evidence` / `attributable` / `rate_limited`；`outcome` 与 `stats` 词表一致，可直接落证据行。
- `fetch-page.py --rung both|native|cffi`：只跑一级客户端臂；`--no-browser-stack` 保留为 `--rung native` 的别名。
- `tests/test_atoms.py`（22 个）与 `tests/test_fetch.py`（8 个）：本地 server，无外网。
- `references/atoms.md`：原子契约、客户端臂命名、扩展方式与边界。
- 自检新增原子契约检查：`reconlib` 可导入、度量与归类字段稳定、`outcome` 在 `stats` 词表内、`fetch-page` 已复用原子；脚本语法检查递归覆盖 `scripts/reconlib/`。

### 变更

- `fetch-page.py` 只做编排，不再自带量测 / 归类 / robots 实现。
- 阶梯停止条件收紧：只有可归因的 `blocked` / `degraded` 才试下一臂；限频、鉴权、内容不存在、链路噪声立即停止——换栈救不了，还会把限频污染成“被识别”。
- 链路类退出码 `6 / 7 / 28 / 35` 统一归为“边缘/链路不可达”，“请完成…验证”变体归为交互验证。
- `cli.md`、`waf-classes.md`、`toolbox.md`、`README.md`、`AGENTS.md` 同步原子层与 `--rung`；`SKILL.md` 版本升到 1.10.0。

### 已知限制

- 客户端臂仍只有 `native` 与 `cffi:<profile>`；真实浏览器内核由外部渲染服务承担，不在库内伪装。
- 本版只固化原子与单 URL 阶梯，批量编排与多臂选举尚未接入。
- 归类仍是启发式：单次观测不足以定类，仍需对照与多 pass（见 [metrics.md](references/metrics.md)）。

## [1.9.0] — 2026-11-01

把 A/B 对比从人眼判断变成可结算的输出：改前改后两份证据行，按路由出差值、区间与“样本不足”判定。

### 新增

- `stats --compare <改后行>`：位置参数放改前、`--compare` 放改后，只比两侧都有的路由；逐项给 `before` / `after` / `delta`（`after − before`）与 Newcombe hybrid score 95% 区间（两个独立比例的差）。
- 四态判定：区间下界 > 0 为 `improved`、上界 < 0 为 `regressed`（退出码 1）、跨 0 为 `inconclusive`；任一侧分母低于 `--min-den`（默认 10）或为 0 标 `insufficient`，`reason` 写明差距。
- `only_before` / `only_after`：只在一侧出现的路由单独列出，不参与整体差值，避免用覆盖面冒充提升。
- `--json` 增加 `mode` 字段（`single` / `compare`）与 `baseline` / `current` / `compare` 三个块。
- `assets/example/evidence-before.jsonl` 与 `evidence-after.jsonl`：本地示例的对策前后证据行，走查与自检共用。
- 6 个对比用例：改善与不足、回归退出码、路由集合变化、`--min-den` 覆盖、零分母、人读双表。

### 变更

- 判定用**未取整**的区间边界，展示值四舍五入到 3 位；避免 `-0.0004` 被展示成 0 后误判。
- `references/metrics.md` 增加“改前改后怎么比”，`cli.md` 增加对比选项、四态与 JSON 契约，`walkthrough.md` 增加 5.1 节实跑输出。
- `self-check.py` 的对比冒烟：示例应判整体完整率改善、可访问性未见显著变化、小样本不足、无回归；示例证据行加入必备文件。
- `SKILL.md` 版本升到 1.9.0，流程与 P1 产出改为 `stats --compare`。

### 已知限制

- 仍是描述统计：Newcombe 区间不校正多重比较，按路由同时看多个指标时误报会略高。
- `--min-den` 默认 10 只是工程下限，不保证区间宽度可接受；样本量需求取决于基线比例。
- 两侧路由集合不同时只比交集，新增 / 消失路由的原因仍需人工归因。

## [1.8.0] — 2026-11-01

把结论变成可复现的产物：指标能结算、回归能对比、判据能替换、走查能重跑。

### 新增

- `scripts/stats.py`（`recon stats`）：按路由结算双指标，给 Wilson 95% 区间；噪声单列，两个分母不合并。输入支持 JSONL 证据行或 `{"rows": [...]}`。
- `canary --save` / `--compare`：概率门基线落盘与回归对比，四项阈值（判定翻转、翻转率 +0.2、内容中位数 −20%、噪声 +0.2），任一触发即非零退出。
- `assets/shape-signals.json` + `shape --signals`：形态判据外置，换表即换口径；`shape --explain` 逐条列出命中的信号与判定理由。
- `references/walkthrough.md` + `assets/example/`：九步端到端走查，跑在本地示例站上，不碰真实目标。
- `tests/`：stdlib `unittest` 用例（形态 / 概率门 / 指标 / 计划），用本地 `ThreadingHTTPServer`，无外网、无浏览器。
- `references/cli.md`：全部命令、选项、退出码、基线阈值与指标行格式。

### 变更

- `SKILL.md` 的常用脚本段下沉到 `references/cli.md`，行数降到 130；流程与按需加载接入 `stats`、`--compare`、`--explain`。
- `scripts/recon.py` 增加 `stats` 子命令，`--json` 契约不变。
- `scripts/plan.py` 支持档案里的可选 `base_url`，计划里的示例 URL 不再硬拼 `https://`。
- `scripts/self-check.py` 增加形态判据表、双指标口径、行为测试接入三项检查；脱敏扫描范围扩到示例站、fixture 与全部配置文件，测试脚本一并做语法校验；`--tests` 可直接跑全量用例。
- CI 在自检之后增加行为测试步骤。
- README / AGENTS.md 同步新命令、质量校验与目录结构。

### 已知限制

- `stats` 只做描述统计与区间估计，不做显著性检验；样本量小时区间会很宽。
- `canary` 的对比阈值是工程默认值，站点噪声大时需要按自身分布调，别把阈值当成判据。
- 行为测试只覆盖脚本契约与固定 fixture，不替代真实目标上的端到端探测。

## [1.7.0] — 2026-10-31

把探索与执行两侧补成闭环。

### 新增

- `references/site-shapes.md`：八种页面形态（静态直出 / CSR 空壳 / 挑战页 / 登录墙 / 业务空态 / API 响应 / 网关错误 / 降级 200），每种给识别信号、常见误判、最小探测路径与三态对应。
- `scripts/shape.py`（`recon shape`）：单响应形态分类，1 请求或 `--html` 离线；输出关键信号与下一步，置信度固定 heuristic。
- `scripts/canary.py`（`recon canary`）：同栈多 pass，报翻转率与内容 min/median/max；硬上限 10 pass、默认间隔 3s（最低 2s），基础设施噪声单独计数。
- `references/execution.md`：从档案到执行——lint 分级、层→S 级映射、五步执行、失败回流表、降级链与停手条件、执行红线。
- `scripts/plan.py`（`recon plan`）：档案 lint（证据绑定、`null`/`0`、疑似 Cookie 名/值与站点常量、过期）并按最早失败门推导执行计划与停止条件；`plan.level` 与推导不一致只告警。

### 变更

- `scripts/recon.py` 增加 `shape` / `canary` / `plan` 三个子命令，`--json` 契约不变（`ok` 仍只取自退出码）；`plan` 支持位置参数档案。
- `SKILL.md` 流程与按需加载接入新能力，行数降到 143。
- `scripts/self-check.py` 增加「执行计划冒烟（模板 lint 无 ERROR）」与「canary 节流护栏」两项；必备清单同步扩容。
- README 快速开始、CLI 表、目录结构与 JSON 契约同步；AGENTS.md 同步命令与参考页。

### 已知限制

- `shape` 是启发式：挑战页 / 登录墙的文案判定会有假阳性，必须用真实浏览器基线复核。
- `canary` 只判断同一栈的稳定性，不跨栈；跨栈差异仍用 `matrix`。

## [1.6.0] — 2026-10-29

对齐 Agent Skills 生态的六件事，内容口径不变。

### 新增

- `references/anti-patterns.md`：反模式黑名单，10 条，每条给「为什么错 + 替代做法」。把散在红线、非目标、常见误判里的东西收成一页。
- `references/review-scorecard.md`：诊断质量评分卡。五维 100 分，由**独立 reviewer agent** 盲评，产出 `recon/<代号>/REVIEW.md`。做题的 agent 不给自己打分。
- `CONTRIBUTING.md` 与 PR / issue 模板：案例投稿走 `references/cases/`，机器门禁校验六段结构 + 脱敏。
- `CHANGELOG.md`（本文件）。

### 变更

- `SKILL.md` frontmatter 补 `license` / `compatibility` / `allowed-tools`：运行时依赖（python3、curl、可出网）从 `preflight.sh` 提到激活时可见。
- `references/evidence-matrix.md` 增加子 agent 编排一节：一个实验一个子 agent，只回报结构化行，主 agent 只维护矩阵与闸门图。
- `scripts/self-check.py` 增加案例结构校验（必检清单同步扩容）。
- README 顶部加一行安装命令、badge 与一段真实 CLI 转录。

## [1.5.0] — 2026-10-29

### 新增

- `references/detection-surface.md`：判定面分三个位置——边缘 / 前端采集→后端判定 / 纯后端评分；澄清 TLS/H2 指纹不是 JS 采得到的（握手早于任何脚本）；给出五个判定实验 E1–E5。
- `scripts/js-surface.py`（`recon surface`）：静态扫六类采集面与上报通道，输出「先 hook 哪几个」。

## [1.4.0] — 2026-10-28

### 新增

- `scripts/recon.py`：统一入口 `doctor | quick | matrix | fetch | fp | report | check`，参数透传，`--json` 输出单个对象（含 `command` 字段），退出码不变。

### 变更

- `fp diff --json`、`recon --version` 一并补上；自检新增「入口可运行」。

## [1.3.0] — 2026-10-28

与 v1.4.0 合并在同一提交。

### 新增

- `assets/workspace/`：`recon/` 工作区骨架（`.gitignore` + 约定说明 + `raw/`）。复制到项目根后记录默认不入库，只有壳被跟踪。

### 变更

- 全仓路径从 `sites/<host>` 统一为 `recon/<代号>`。

## [1.2.0] — 2026-10-28

### 新增

- 指纹细化：两个默认读数页（浏览器侧、网络侧）+ `scripts/fp-echo.py` 快照收集与逐字段 diff。
- `references/waf-classes.md`：WAF 六类机制、判据与合规处理路径。
- `scripts/fetch-page.py`：客户端阶梯取内容，失败时给出 WAF 归类。

## [1.1.0] — 2026-10-28

### 新增

- `references/toolbox.md` + `scripts/preflight.sh`：默认工具链与版本陷阱。
- `references/fingerprint-methods.md`：采集 / 自洽 / 差分 / 归因。
- `scripts/quick-check.sh`：可行性快筛（每 URL 1 请求 + robots）。
- `scripts/report.py`：从档案 + 证据表生成报告。

## [1.0.0] — 2026-10-28

### 新增

- 首个版本：闸门图、七层分诊、证据矩阵、双指标、对策阶梯、站点档案、五篇脱敏案例；`scripts/self-check.py` 作为发布门禁，Actions 每次 push 自动跑。
