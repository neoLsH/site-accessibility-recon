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

## 一套能力，四种形态

同一组原子对外有四个交付面，口径只有一份：

| 形态 | 入口 | 回答什么 |
|---|---|---|
| Skill | [SKILL.md](SKILL.md) + `references/` | 先做什么、怎么判、什么时候停 |
| CLI | `scripts/recon.py <命令>` | 单点执行，人读表 + `--json` 单对象 |
| Agent | `recon advise --json` | 证据行 → 唯一动作、层、理由与停止条件 |
| LLM | `advise/1` + 提示骨架 | 解释与编排，不改写计数 |

分工、共享契约与扩展方式见 [forms.md](references/forms.md)；Agent 与 LLM 的接入细节见 [agent.md](references/agent.md)。

## 一次真实运行长什么样

拿一个**没有任何风控**的页面做演示（`example.com`），先快筛（下面按列对齐了，实际输出是 TAB 分隔，`--json` 才是机器格式）：

```text
$ ./scripts/recon.py quick https://example.com --selector body
verdict  http  bytes  content  final_url            url                reason
待定      200   577    156      https://example.com/  https://example.com  内容区 156 低于下限 200，且无 CSR 迹象

结论：0/1 条可直接由 HTTP 取得内容，1 条需浏览器实测或进一步分析
```

（首次运行会附判定词表；上面从略。）「待定」不是失败，是**信号不足**。接着跑客户端栈矩阵：

```text
$ ./scripts/recon.py matrix https://example.com --selector body --passes 2 --delay 0
client       identity  http  bytes  content  time_s  final_url
native       -         200   577    156      0.545   https://example.com/
cffi:chrome  chrome    200   577    156      0.968   https://example.com/
native       -         200   577    156      0.640   https://example.com/
cffi:chrome  chrome    200   577    156      0.794   https://example.com/
```

默认臂是 `native,cffi:chrome`；装了更多外部内核时用 `--arms` / `--arms-file` 扩列。

怎么读这段：

- 两条臂、两个 pass，**没有一个格翻转**，字节数恒为 577 → 不存在 L2 传输闸门。
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
| 双指标 | 可访问性成功率 / 内容完整率，按路由拆分并用 `stats` 出区间；改前改后跑 `stats --compare` 看差值与样本量（时间受限可后补，但须标注样本范围） |
| 报告 | 复现步骤、根因、最低成本方案、回归方式（同上，可后补；用 `scripts/report.py` 从档案生成） |

## 核心模型

**闸门图**：请求要穿过若干**并联**的闸门，全部通过才算可访问；失败发生在第一个过不去的门。

```
请求 ─► 网络可达 ─► 传输身份 ─► 请求语义 ─► 运行时身份 ─► 业务信任 ─► 内容
        任一不过即终止；下游闸门再完美也拿不到内容
```

**分层**：L0 先定口径，再按七层排查（L1 链路 → L2 传输 → L3 请求 → L4 运行时 → L5 业务 → L6 服务端决策 → L7 观测）。

**三态分诊**：链路失败 / 反爬拦截 / 内容业务状态。三者对策互斥，判错一次后面全错。

**双指标**：可访问性成功率与内容完整率分母不同，禁止合并成一个“成功率”；`stats` 负责噪声剔除、路由拆分与区间。

**改前改后**：`stats before.jsonl --compare after.jsonl` 只比两边都有的路由，出差值与 Newcombe 区间；区间跨 0 写“未见显著变化”，任一侧分母低于 10 标 `insufficient`，任一路由判回归即退出 1。

**回归基线**：改前 `canary --save` 落基线，改后 `--compare` 对同一份基线复测；结论翻转、翻转率上升、内容中位数下降任一命中即判回归。

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

# 2.5 认页面形态（1 请求）：静态 / CSR 空壳 / 挑战页 / 登录墙 / 业务空态
./scripts/recon.py shape 'https://example.com/article/1'

# 3. 选 5–10 个 URL，按路由分组；用真实浏览器记录内容区长度作为基线
#    注意：量正文容器，不要量整页

