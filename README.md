# site-accessibility-recon

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Agent Skills](https://img.shields.io/badge/Agent%20Skills-Standard-green)](https://agentskills.io)
[![npx skills](https://img.shields.io/badge/npx%20skills-Compatible-blue)](https://github.com/vercel-labs/skills)
[![check](https://github.com/neoLsH/site-accessibility-recon/actions/workflows/check.yml/badge.svg)](https://github.com/neoLsH/site-accessibility-recon/actions/workflows/check.yml)

> 一个给 Agent 用的「网页可访问性排查」技能：回答**为什么抓不到 / 抓不全**，而不是教怎么过验证码。

A diagnosable, layered playbook for web access failures — built for agents, portable across Claude Code, Codex CLI, Gemini CLI, Copilot CLI and plain `AGENTS.md`.

**安装**（跨 runtime，自动识别你的 Agent）：

```bash
npx skills add neoLsH/site-accessibility-recon
```

没有 npx 或想手动装：`git clone` 后跑 `./adapters/install.sh <你的 skills 目录>`，见[安装与 Agent 适配](#安装与-agent-适配)。

## 为什么需要它

抓取失败几乎总被归因成“被反爬了”，但真实原因往往在别处：

- 请求根本没到源站（代理没挂上、DNS 泄漏、导航失败）；
- 内容本身已删除 / 审核 / 地域限制（业务状态，不是反爬）；
- 返回了 200，但正文被悄悄抽空（降级软拦截）；
- 边缘按 TLS 栈而不是 UA 判定，而你只测了恰好能过的客户端；
- 自己的自动化客户端启用了某个 CDP 域，留下了可被页面探测的痕迹；
- 指标算错了：把登录墙、导航壳、502 都计成“可用”。

通用工具的默认答案是“加 stealth / 换代理 / 解验证码”。这套 skill 的答案是**先分诊、再分层隔离、最后选最便宜的那扇门**，并且要求每个结论都有可复现的证据。

## 一次真实运行长什么样

拿一个**没有任何风控**的页面做演示（`example.com`），先快筛（下面按列对齐了，实际输出是 TAB 分隔，`--json` 才是机器格式）：

```text
$ ./scripts/recon.py quick https://example.com --selector body
verdict  http  bytes  content  final_url            url                reason
待定      200   577    156      https://example.com/  https://example.com  内容区 156 低于下限 200，且无 CSR 迹象

结论：0/1 条可行，1 条需进一步分析
```

「待定」不是失败，是**信号不足**。接着跑客户端栈矩阵：

```text
$ ./scripts/recon.py matrix https://example.com --selector body --passes 2
client               identity  http  bytes  content  time_s   final_url
# pass 1/2
curl(native)         chrome    200   577    156      0.546660  https://example.com/
curl(native)         safari    200   577    156      0.626322  https://example.com/
curl_cffi(chrome)    chrome    200   577    156      0.977     https://example.com/
curl_cffi(safari)    safari    200   577    156      0.669     https://example.com/
node-fetch(undici)   chrome    200   577    156      0.672     https://example.com/
python-requests      chrome    200   577    156      0.644     https://example.com/
# pass 2/2 —— http / bytes / content 与 pass 1 完全一致，仅耗时略变
```

怎么读这段：

- 六行结果（四个客户端栈 × 两种身份）、两个 pass，**没有一个格翻转**，字节数恒为 577 → 不存在 L2 传输闸门，也没有 L3 身份规则。
- 但 `content` 只有 156，低于下限 → 这时**不能**下「可访问」的结论，只能说「这个栈组能拿到内容，但内容区就这么长」。
- 还缺真实浏览器基线这一行。补齐之前，正确输出就是「待定」。

工具的价值不是给出答案，而是**把「还缺哪一行对照」指出来**——这正是[反模式黑名单](references/anti-patterns.md)第 1 条和第 9 条要防的事。

## 产出物

一次完整排查交付五件东西，其中前三件（P0）缺一件就没有结论：

| 产出 | 作用 |
|---|---|
| 闸门图 | 这次请求卡在哪几扇门，哪些已证通过，哪些还是假设 |
| 证据矩阵 | 客户端栈 × 身份 × 路由 的结果表，含对照组 |
| 站点档案 | `recon/<代号>/profile.yaml`，机器可读，可直接驱动执行侧 |
| 双指标 | 可访问性成功率 / 内容完整率，分开统计（时间受限可后补，但须标注样本范围） |
| 报告 | 复现步骤、根因、最低成本方案、回归方式（同上，可后补；用 `scripts/report.py` 从档案生成） |

## 核心模型

**闸门图**：请求要穿过若干**并联**的闸门，全部通过才算可访问；失败发生在第一个过不去的门。

```
请求 ─► 网络可达 ─► 传输身份 ─► 请求语义 ─► 运行时身份 ─► 业务信任 ─► 内容
        任一不过即终止；下游闸门再完美也拿不到内容
```

**分层**：L0 先定口径，再按七层排查（L1 链路 → L2 传输 → L3 请求 → L4 运行时 → L5 业务 → L6 服务端决策 → L7 观测）。

**三态分诊**：链路失败 / 反爬拦截 / 内容业务状态。三者对策互斥，判错一次后面全错。

**双指标**：可访问性成功率与内容完整率分母不同，禁止合并成一个“成功率”。

**对策阶梯**：S0 换数据源 → S1 请求语义 → S2 客户端栈 → S3 运行时卫生 → S4 出口策略 → S5 内核身份 → S6 行为 → S7 合法鉴权。永远从最便宜的开始。

## 快速开始（约 30 分钟）

```bash
# 0. 环境自检：哪些工具在、缺什么、版本陷阱（LibreSSL 的 JA3 与 OpenSSL 不同）
./scripts/recon.py doctor

# 1. 装上（换成你 Agent 实际读取的 skills 目录）
./adapters/install.sh .claude/skills

# 1.5 落地工作区骨架：所有记录写到 recon/，自带 .gitignore，内容默认不入库
cp -R ./assets/workspace/. ./recon/

# 2. 可行性快筛：一批 URL 各发 1 请求，先分掉合规禁止 / 不可达 / 需授权 / 疑似拦截
./scripts/recon.py quick --from urls.txt --selector '#content'

# 3. 选 5–10 个 URL，按路由分组；用真实浏览器记录内容区长度作为基线
#    注意：量正文容器，不要量整页

# 4. 跑客户端栈矩阵（同 UA 换栈看是否翻转；同时量内容区，区分空壳与真正文）
./scripts/recon.py matrix 'https://example.com/article/1' --selector '#content' --passes 3

# 5. 分诊：链路失败？反爬拦截？内容不存在？
#    见 references/triage-and-gates.md；判进反爬拦截后按 references/waf-classes.md 归类

# 5.1 指纹细化：两个固定读数页——浏览器侧 ippure.com/fingerprint.html，网络侧 tls 回显
./scripts/recon.py fp collect --out baseline.json
./scripts/recon.py fp collect --via cffi --impersonate chrome --out chrome.json
./scripts/recon.py fp diff baseline.json chrome.json

# 5.2 确认路径可行且拿到内容（由低到高换栈，失败给出 WAF 归类）
./scripts/recon.py fetch 'https://example.com/article/1' --selector '#content'

# 5.3 定判定面：它采集了什么、往哪上报（采集≠判定，见 references/detection-surface.md）
./scripts/recon.py surface 'https://example.com/article/1'

# 6. 自底向上隔离，画闸门图，落 recon/<代号>/profile.yaml（模板见 assets/）

# 7. 从档案生成报告，再跑自检，零失败再提交
./scripts/recon.py report recon/forum/profile.yaml --out recon/forum/report.md
./scripts/recon.py check
```

浏览器内指纹快照（含 CDP 泄漏探针）：

```js
// 页面上下文执行
await fingerprintProbe()
// 或 CDP Runtime.evaluate: "(async () => JSON.stringify(await fingerprintProbe()))()"
```

## CLI 入口

七个脚本收敛成一个入口，参数原样透传给底层脚本：

```
recon <命令> [参数...] [--json]
```

| 命令 | 背后 | 说明 |
|---|---|---|
| `doctor` | `preflight.sh` | 环境自检：缺什么、缺了会降级成什么 |
| `quick` | `quick-check.sh` | 可行性快筛：每 URL 1 请求 + robots |
| `matrix` | `client-matrix.sh` | 客户端栈矩阵 + 内容区长度 |
| `fetch` | `fetch-page.py` | 客户端阶梯取内容 + WAF 归类 |
| `surface` | `js-surface.py` | 站点 JS 采集面：静态扫六类采集与上报通道 |
| `fp` | `fp-echo.py` | `fp collect` 采集快照 / `fp diff A B` 比对 |
| `report` | `report.py` | `recon report <档案>` 生成报告 |
| `check` | `self-check.py` | 仓库自检 |

`--json` 把输出收敛成**一个**对象，退出码不变：

```json
{"command": "quick", "ok": false,
 "rows": [{"verdict": "待定", "http": "200", "bytes": "577", "content": "156",
           "final_url": "https://example.com/", "url": "https://example.com",
           "reason": "内容区 156 低于下限 200，且无 CSR 迹象"}]}
```

`fp` → `{"command":"fp","diff":{"total":3,"differ":2,"fields":[...]}}`，`fetch` → `records`，`surface` → `categories` / `suggest`，`check` → `failures`，`report` → `out` / `report`。`ok` 直接取自退出码，所以「有 URL 不可行」「两份指纹有差异」都是 `false`。

想当全局命令用：

```bash
ln -s "$PWD/scripts/recon.py" ~/.local/bin/recon
# 或 alias recon="$PWD/scripts/recon.py"
```

还没做 pip / uvx / npx 打包——先把命令和 JSON 契约用稳，打包器随时能加一层。

## 安装与 Agent 适配

核心只有一份 `SKILL.md`，适配层是薄壳（只做路径与指针）。详见 [adapters/README.md](adapters/README.md)。

```bash
# 推荐：跨 runtime 安装器，自动识别你当前的 Agent
npx skills add neoLsH/site-accessibility-recon

# 或者显式指定目标目录
./adapters/install.sh .claude/skills      # 项目级
./adapters/install.sh ~/.codex/skills     # 用户级
```

| Agent | 方式 |
|---|---|
| Claude Code / Codex CLI / Gemini CLI / Copilot CLI | Agent Skills（`SKILL.md`） |
| Cursor / Windsurf / 其他 | 指向 `AGENTS.md` |
| 任意 Agent | 让它读 `SKILL.md` |

运行时依赖写在 `SKILL.md` 的 `compatibility` 字段里：需要 python3 与 curl，建议装 `curl_cffi`；断网时脚本自动降级为离线复核。装完先跑 `./scripts/recon.py doctor` 看本机缺什么、缺了降级成什么。

## 目录结构

```
SKILL.md                      # 唯一入口（Agent Skills 标准）
AGENTS.md                     # 给只读 AGENTS.md 的 Agent
README.md
CHANGELOG.md                  # 版本记录
CONTRIBUTING.md               # 贡献流程与案例投稿门槛
references/
  triage-and-gates.md         # 三态判据 + 闸门图 + 归因树
  layers.md                   # 七层判据、探针、常见误判
  vectors.md                  # 检测向量全景（每层还有哪些信号）
  evidence-matrix.md          # 实验设计 + 子 agent 编排 + 非确定性
  metrics.md                  # 双指标 + 虚高审计
  countermeasure-ladder.md    # 对策阶梯 + 决策树
  site-profile.md             # 站点档案 schema + 报告生成
  toolbox.md                  # 默认工具链、安装、版本陷阱
  fingerprint-methods.md      # 指纹分析：采集 / 自洽 / 差分 / 归因 + 两个默认读数页
  waf-classes.md              # WAF 六类机制、判据与合规处理路径
  detection-surface.md        # 判定面：客户端采集 vs 服务端评分 + 五个判定实验
  anti-patterns.md            # 反模式黑名单（10 条，含替代做法）
  review-scorecard.md         # 诊断质量评分卡（独立 reviewer 盲评）
  cases/                      # 脱敏案例（推理链）
assets/
  site-profile.template.yaml  # 站点档案模板
  report-template.md          # 报告模板
  workspace/                  # 工作区骨架：复制成 <项目根>/recon/（自带 .gitignore）
    .gitignore                # 除骨架外全部忽略
    README.md                 # 工作区约定：命名、规矩、重来一次
    raw/.gitkeep              # 原始响应体目录
scripts/
  recon.py                    # 统一入口：doctor / quick / matrix / fetch / surface / fp / report / check
  preflight.sh                # 环境自检（必需 / 可选工具与版本）
  quick-check.sh              # 可行性快筛（robots 检查 + 1 请求 + 内容区）
  client-matrix.sh            # 客户端栈矩阵
  fingerprint-probe.js        # 指纹快照 + CDP 泄漏探针
  fp-echo.py                  # 网络栈指纹快照 + 逐字段 diff
  fetch-page.py               # 客户端阶梯取内容 + WAF 归类
  js-surface.py               # 站点 JS 采集面静态扫描
  report.py                   # 从 profile.yaml + evidence.csv 生成报告
  self-check.py               # 结构与脱敏校验（可用于 CI）
adapters/
  README.md
  install.sh
.github/
  workflows/check.yml         # 每次 push / PR 跑 self-check
  PULL_REQUEST_TEMPLATE.md
  ISSUE_TEMPLATE/
LICENSE                       # MIT
```

## 质量校验

```bash
python3 scripts/self-check.py
```

覆盖：必备文件、frontmatter 规范（name / 单行 description / 长度）、SKILL.md 行数（≤150）、未决占位符、相对链接可解析、脱敏红线（站点名 / 厂商名 / 非标准状态码 / Cookie 名 + 仓库外词表里的内网标识）、全部脚本语法（`.sh` / `.js` / `.py`）、工作区骨架（内容被忽略、骨架可入库）、`recon` 入口可运行、**案例结构**（章节齐全、编号规范、置信度已标）、**版本记录**（`CHANGELOG.md` 含当前版本）。退出码非 0 即不可发布。

自检只管仓库合不合规，不管某次诊断站不站得住——后者用 [诊断质量评分卡](references/review-scorecard.md)，由独立 reviewer agent 盲评。

## 案例

案例只保留**机制与实验设计**，去掉了站点标识、域名、厂商产品名与 Cookie 名：

- [两道正交闸门](references/cases/01-orthogonal-gates.md) — SSR 不等于 HTTP 抓取可行；UA 规则与 TLS 栈并联
- [降级 200 与一次性滚动凭证](references/cases/02-degraded-200-and-rolling-cookie.md) — 200 不等于成功；冻结重放 vs 写回链
- [CDP 痕迹、延迟重定向与概率门](references/cases/03-cdp-runtime-leak.md) — 先审计自己；判定前先 settle；指标虚高 2 倍
- [SaaS 风控的两道门](references/cases/04-saas-bot-defense.md) — 干净 IP 必要但非充分
- [挑战页 vs 硬阻断](references/cases/05-challenge-vs-hard-block.md) — 先分类再动手

投稿新案例见 [CONTRIBUTING.md](CONTRIBUTING.md#1-案例投稿最需要)；六段结构与脱敏由 CI 校验。

## 设计原则

1. **诊断优先于对抗。** 不知道卡在哪扇门，任何绕过都是碰运气。
2. **证据优先于叙述。** 每个结论绑定实验；区分 observed / mechanism / efficacy。
3. **分层优于清单。** 分层决定排查顺序，清单只能覆盖已见过的坑。
4. **按需加载。** `SKILL.md` 保持精简，细节下沉到 `references/`，避免把整本手册塞进上下文。
5. **档案优于报告。** 报告给人看，档案给系统用。
6. **承认不可解。** 明确写出“这条路由当前无解”，比无限试错更有价值。
7. **归类不绕过。** WAF 按机制归类是为了选对合规路径，不是提供绕过手法。
8. **记录留在本地。** 工作区放项目根，骨架进仓库、内容不进；要外发就另存脱敏版。
9. **先定判定面再动手。** 边缘、前端采集、服务端评分三者的修复杠杆互斥；采集不等于判定，只有 ablation 能证明因果。

## 边界

仅用于**授权范围内**的可访问性诊断与可靠性工程：

- 遵守 robots、ToS 与站点限速；只测你有权测的目标。
- 不绕过付费墙、登录鉴权、访问控制。
- 不提供验证码求解、账号池、凭证填充方案；验证码是上层没修好的症状，优先修上层。
- 不把站点内部常量（site id、hash、签名密钥）与具体 Cookie 名 / 值写进档案或仓库。

## 参与

案例投稿、判据修正、脚本修复的门槛各不相同，完整规则见 [CONTRIBUTING.md](CONTRIBUTING.md)。三条硬要求：

- 案例走 `references/cases/` 的六段结构（现象 → 误判 / 分诊 → 决定性实验 → 结论 → 可迁移规律 → 置信度）；章节与脱敏由 CI 机器校验。
- 新增结论标置信度；没有证据写 `assumed`，不要伪装成 mechanism。
- **不出现任何内网平台名、内网域名、内部产品代号。** 要拦的词写进 `~/.config/site-recon/denylist.local`（每行一个正则，可用 `RECON_DENY` 指向别处），**不要放进仓库目录**——ignore 挡不住 `git add -f`、打包和整目录拷贝。自检会加载它一起扫；CI 没有这份词表，自动跳过该项。

脱敏是硬要求，`python3 scripts/self-check.py` 必须通过。

## 状态

v1.6.0 — 对齐 Agent Skills 生态的六件事：`SKILL.md` frontmatter 补 `license` / `compatibility` / `allowed-tools`（运行时依赖从 `preflight.sh` 提到激活时可见）；新增 [反模式黑名单](references/anti-patterns.md)（10 条，含替代做法）与 [诊断质量评分卡](references/review-scorecard.md)（五维 100 分，独立 reviewer 盲评，产出 `recon/<代号>/REVIEW.md`）；[evidence-matrix.md](references/evidence-matrix.md) 补子 agent 编排（一个实验一个子 agent、只回报结构化行、缺对照整批作废）；新增 [CONTRIBUTING.md](CONTRIBUTING.md)、PR / issue 模板、`CHANGELOG.md`；自检增加案例结构与版本记录校验。此前各版本见 [CHANGELOG.md](CHANGELOG.md)。`scripts/self-check.py` 作为发布门禁，GitHub Actions 每次 push 自动跑。已知限制：尚未对公开目标做完整的端到端实测，也只在 Claude Code 上验证过加载；其他 Agent 走 `SKILL.md` / `AGENTS.md` 标准读取。

## License

MIT，见 [LICENSE](LICENSE)。
