# AGENTS.md

本仓库是一个 Agent Skill：**站点可访问性探索（web access recon）**。若你的 Agent 支持 `SKILL.md`（Agent Skills 标准），直接加载 [SKILL.md](SKILL.md)，本文件可以忽略。

本文件是 `SKILL.md` 的精简镜像，供只读 `AGENTS.md` 的 Agent 使用；两者冲突时以 `SKILL.md` 为准。

## 这个仓库解决什么

回答“这个 URL 为什么抓不到 / 抓不全”，并给出**最低成本**的正解。不是验证码求解器，不做鉴权绕过。

## 硬规则（不可跳过）

1. **先分诊，再归因。** 异常先判三态：链路失败 / 反爬拦截 / 内容业务状态。三态对策互斥，判错一次后面全错。
2. **单变量 + 对照。** 一次只改一个因素，始终保留真实客户端（或已知可用客户端）作对照。无对照的因果结论只能标为假设。
3. **200 不等于成功。** 降级软拦截、登录墙、空壳页都可能返回 200，必须量**内容区**而不是整页。空内容先按 [兜底判据](references/triage-and-gates.md) 排除“抽取下游丢失”，再判反爬。
4. **双指标分开报。** 可访问性成功率（能不能拿到真实内容）与内容完整率（拿到的完整吗）分母不同，禁止合并成一个“成功率”。
5. **非确定性要报区间。** 同一 URL 多 pass 会翻转。禁止用小样本点估计报良率。
6. **区分基础设施噪声。** 502/超时/连接重置不算“被反爬”，单独计数。
7. **探测要节流。** 连续打同一出口会触发频率降权，把观测结果污染成假阳性。
8. **记录写在工作区。** 全部产出写 `<项目根>/recon/<代号>/`，骨架见 [assets/workspace/](assets/workspace/)（自带 `.gitignore`，内容默认不入库）。不写进 skill 目录——`skills update` 会整份重写。

## 工作流

```
⓪ 可行性快筛  →  ① 定口径  →  ② 三态分诊  →  ③ 自底向上逐层隔离（L1→L5）  →  ④ 对策 + A/B 回归

（对策等级另用 S0–S7 编号，见 references/countermeasure-ladder.md。）
```

⓪ 只回答“这条路值不值得走”，一发请求即可；它不是结论，跳过它容易在死路上烧预算。

分层顺序：链路 → 传输（TLS/H2）→ 请求语义（Header/路由）→ 运行时（headless/CDP/指纹）→ 业务（token/登录/权限）→ 服务端决策模型 → 观测。

形态与概率门是分诊的前置：`recon shape` 先认页面形态（静态 / CSR 空壳 / 挑战 / 登录墙 / 业务空态），`recon canary` 再用同栈多 pass 判断是否概率门。单发请求推不出翻转率。

选臂只在合规客户端栈之间做，按 `host|路由类|响应类` 分上下文累计胜率；默认用 `--prior-strength 4` 从相邻上下文借先验，`--half-life 0` 不遗忘；`quick` 每条 URL 只发一个请求、跨 URL 学习，`matrix` 默认穷举，`canary` 固定一条臂。`--block-budget 3` 让连续可归因 blocked 暂停单臂，全暂停记 `all-arms-suspended`；`fixed` / `round_robin` 不受裁剪，暂停只在内存里。`--policy-out` / `--policy-in` 让后验跨轮复用（`recon-policy/1`）。规则见 [strategy.md](references/strategy.md)。

限频是 host 级联合决策：`--rate-limit-budget`（默认 2）与 `--rate-limit-cooldown`（默认 30s）让同一 host 连续 429 / `Retry-After` 后进入冷却，冷却期零请求（含 robots.txt），时长取参数与 `Retry-After` 的较长者；限频样本不更新选臂后验，冷却只约束节奏、不跨轮持久化。规则见 [strategy.md](references/strategy.md)。

探索计划可执行：`explore --plan-out` 写出的计划交给 `crawl --plan`，配额按 `host + 路由类` 聚合、只允许计划内的臂，计划外或额度用尽的路由零请求（含 robots.txt），记 `plan-exhausted`；选臂仍在额度内按策略决定。规则见 [explore.md](references/explore.md)。