# 4. 跑客户端栈矩阵（同 UA 换栈看是否翻转；同时量内容区，区分空壳与真正文）
./scripts/recon.py matrix 'https://example.com/article/1' --selector '#content' --passes 3

# 4.5 概率门定性 + 落基线：同栈多 pass，翻转率 >0 就按区间报，别报单点
./scripts/recon.py canary 'https://example.com/article/1' --selector '#content' --passes 5 \
  --save recon/forum/canary.json

# 4.6 双指标结算：证据行按路由拆分，噪声单列，附 Wilson 区间
./scripts/recon.py stats recon/forum/evidence.jsonl
# 4.7 改前改后：同一批路由出差值 + Newcombe 区间，小样本判 insufficient
./scripts/recon.py stats recon/forum/evidence-before.jsonl \
  --compare recon/forum/evidence-after.jsonl

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

# 7. 档案 lint + 执行计划：卡哪扇门、下一步做哪一级、怎么验证；再生成报告与自检
./scripts/recon.py plan recon/forum/profile.yaml
./scripts/recon.py report recon/forum/profile.yaml --out recon/forum/report.md
./scripts/recon.py canary 'https://example.com/article/1' --selector '#content' \
  --passes 5 --compare recon/forum/canary.json      # 改后回归对比
./scripts/recon.py stats recon/forum/evidence-before.jsonl \
  --compare recon/forum/evidence-after.jsonl        # 改后双指标差值
./scripts/recon.py check
```

想先看完整链路而不碰真实站点：按 [端到端走查](references/walkthrough.md) 用仓库自带的本地示例站跑一遍。

浏览器内指纹快照（含 CDP 泄漏探针）：

```js
// 页面上下文执行
await fingerprintProbe()
// 或 CDP Runtime.evaluate: "(async () => JSON.stringify(await fingerprintProbe()))()"
```

## CLI 入口

所有脚本收敛成一个入口，参数原样透传给底层脚本：

```
recon <命令> [参数...] [--json]
```

| 命令 | 背后 | 说明 |
|---|---|---|
| `doctor` | `preflight.sh` | 环境自检：缺什么、缺了会降级成什么 |
| `quick` | `quick.py` | 可行性快筛：每 URL 1 请求 + robots，跨 URL 学选臂 |
| `shape` | `shape.py` | 单响应形态分类：静态 / CSR 空壳 / 挑战 / 登录墙 / 业务空态 |
| `matrix` | `matrix.py` | 客户端栈矩阵 + 内容区长度，默认穷举全部臂 |
| `canary` | `canary.py` | 概率门定性：同栈多 pass，翻转率与内容区间；`--save` / `--compare` 做基线回归 |
| `stats` | `stats.py` | 双指标结算：按路由拆分并附 Wilson 区间；`--compare` 出改前改后差值与 Newcombe 区间 |
| `fetch` | `fetch-page.py` | 客户端臂阶梯取内容 + WAF 归类；`--rung` 只跑一级 |
| `crawl` | `crawl.py` | 批量取内容：多臂选举客户端臂，预算 / 节流 / 熔断护栏，产出证据行与试验行；`--policy-in/out` 跨轮热启动 |
| `bandit` | `bandit.py` | 离线推演选臂策略：给定胜率比较请求开销与成功率，不发请求；`--rates-json` 分组、`--policy-in` 读策略档案 |
| `explore` | `explore.py` | 只读探索规划：试验行 / 策略档案 → 预算内的下一批探测、未见臂与停止规则，不发请求 |
| `advise` | `advise.py` | 证据行 → 下一步动作：只读、带停止条件，`advise/1` 契约供 Agent / LLM 消费 |
| `surface` | `js-surface.py` | 站点 JS 采集面：静态扫六类采集与上报通道 |
| `fp` | `fp-echo.py` | `fp collect` 采集快照 / `fp diff A B` 比对 |
| `plan` | `plan.py` | 档案 lint + 执行计划：最早失败门 → S 级与验证步骤 |
| `report` | `report.py` | `recon report <档案>` 生成报告 |
| `check` | `self-check.py` | 仓库自检 |

`--json` 把输出收敛成**一个**对象，退出码不变：

```json
{"command": "quick", "ok": false,
 "rows": [{"verdict": "待定", "http": "200", "bytes": "577", "content": "156",
           "final_url": "https://example.com/", "url": "https://example.com",
           "reason": "内容区 156 低于下限 200，且无 CSR 迹象"}]}
