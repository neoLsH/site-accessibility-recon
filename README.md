# site-accessibility-recon

> 一个给 Agent 用的「网页可访问性排查」技能：回答**为什么抓不到 / 抓不全**，而不是教怎么过验证码。

A diagnosable, layered playbook for web access failures — built for agents, portable across Claude Code, Codex CLI, Gemini CLI, Copilot CLI and plain `AGENTS.md`.

## 为什么需要它

抓取失败几乎总被归因成“被反爬了”，但真实原因往往在别处：

- 请求根本没到源站（代理没挂上、DNS 泄漏、导航失败）；
- 内容本身已删除 / 审核 / 地域限制（业务状态，不是反爬）；
- 返回了 200，但正文被悄悄抽空（降级软拦截）；
- 边缘按 TLS 栈而不是 UA 判定，而你只测了恰好能过的客户端；
- 自己的自动化客户端启用了某个 CDP 域，留下了可被页面探测的痕迹；
- 指标算错了：把登录墙、导航壳、502 都计成“可用”。

通用工具的默认答案是“加 stealth / 换代理 / 解验证码”。这套 skill 的答案是**先分诊、再分层隔离、最后选最便宜的那扇门**，并且要求每个结论都有可复现的证据。

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

六个脚本收敛成一个入口，参数原样透传给底层脚本：

```
recon <命令> [参数...] [--json]
```

| 命令 | 背后 | 说明 |
|---|---|---|
| `doctor` | `preflight.sh` | 环境自检：缺什么、缺了会降级成什么 |
| `quick` | `quick-check.sh` | 可行性快筛：每 URL 1 请求 + robots |
| `matrix` | `client-matrix.sh` | 客户端栈矩阵 + 内容区长度 |
| `fetch` | `fetch-page.py` | 客户端阶梯取内容 + WAF 归类 |
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

`fp` → `{"command":"fp","diff":{"total":3,"differ":2,"fields":[...]}}`，`fetch` → `records`，`check` → `failures`，`report` → `out` / `report`。`ok` 直接取自退出码，所以「有 URL 不可行」「两份指纹有差异」都是 `false`。

想当全局命令用：

```bash
ln -s "$PWD/scripts/recon.py" ~/.local/bin/recon
# 或 alias recon="$PWD/scripts/recon.py"
```

还没做 pip / uvx / npx 打包——先把命令和 JSON 契约用稳，打包器随时能加一层。

## 安装与 Agent 适配

核心只有一份 `SKILL.md`，适配层是薄壳（只做路径与指针）。详见 [adapters/README.md](adapters/README.md)。

| Agent | 方式 |
|---|---|
| Claude Code / Codex CLI / Gemini CLI / Copilot CLI | Agent Skills（`SKILL.md`） |
| Cursor / Windsurf / 其他 | 指向 `AGENTS.md` |
| 任意 Agent | 让它读 `SKILL.md` |

## 目录结构

```
SKILL.md                      # 唯一入口（Agent Skills 标准）
AGENTS.md                     # 给只读 AGENTS.md 的 Agent
references/
  triage-and-gates.md         # 三态判据 + 闸门图 + 归因树
  layers.md                   # 七层判据、探针、常见误判
  vectors.md                  # 检测向量全景（每层还有哪些信号）
  evidence-matrix.md          # 实验设计 + 非确定性
  metrics.md                  # 双指标 + 虚高审计
  countermeasure-ladder.md    # 对策阶梯 + 决策树
  site-profile.md             # 站点档案 schema + 报告生成
  toolbox.md                  # 默认工具链、安装、版本陷阱
  fingerprint-methods.md      # 指纹分析：采集 / 自洽 / 差分 / 归因 + 两个默认读数页
  waf-classes.md              # WAF 六类机制、判据与合规处理路径
  cases/                      # 脱敏案例（推理链）
assets/
  site-profile.template.yaml  # 站点档案模板
  report-template.md          # 报告模板
  workspace/                  # 工作区骨架：复制成 <项目根>/recon/（自带 .gitignore）
    .gitignore                # 除骨架外全部忽略
    README.md                 # 工作区约定：命名、规矩、重来一次
    raw/.gitkeep              # 原始响应体目录
scripts/
  recon.py                    # 统一入口：doctor / quick / matrix / fetch / fp / report / check
  preflight.sh                # 环境自检（必需 / 可选工具与版本）
  quick-check.sh              # 可行性快筛（robots 检查 + 1 请求 + 内容区）
  client-matrix.sh            # 客户端栈矩阵
  fingerprint-probe.js        # 指纹快照 + CDP 泄漏探针
  fp-echo.py                  # 网络栈指纹快照 + 逐字段 diff
  fetch-page.py               # 客户端阶梯取内容 + WAF 归类
  report.py                   # 从 profile.yaml + evidence.csv 生成报告
  self-check.py               # 结构与脱敏校验（可用于 CI）
adapters/
  README.md
  install.sh
.github/workflows/check.yml   # 每次 push 跑 self-check
LICENSE                       # MIT
```