默认 Agent：`recon agent <url> --out recon/<代号>` 有界地串起站点分析（每 host 1 请求，robots 先行）、`crawl` 多臂探索、同站链接发现、`explore` 计划 + frontier 增补与 `crawl --plan` 配额执行；轮数 / 页面 / 请求三重上限，终态建议、限频冷却、熔断与全臂暂停即停。它不是常驻调度器，执行仍沿用 `crawl` 的护栏。规则见 [agent.md](references/agent.md)。

会话状态只在同一次运行内、按 `(臂, host)` 隔离，存临时目录且命令结束即删；`crawl` / `fetch` / `quick` 默认开启（`--no-cookies` 关闭），`matrix` 默认关闭（`--cookies` 开启），`canary` 不持有。会话值不进证据行与策略档案，也不跨进程复用。规则见 [sessions.md](references/sessions.md)。

## 按需读取

- 三态判据与闸门图：[references/triage-and-gates.md](references/triage-and-gates.md)
- 页面形态（静态 / CSR 空壳 / 挑战 / 登录墙 / 业务空态）与最小探测路径：[references/site-shapes.md](references/site-shapes.md)
- 判定面（客户端采集 vs 服务端评分）与五个判定实验：[references/detection-surface.md](references/detection-surface.md)
- 分层判据与探针：[references/layers.md](references/layers.md)
- 每层还有哪些信号没查：[references/vectors.md](references/vectors.md)
- 实验设计与非确定性：[references/evidence-matrix.md](references/evidence-matrix.md)
- 双指标与防虚高：[references/metrics.md](references/metrics.md)
- 全部命令、选项、判据、退出码与基线阈值：[references/cli.md](references/cli.md)
- 原子层（量测 / 归类 / robots / 取样 / 节奏 / 证据 / 选举）与扩展方式：[references/atoms.md](references/atoms.md)
- 客户端臂注册表、外部内核契约与能力过滤：[references/arms.md](references/arms.md)
- 选臂上下文、奖励与成本、安全探索与暂停、限频冷却、各能力点的选臂语义：[references/strategy.md](references/strategy.md)
- 会话状态（按臂 / host 隔离、生命周期、各能力点默认值）：[references/sessions.md](references/sessions.md)
- 批量采集（多臂选举 / 预算护栏 / 证据行与试验行）：[references/crawl.md](references/crawl.md)
- 离线推演（给定胜率比较选举策略）：[references/bandit.md](references/bandit.md)
- 探索规划（后验 → 下一批探测预算）与按配额执行（`crawl --plan`）：[references/explore.md](references/explore.md)
- 默认探索 Agent（有界循环、`agent/1`、停止原因）与 LLM 接口（`advise/1` 契约、动作优先级、提示骨架与护栏）：[references/agent.md](references/agent.md)
- 四种形态（Skill / CLI / Agent / LLM）的分工、共享契约与扩展方式：[references/forms.md](references/forms.md)
- 端到端走查（本地示例站，不碰真实目标）：[references/walkthrough.md](references/walkthrough.md)
- 对策阶梯与决策树：[references/countermeasure-ladder.md](references/countermeasure-ladder.md)
- 从档案到执行：lint 分级、层→级、五步、失败回流：[references/execution.md](references/execution.md)
- 站点档案 schema 与报告生成：[references/site-profile.md](references/site-profile.md)
- 默认工具链与版本坑：[references/toolbox.md](references/toolbox.md)
- 遇到 WAF 的六类机制与合规路径：[references/waf-classes.md](references/waf-classes.md)
- 指纹分析手法：[references/fingerprint-methods.md](references/fingerprint-methods.md)
- 交付前自查：常见误判黑名单 [references/anti-patterns.md](references/anti-patterns.md)；这次诊断站不站得住 [references/review-scorecard.md](references/review-scorecard.md)
- 案例：[references/cases/](references/cases/)

## 脚本

