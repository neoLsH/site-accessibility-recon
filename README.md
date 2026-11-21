# site-accessibility-recon

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![check](https://github.com/neoLsH/site-accessibility-recon/actions/workflows/check.yml/badge.svg)](https://github.com/neoLsH/site-accessibility-recon/actions/workflows/check.yml)

给 Agent 和开发者用的网页可访问性诊断工具。区分链路失败、拦截、登录墙与内容业务状态，用证据回答“为什么抓不到、哪里没抓全”。

既可作为 Agent Skill 加载，也可直接运行 CLI。默认 Agent 用规则做有界探索；可选 LangGraph 提供 checkpoint、中断和恢复，不调用大模型。

## 能做什么

- **诊断页面**：快筛、页面形态分类、客户端栈对照，避免把 HTTP 200 当作内容完整。
- **探索可用路径**：在已配置的合规客户端臂之间用上下文 MAB 选臂，结合成本、历史样本与请求配额。
- **自动跑多轮**：站点分析 → 采集 → 同站链接发现 → 规划 → 配额执行，达到预算或安全停止条件即停。
- **留证与复核**：输出证据行、试验行、策略档案和报告；分别统计可访问性与内容完整率，支持基线回归。
- **接入其他 Agent**：`--json` 输出机器格式，`advise` 给只读建议，`brief` 生成有大小上限的上下文包。

内置 `native`（curl）与可选 `cffi:<profile>`；`browser:<engine>` 通过外部适配器接入，仓库不内置浏览器。配置方法见[客户端臂](references/arms.md)。

## 安装

作为 Agent Skill 安装：

```bash
npx skills add neoLsH/site-accessibility-recon
```

直接使用 CLI：

```bash
git clone https://github.com/neoLsH/site-accessibility-recon.git
cd site-accessibility-recon
python3 scripts/recon.py doctor
```

基础运行需要 Python 3、curl 和 Bash。`doctor` 会列出缺失的可选工具；只使用 `--arms native` 不需要安装 `curl_cffi`。

手动安装到 Agent 的技能目录、通过 `AGENTS.md` 接入等方式，见[安装与适配](adapters/README.md)。

## 快速上手：只访问本机

在仓库目录启动自带示例站：

```bash
python3 -m http.server 8765 --bind 127.0.0.1 --directory assets/example/site
```

另开一个终端，在同一仓库目录执行：

```bash
python3 scripts/recon.py agent http://127.0.0.1:8765/article/1.html \
  --out recon/demo --selector '#content' --arms native \
  --max-rounds 2 --max-pages 5 --max-requests 8 --json

python3 scripts/recon.py stats recon/demo/evidence.jsonl --json
python3 scripts/recon.py brief recon/demo --budget 4000 --json
```

结果写入 `recon/demo/`：

| 文件 | 内容 |
|---|---|
| `agent.json` | 分析、每轮结果、预算用量和停止原因 |
| `evidence.jsonl` | 页面结局，供 `stats` 结算 |
| `trials.jsonl` | 各次客户端尝试，供策略规划使用 |
| `plan.json` / `policy.json` | 后续轮次的配额计划 / 可复用的策略计数 |
| `bodies/` | 开启链接发现时留存的响应体 |

后续轮次未执行时，可能没有 `plan.json`。`recon/` 内容默认不入库；外发前仍需脱敏。完整实验流程见[本地走查](references/walkthrough.md)。

用于实际目标前，先确认授权、robots 和站点限速，并将选择器改为真实正文容器。不要直接沿用示例站的内容判据。请求预算统计目标页面取样，robots 检查另计。

## 可选：LangGraph 中断与恢复

在 macOS / Linux 上创建独立环境：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-langgraph.txt
```

保持上面的本地示例站运行，使用新的输出目录与固定的 thread ID：

```bash
python scripts/recon.py agent http://127.0.0.1:8765/article/ \
  --out recon/graph-demo --selector '#content' --arms native \
  --runtime langgraph --thread-id demo --interrupt --stream
```

若进入第二轮，Agent 会在计划审核前暂停。检查 `recon/graph-demo/plan.json` 后恢复：

```bash
python scripts/recon.py agent --out recon/graph-demo \
  --runtime langgraph --thread-id demo --resume --stream
```

恢复沿用保存的种子、配置和预算；人工编辑只能缩减原配额，不能扩大目标或客户端范围。`--stream` 输出逐节点 JSONL，与 `--json` 互斥。CLI 跨进程恢复使用默认 SQLite，不使用进程内的 `--memory`。

checkpoint 不保证网络请求恰好执行一次：网络阶段状态不确定时拒绝自动重放。会话凭证不进 checkpoint，也不跨进程恢复。详见 [Agent 运行与恢复](references/agent.md)。

## 按任务查文档

| 任务 | 入口 |
|---|---|
| 让 Agent 理解排查顺序与规则 | [SKILL.md](SKILL.md) |
| 查看全部命令、选项与退出码 | [CLI 手册](references/cli.md)；`python3 scripts/recon.py --help` |
| 判断链路、拦截与业务状态 | [三态分诊](references/triage-and-gates.md)、[页面形态](references/site-shapes.md) |
| 配置客户端与外部浏览器 | [客户端臂](references/arms.md)、[会话边界](references/sessions.md) |
| 调整 MAB、采集与探索配额 | [选臂策略](references/strategy.md)、[批量采集](references/crawl.md)、[探索规划](references/explore.md) |
| 接入循环、建议与上下文包 | [Agent 手册](references/agent.md)、[交付形态](references/forms.md) |
| 对比改前改后，检查证据质量 | [指标口径](references/metrics.md)、[质量评分卡](references/review-scorecard.md) |
| 看实验设计与脱敏案例 | [证据矩阵](references/evidence-matrix.md)、[脱敏案例](references/cases/) |

## 使用边界

- 只测试有权测试的目标，遵守 robots、ToS 与站点限速。
- 不绕过付费墙、登录鉴权或访问控制；不提供验证码求解、账号池、凭证填充。
- 不把具体 Cookie 名值、站点内部常量、密钥或内部平台信息写进档案和仓库。
- 页面形态与路由分类是启发式判断，不代替真实内容对照；少量成功样本不能证明不存在拦截。
- 外部渲染器的内容质量由适配器负责；本仓库校验其协议，不保证渲染结果。

## 开发与贡献

```bash
python3 scripts/self-check.py --tests
```

自检覆盖结构、链接、脱敏、语法和行为测试。测试使用本地 fixture，不访问外部目标；安装可选依赖后同一测试集会覆盖 LangGraph。CI 在 push / PR 时运行。

脚本实现位于 `scripts/reconlib/`，CLI 负责组合能力；说明文档位于 `references/`。提交规则与案例要求见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 状态

v1.24.0。版本历史见 [CHANGELOG.md](CHANGELOG.md)，后续修复列于 Unreleased。协议与参数以当前 CLI 和对应手册为准。

验证以本地示例站和测试为主，尚未覆盖公开目标的完整端到端流程；已记录的 Skill 加载验证限于 Claude Code，其他 Agent 的兼容性未逐一实测。

## License

MIT，见 [LICENSE](LICENSE)。