```

`fp` → `{"command":"fp","diff":{"total":3,"differ":2,"fields":[...]}}`，`fetch` → `records`，`crawl` → `summary` / `pages`，`bandit` → `rates` / `rows`，`explore` → `contexts` / `steps` / `hold` / `stop`，`advise` → `schema` / `evidence` / `findings` / `next`，`surface` → `categories` / `suggest`，`shape` → `shape` / `signals` / `explain` / `next`，`canary` → `verdict` / `flip_rate` / `content` / `compare`，`stats` → `overall` / `groups`（`--compare` 时另有 `mode` / `baseline` / `current` / `compare`），`plan` → `lint` / `blocking` / `steps`，`check` → `failures`，`report` → `out` / `report`。`ok` 直接取自退出码，所以「有 URL 不可行」「两份指纹有差异」「这次抓取翻转了」「相对基线回归了」「预算/熔断提前停止」都是 `false`。

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
  metrics.md                  # 双指标 + 改前改后对比 + 虚高审计
  countermeasure-ladder.md    # 对策阶梯 + 决策树
  site-profile.md             # 站点档案 schema + 报告生成
  toolbox.md                  # 默认工具链、安装、版本陷阱
  fingerprint-methods.md      # 指纹分析：采集 / 自洽 / 差分 / 归因 + 两个默认读数页
  waf-classes.md              # WAF 六类机制、判据与合规处理路径
  detection-surface.md        # 判定面：客户端采集 vs 服务端评分 + 五个判定实验
  anti-patterns.md            # 反模式黑名单（10 条，含替代做法）
  review-scorecard.md         # 诊断质量评分卡（独立 reviewer 盲评）
  site-shapes.md              # 八种页面形态：识别信号、误判与最小探测路径
  execution.md                # 从档案到执行：lint 分级、层→级、五步、失败回流
  cli.md                      # 命令、选项、判据、退出码、基线阈值与指标行格式
  atoms.md                    # 原子层：度量 / 归类 / 合规 / 取样 / 节奏 / 证据 / 选举
  arms.md                     # 客户端臂：注册表、外部内核契约、能力过滤与安全边界
  strategy.md                 # 选臂上下文、奖励与成本、各能力点的选臂语义与读法
  crawl.md                    # 批量采集：多臂选举、护栏、证据行与试验行
  bandit.md                   # 离线推演：胜率来源、指标读法、偏差与限制
  explore.md                  # 只读探索规划：输入、预算分配、读法与停止规则
  agent.md                    # Agent / LLM 接口：advise/1 契约、动作优先级、提示骨架与护栏
  forms.md                    # 四种形态：Skill / CLI / Agent / LLM 的分工、共享契约与扩展方式
  walkthrough.md              # 端到端走查（本地示例站，不碰真实目标）
  cases/                      # 脱敏案例（推理链）
assets/
  site-profile.template.yaml  # 站点档案模板
  report-template.md          # 报告模板
  shape-signals.json          # 形态判据表（可用 --signals 整体替换）
  example/                    # 走查用本地示例站、示例档案与证据行
  workspace/                  # 工作区骨架：复制成 <项目根>/recon/（自带 .gitignore）
    .gitignore                # 除骨架外全部忽略
    README.md                 # 工作区约定：命名、规矩、重来一次
    raw/.gitkeep              # 原始响应体目录
scripts/
  recon.py                    # 统一入口：doctor / quick / shape / matrix / canary / stats / fetch / crawl / bandit / explore / advise / surface / fp / plan / report / check
  preflight.sh                # 环境自检（必需 / 可选工具与版本）
  quick-check.sh              # 薄壳：转调 quick.py（保留旧入口）
  quick.py                    # 可行性快筛（robots + 1 请求/URL + 跨 URL 选臂）
  shape.py                    # 单响应形态分类（启发式，可 --html 离线 / --explain / --signals）
  client-matrix.sh            # 薄壳：转调 matrix.py（保留旧入口）
  matrix.py                   # 客户端栈矩阵（默认穷举臂，可多 pass / 停在上限）
  canary.py                   # 概率门：同栈多 pass 的翻转率与内容区间；基线保存与对比
  stats.py                    # 双指标结算：按路由拆分 + Wilson 区间；--compare 出差值 + Newcombe 区间
  fingerprint-probe.js        # 指纹快照 + CDP 泄漏探针
  fp-echo.py                  # 网络栈指纹快照 + 逐字段 diff
  fetch-page.py               # 客户端臂阶梯取内容 + WAF 归类（--rung 只跑一级）
  crawl.py                    # 批量取内容：MAB 选臂 + 预算 / 节流 / 熔断 + 证据行
  bandit.py                   # 离线推演选臂策略：胜率 → 请求开销与成功率（不发请求）
  explore.py                  # 只读探索规划：后验 → 下一批探测（不发请求）
  advise.py                   # 证据行 → 下一步动作：只读、带停止条件（advise/1 契约）
  reconlib/                   # 原子层：measure / classify / robots / fetch / arms / render / pacing / evidence / context / policy / policy_store / simulate / explore / advice；session 是编排层（唯一口径来源）
  js-surface.py               # 站点 JS 采集面静态扫描
  plan.py                     # 档案 lint + 执行计划推导
  report.py                   # 从 profile.yaml + evidence.csv 生成报告
  self-check.py               # 结构、脱敏、脚本语法与冒烟校验（--tests 一并跑行为测试）
tests/                        # stdlib unittest：原子 / 取值 / 形态 / 概率门 / 指标 / 计划 / 节奏 / 证据 / 上下文 / 选举 / 先验与档案 / 批量采集 / 推演 / 建议 / 臂注册表 / 外部内核 / 编排（本地 server，无外网）
adapters/
  README.md
  install.sh
.github/
  workflows/check.yml         # 每次 push / PR 跑 self-check + 行为测试
  PULL_REQUEST_TEMPLATE.md
  ISSUE_TEMPLATE/
LICENSE                       # MIT
```