```bash
scripts/recon.py <命令> <参数>          # 统一入口，参数透传；加 --json 输出单个 JSON 对象
scripts/recon.py doctor                                         # 环境自检
scripts/recon.py quick --from urls.txt --selector '#content'    # 可行性快筛（含 robots 检查）
scripts/recon.py shape <url>                                    # 单响应形态分类（可 --html / --explain / --signals）
scripts/recon.py matrix <url> --selector '#content' --passes 3   # 客户端栈矩阵 + 内容区长度
scripts/recon.py canary <url> --selector '#content' --passes 5   # 概率门：翻转率与内容区间
scripts/recon.py canary <url> --selector '#content' --save baseline.json   # 存基线
scripts/recon.py canary <url> --selector '#content' --compare baseline.json # 回归对比（阈值见 cli.md）
scripts/recon.py stats recon/<代号>/evidence.jsonl               # 双指标结算：按路由 + Wilson 区间
scripts/recon.py stats before.jsonl --compare after.jsonl        # 改前改后：差值与 Newcombe 区间，小样本判 insufficient
scripts/fingerprint-probe.js                                    # 页面内指纹快照 + CDP 泄漏探针
scripts/recon.py fp diff a.json b.json                          # 网络指纹快照 + 逐字段 diff
scripts/recon.py fetch <url> --selector '#content'               # 客户端臂阶梯取内容 + WAF 归类（--rung 只跑一级）
scripts/recon.py fetch <url> --arms browser:chromium --arms-file ~/.config/site-recon/arms.json  # 外部内核（仓库不内置）
scripts/recon.py crawl --from urls.txt --selector '#content'     # 批量取内容：多臂选举 + 预算 / 熔断护栏
scripts/recon.py crawl --from urls.txt --policy-out recon/<代号>/policy.json   # 存后验，下一轮热启动
scripts/recon.py crawl --from urls2.txt --policy-in recon/<代号>/policy.json --policy-out recon/<代号>/policy.json
scripts/recon.py bandit --arms native=0.4,cffi:chrome=0.75       # 离线推演选臂策略
scripts/recon.py bandit --rates-json recon/<代号>/rates.json      # 按上下文分组推演（不发请求）
scripts/recon.py bandit --policy-in recon/<代号>/policy.json      # 用上一轮策略档案推演
scripts/recon.py explore --trials recon/<代号>/trials.jsonl --budget 30  # 只读：下一批探测预算
scripts/recon.py advise recon/<代号>/evidence.jsonl --json        # 证据行 → 下一步动作（只读，不执行）
scripts/recon.py agent <url> --out recon/<代号>                  # 默认 Agent：分析 → 探索 → 计划 → 配额执行（有界）
scripts/recon.py surface <url>                                  # 站点 JS 采集面：采了什么、往哪上报
scripts/recon.py plan recon/<代号>/profile.yaml                  # 档案 lint + 执行计划
scripts/recon.py report recon/<代号>/profile.yaml                # 从档案生成报告
```

退出码与底层脚本一致；`--json` 不改变退出码，只把输出收敛成 `{"command": ..., "ok": ...}` 加该命令的字段。

## 边界

仅用于授权范围内的可访问性诊断与可靠性工程。不绕过付费墙 / 登录鉴权 / 访问控制，不提供验证码求解与账号池方案，不把站点内部常量写入仓库。

---

## 维护本仓库时

- `SKILL.md` 是唯一入口，保持精简（≤150 行），细节下沉到 `references/`。
- 每个 `references/*.md` 只回答一类问题，避免与 `SKILL.md` 重复。
- 改动后跑 `python3 scripts/self-check.py`，必须零失败；涉及脚本行为时再跑 `python3 -m unittest discover -s tests -v`（本地 fixture，无外网）。
- 新能力先落到 `scripts/reconlib/` 原子层，CLI 只做编排；度量与归类逻辑不允许在 CLI 里再写一份。
- 改 `SKILL.md` 的 `metadata.version` 要同步 `README.md` 的「状态」与 `CHANGELOG.md` 的条目；案例投稿规则见 [CONTRIBUTING.md](CONTRIBUTING.md)。
- 案例必须脱敏：不含内网平台名、内网域名、IP、工单号、站点密钥或指纹常量。要拦的词写进 `~/.config/site-recon/denylist.local`（仓库外），不要放进仓库目录。
- 新增结论时标注置信度：observed / mechanism / efficacy。