## 质量校验

```bash
python3 scripts/self-check.py
```

覆盖：必备文件、frontmatter 规范（name / 单行 description / 长度）、SKILL.md 行数（≤150）、未决占位符、相对链接可解析、脱敏红线（站点名 / 厂商名 / 非标准状态码 / Cookie 名 + 仓库外词表里的内网标识）、全部脚本语法（`.sh` / `.js` / `.py`）、工作区骨架（内容被忽略、骨架可入库）、`recon` 入口可运行。退出码非 0 即不可发布。

## 案例

案例只保留**机制与实验设计**，去掉了站点标识、域名、厂商产品名与 Cookie 名：

- [两道正交闸门](references/cases/01-orthogonal-gates.md) — SSR 不等于 HTTP 抓取可行；UA 规则与 TLS 栈并联
- [降级 200 与一次性滚动凭证](references/cases/02-degraded-200-and-rolling-cookie.md) — 200 不等于成功；冻结重放 vs 写回链
- [CDP 痕迹、延迟重定向与概率门](references/cases/03-cdp-runtime-leak.md) — 先审计自己；判定前先 settle；指标虚高 2 倍
- [SaaS 风控的两道门](references/cases/04-saas-bot-defense.md) — 干净 IP 必要但非充分
- [挑战页 vs 硬阻断](references/cases/05-challenge-vs-hard-block.md) — 先分类再动手

## 设计原则

1. **诊断优先于对抗。** 不知道卡在哪扇门，任何绕过都是碰运气。
2. **证据优先于叙述。** 每个结论绑定实验；区分 observed / mechanism / efficacy。
3. **分层优于清单。** 分层决定排查顺序，清单只能覆盖已见过的坑。
4. **按需加载。** `SKILL.md` 保持精简，细节下沉到 `references/`，避免把整本手册塞进上下文。
5. **档案优于报告。** 报告给人看，档案给系统用。
6. **承认不可解。** 明确写出“这条路由当前无解”，比无限试错更有价值。
7. **归类不绕过。** WAF 按机制归类是为了选对合规路径，不是提供绕过手法。
8. **记录留在本地。** 工作区放项目根，骨架进仓库、内容不进；要外发就另存脱敏版。

## 边界

仅用于**授权范围内**的可访问性诊断与可靠性工程：

- 遵守 robots、ToS 与站点限速；只测你有权测的目标。
- 不绕过付费墙、登录鉴权、访问控制。
- 不提供验证码求解、账号池、凭证填充方案；验证码是上层没修好的症状，优先修上层。
- 不把站点内部常量（site id、hash、签名密钥）与具体 Cookie 名 / 值写进档案或仓库。

## 参与

- 新增案例请按 `references/cases/` 的既有结构：现象 → 误判 → 决定性实验 → 结论 → 可迁移规律 → 置信度。
- 新增结论请标注置信度；没有证据的写 `assumed`。
- 新增检测向量请落到 `references/vectors.md`，并绑定层与对策级别。
- **不出现任何内网平台名、内网域名、内部产品代号。** 要拦的词写进 `~/.config/site-recon/denylist.local`（每行一个正则，可用 `RECON_DENY` 指向别处），**不要放进仓库目录**——ignore 挡不住 `git add -f`、打包和整目录拷贝。自检会加载它一起扫；CI 没有这份词表，自动跳过该项。
- 脱敏是硬要求，`python3 scripts/self-check.py` 必须通过。

## 状态

v1.4.0 — 收敛出 CLI 入口 `scripts/recon.py`（`doctor|quick|matrix|fetch|fp|report|check`），参数透传、`--json` 输出单个对象、退出码不变；`fp diff --json` 与 `recon --version` 一并补上，自检新增「入口可运行」。v1.3.0 落地 `recon/` 工作区：`assets/workspace/` 提供骨架（`.gitignore` + 约定说明 + `raw/`），复制到项目根后记录默认不入库、只有壳被跟踪；全仓路径从 `sites/<host>` 统一为 `recon/<代号>`。v1.2.0 引入指纹细化（两个默认读数页 + `fp-echo.py` 快照 diff）、WAF 六类归因（`waf-classes.md`）与客户端阶梯取内容（`fetch-page.py`）。v1.1.0 引入默认工具链（`toolbox.md` + `preflight.sh`）、指纹分析手法、可行性快筛（`quick-check.sh`）与报告生成（`report.py`）。`scripts/self-check.py` 作为发布门禁，GitHub Actions 每次 push 自动跑。已知限制：尚未对公开目标做完整的端到端实测，也只在 Claude Code 上验证过加载；其他 Agent 走 `SKILL.md` / `AGENTS.md` 标准读取。

## License

MIT，见 [LICENSE](LICENSE)。