## 质量校验

```bash
python3 scripts/self-check.py            # 结构 / 脱敏 / 脚本语法 / 冒烟
python3 -m unittest discover -s tests -v # 行为测试（本地 fixture，无外网）
```

覆盖：必备文件、frontmatter 规范（name / 单行 description / 长度）、SKILL.md 行数（≤150）、未决占位符、相对链接可解析、脱敏红线（站点名 / 厂商名 / 非标准状态码 / Cookie 名 + 仓库外词表里的内网标识，扫描范围含示例站与 fixture）、全部脚本与测试语法（`.sh` / `.js` / `.py`）、工作区骨架（内容被忽略、骨架可入库）、`recon` 入口可运行、**案例结构**（章节齐全、编号规范、置信度已标）、**版本记录**（`CHANGELOG.md` 含当前版本）、**执行计划可运行**（模板与示例档案 lint 均无 ERROR）、**节流护栏**（`canary` 的 pass 上限与默认间隔）、**形态判据表**（可解析且被 `shape.py` 默认加载）、**双指标口径**（示例证据行结算为 6/7 可访问、4/6 完整）、**改前改后对比**（示例整体仅完整率改善、小样本判不足、无回归）、**原子层契约**（`reconlib` 可导入、度量与归类口径稳定、`fetch-page` 复用）、**批量采集护栏**（`crawl` 的 pages / requests / 间隔 / 熔断常量受限，试验行被 `stats` 拒绝）、**离线推演**（`bandit` 不发请求、同 seed 可复现，示例试验行估出 0.40 / 0.75）、**策略上下文**（上下文键与回退链稳定、`quick` / `matrix` 薄壳转调 Python、`bandit --rates-json` 按上下文分组）、**策略记忆**（分层先验 / 遗忘 / 诊断成立，`recon-policy/1` 档案原子写与往返一致，四个采集入口与 `bandit --policy-in` 全部接入）、**探索规划**（`explore` 只读、预算封顶、未试臂可识别、同输入可复现）、**建议层**（`advise` 只读、示例判补样本、噪声剔出分母、试验行被拒、建议带停止条件）、**行为测试接入**（`tests/` 用例已进 CI）。`python3 scripts/self-check.py --tests` 会顺带跑一遍行为测试。退出码非 0 即不可发布。

