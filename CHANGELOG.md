# 更新记录

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；版本号用 `主.次.修订`。

标签与提交不一定一一对应：v1.3.0 与 v1.4.0 的工作合并在同一个提交里，所以仓库里只有 v1.4.0 的标签。

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