行为测试用 stdlib `unittest` + 本地 `ThreadingHTTPServer`，不访问外网、不需要浏览器；CI 每次 push / PR 各跑一遍。

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
10. **执行从档案推导。** 计划不手写：`plan` 先 lint 档案，再按最早失败的门给出动作与判据；跳过 lint 的计划会把错误结论放大成线上改动。
11. **可复现胜过结论。** 判据写成数据（`shape-signals.json`）、指标写成可结算的行（`stats`）、改前改后写成可比较的两份行（`stats --compare`）、回归写成可对比的基线（`canary --compare`）；走查在本地示例站跑通，别人才有办法重跑你的结论。

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

v1.17.0 — 把后验变成可执行的探索预算：新增只读规划器 `recon explore`（[explore.py](scripts/explore.py) + [explore.md](references/explore.md)），输入试验行或 `recon-policy/1` 档案，按上下文列出未见臂 / 补样本 / 探索次优臂，在 `--budget` 与 `--max-per-context` 内贪心分配请求，并给出 `hold`、`stop` 与理由；同输入可复现、不联网、不发请求、不产生证据行。`advise` 的换臂与补样本动作补上 `--trial-out` + `explore` 命令。新增 16 个用例与自检探索规划门禁。已知限制：规划只按请求数计预算，不含耗时与 robots 请求；`pages` 是样本权重而非精确页数。v1.16.0 — 把选臂从“本轮学”推进到“跨轮学”：`ContextualBandit` 增加分层经验先验（`--prior-strength` 默认 4，从相邻上下文借伪计数但不改本地 pulls）、指数遗忘（`--half-life` 默认 0 关闭）、Wilson `ci95`、确定性蒙特卡洛 `p_best` 与 `exploit/explore` 诊断；新增 `reconlib/policy_store.py`（`recon-policy/1`、原子写、结构校验），`crawl` / `fetch` / `quick` / `matrix` 全部支持 `--policy-in` / `--policy-out`，`bandit --policy-in` 可按上下文分桶推演。新增 `tests/test_policy_prior.py`（10 个）与 `tests/test_policy_store.py`（7 个），自检新增策略记忆门禁。v1.15.0 — 把选臂从“按 host 学胜率”推进到“按上下文 + 成本学”：新增 `reconlib/context.py`（`host|路由类|响应类` 键与回退链）与 `ContextualBandit`（`ok=1.0` / `degraded=0.5` / `blocked=0.0`；`auth` / `missing` / `noise` / 限频不更新；按 5000ms 归一化的成本惩罚，`--cost-weight` 默认 0.1、0 关闭）。`fetch` / `crawl` / `quick` 全部接入，证据行与试验行带 `context`，试验行另带 `cost_ms`；`quick`（[quick.py](scripts/quick.py)）与 `matrix`（[matrix.py](scripts/matrix.py)）改为 Python 实现，旧 `.sh` 变薄壳，`quick` 每条 URL 只发一个请求、跨 URL 学习，`matrix` 默认穷举；`bandit --rates-json` 支持按上下文分组推演；`advise` 增加 `context-split` 差异发现。新增 [strategy.md](references/strategy.md)，同步 `SKILL.md`、`README.md`、`AGENTS.md`、`atoms.md`、`arms.md`、`cli.md`、`crawl.md`、`bandit.md`、`forms.md`、`agent.md`、`toolbox.md`。v1.14.0 — 把「客户端臂」从两个硬编码名字变成可扩展注册表：内置 `native` / `cffi:<profile>` / `browser:<engine>`，外部浏览器内核通过 `cmd` 或 `http` 适配器接入（仓库不内置），配置放在仓库外（`--arms-file` / `RECON_ARMS_FILE` / `~/.config/site-recon/arms.json`）。新增 [arms.md](references/arms.md)、`reconlib/arms.py`、`render.py` 与统一编排层 `session.py`（robots → 节流 → 选臂 → 取样 → 归类 → 更新 → 行）；`fetch` / `crawl` / `canary` 全部接入，`canary` 仍在整个 pass 序列固定一条臂以保翻转率口径。未配置的外部臂以 `skipped` 出现且不计请求预算；外部命令不经 shell，响应体上限 16 MiB。默认行为与输出字段不变。v1.13.0 — 把“下一步做什么”和“能力以什么形态交付”补齐：新增 `recon advise`（[advise.py](scripts/advise.py) + [agent.md](references/agent.md)），把证据行翻译成唯一动作、层、理由、置信度与停止条件（`advise/1` 契约），只读、不联网、不执行命令、拒绝试验行；原子层新增 `advice`（[atoms.md](references/atoms.md)）；新增 [forms.md](references/forms.md) 说明 Skill / CLI / Agent / LLM 四种形态的分工与共享契约；新增 18 个用例与自检建议层冒烟。v1.12.0 — 把“换不换选举策略”变成离线可算的问题：新增 `recon bandit`（[bandit.py](scripts/bandit.py) + [bandit.md](references/bandit.md)），用每臂胜率推演 `thompson` / `ucb1` / `fixed` / `round_robin` 的请求开销与成功率，并给出 `best-single` 期望基线；胜率可手给（`--arms`）或从 `crawl` 的试验行估计（`--trials`，只取可归因、非限频尝试）。原子层新增 `simulate`（[atoms.md](references/atoms.md)），同 seed 可复现、不发请求；新增示例试验行与 19 个用例；自检校验推演离线性与示例口径。v1.11.0 — 把批量取内容变成可结算的编排：新增 `recon crawl`（[crawl.py](scripts/crawl.py) + [crawl.md](references/crawl.md)），用多臂老虎机在已有客户端臂之间选举（`thompson` / `ucb1` / `fixed` / `round_robin`，按 host 学习胜率），强制 `--max-pages 50` / `--max-requests 150` / 每 host 5s 间隔 / 连续 3 次 blocked 熔断；`429` 只推迟下一次请求、不自动重试；`robots: deny` 的 URL 一个请求都不发。原子层扩到七个（[atoms.md](references/atoms.md)）：`pacing` 管节流退避熔断、`evidence` 管证据行与试验行、`policy` 管选臂，`stats` 显式拒绝 `kind: trial` 的试验行，避免把尝试次数当页面数。自检新增批量采集护栏与试验行隔离。v1.10.0 — 把单点能力拆成原子：新增 `scripts/reconlib/`（[atoms.md](references/atoms.md)），`measure` 量内容区、`classify` 归结局与可归因性、`robots` 判合规、`fetch` 管客户端臂；`fetch-page.py` 改为只做阶梯编排，并新增 `--rung both|native|cffi`。归类新增 `attributable` / `rate_limited`：不可归因的失败（限频、鉴权、内容不存在、链路噪声）不再继续换栈。自检把原子契约与 `reconlib` 语法纳入门禁。v1.9.0 — 把 A/B 对比从人眼判断变成可结算的输出：`recon stats <改前行> --compare <改后行>`（[stats.py](scripts/stats.py) + [metrics.md](references/metrics.md)）只比两侧都有的路由，逐项给改前、改后、差值与 Newcombe hybrid score 95% 区间（两个独立比例的差），按**未取整**区间落 `improved` / `regressed` / `inconclusive`；任一侧分母低于 `--min-den`（默认 10）或为 0 时标 `insufficient` 并给出差距，不拿小样本下方向；任一路由判回归即退出 1，只在一侧出现的路由单独列出、不进差值。新增改前 / 改后示例证据行与 6 个对比用例，自检增加对比冒烟（整体改善、小样本判不足、无回归），[cli.md](references/cli.md)、[walkthrough.md](references/walkthrough.md) 补输出与读法。v1.8.0 — 把「结论」变成可复现的产物：`recon stats`（[stats.py](scripts/stats.py) + [metrics.md](references/metrics.md)）按路由结算双指标并给 Wilson 95% 区间，噪声单列、两个分母不合并；`recon canary --save/--compare`（[canary.py](scripts/canary.py)）把回归固化成基线与四项阈值（判定翻转、翻转率 +0.2、内容中位数 −20%、噪声 +0.2）；`recon shape` 的判据外置到 [shape-signals.json](assets/shape-signals.json)，新增 `--explain` 与 `--signals`；新增 [references/walkthrough.md](references/walkthrough.md) 与 [assets/example/](assets/example/) 本地示例站，九步走查不碰真实目标；常用命令与判据下沉到 [references/cli.md](references/cli.md)，`SKILL.md` 降到 130 行；新增 `tests/`（stdlib unittest，本地 server，无外网）并接进 CI；自检增加形态判据表、双指标口径与测试接入三项。v1.7.0 — 把探索与执行两侧补成闭环：`recon shape`（[site-shapes.md](references/site-shapes.md)）先认八种页面形态，避免用错判据；`recon canary`（[canary.py](scripts/canary.py)）用同栈多 pass 的翻转率区分确定性闸门与概率门，硬上限 10 pass、间隔不低于 2s；`recon plan`（[plan.py](scripts/plan.py) + [execution.md](references/execution.md)）先 lint 档案（证据绑定、`null`/`0`、疑似站点常量、过期），再按最早失败的门推导 S 级、五步动作与停止条件。自检新增执行计划冒烟与节流护栏两项。v1.6.0 — 对齐 Agent Skills 生态的六件事：`SKILL.md` frontmatter 补 `license` / `compatibility` / `allowed-tools`（运行时依赖从 `preflight.sh` 提到激活时可见）；新增 [反模式黑名单](references/anti-patterns.md)（10 条，含替代做法）与 [诊断质量评分卡](references/review-scorecard.md)（五维 100 分，独立 reviewer 盲评，产出 `recon/<代号>/REVIEW.md`）；[evidence-matrix.md](references/evidence-matrix.md) 补子 agent 编排（一个实验一个子 agent、只回报结构化行、缺对照整批作废）；新增 [CONTRIBUTING.md](CONTRIBUTING.md)、PR / issue 模板、`CHANGELOG.md`；自检增加案例结构与版本记录校验。此前各版本见 [CHANGELOG.md](CHANGELOG.md)。`scripts/self-check.py` 作为发布门禁，GitHub Actions 每次 push 自动跑自检与行为测试。已知限制：走查与行为测试都跑在本地示例站上，尚未对公开目标做完整的端到端实测，也只在 Claude Code 上验证过加载；其他 Agent 走 `SKILL.md` / `AGENTS.md` 标准读取。Agent 形态只提供 `advise/1` 契约与 CLI，不含常驻调度器，循环与人工确认由宿主负责。外部内核的质量取决于适配器：仓库只校验 `cmd` / `http` 契约（JSON、16 MiB、错误归 `noise`），不保证渲染结果、登录态或出口与真实浏览器一致。上下文只按 URL 路径与首次结局粗分，可能把相似页面合并统计；成本只用于同类页面内排序，不跨上下文比较绝对耗时。分层先验会把相邻上下文的胜率带进来，站点内差异很大时应关掉；遗忘只在开启后生效，默认保留全部历史。策略档案只含计数与配置，且不覆盖本轮 CLI 参数；跨目标复用前先确认 host / 路由键仍适用。

## License

MIT，见 [LICENSE](LICENSE)。
