---
name: site-accessibility-recon
description: 系统化、分层的网页可访问性排查与反爬归因。回答“这个 URL 为什么抓不到 / 抓不全”，并先快速判断可访问性路径可行性：区分链路失败、反爬拦截、内容业务状态，再按 传输 / 请求 / 运行时 / 凭证 / 服务端策略 逐层单变量隔离，用指纹分析定身份，遇 WAF 先按机制归类再选合规路径，产出闸门图、证据矩阵、机器可读站点档案与可复现报告，并以“可访问性成功率 / 内容完整率”双指标验证对策。Triggers - 抓取返回空、被拦、验证码、登录墙、内容不完整，异常状态码、非标准拒绝页，TLS/H2 指纹，JA3/JA4，canvas/WebGL 指纹，CDP 与 headless 检测，WAF 归类与风控拦截处理，可行性快筛，bot defense triage，scrape reliability。
license: MIT
compatibility: 需要 python3 与 curl；建议另装 curl_cffi 做浏览器级 TLS 指纹仿真。要让脚本真正联网，还需要能出网的出口；断网时自动降级为离线复核（只分析已留存的 HTML 与快照）。
allowed-tools: Bash, Read, Write, Edit, WebFetch, Task
metadata:
  version: 1.6.0
  scope: diagnostics
---

# 站点可访问性探索

这个 skill 不做“怎么过验证码”，它回答两个问题：**为什么拿不到 / 拿不全**，以及**最便宜的正解是什么**。

## 非目标

- 不是前端无障碍（a11y），虽然名字里有 accessibility。
- 不是验证码求解器、凭证窃取、付费墙 / 登录鉴权绕过。
- 不提供“绕过一切”的万能脚本。每个结论都必须绑定它的证据和适用条件。

## 心智模型：闸门图

一个请求要穿过若干**并联**的闸门，**全部通过**才算可访问；失败发生在**第一个**过不去的闸门。

```
请求 ──► [闸1 网络可达] ──► [闸2 传输身份] ──► [闸3 请求语义] ──► [闸4 运行时身份] ──► [闸5 业务信任] ──► 内容
          任一闸不过 = 请求终止；下游闸门再完美也拿不到内容
```

所以不要问“是不是被反爬了”，要问“卡在哪一扇门”。每扇门只描述三件事：

1. **判据**：怎么观测到它（状态码 / 响应体 / 最终 URL / 响应头 / 内容区长度）。
2. **成本**：满足它要付出什么（换请求方式 / 换出口 / 换内核 / 换数据源）。
3. **正交性**：两扇门是否独立。**正交闸门会让单变量实验看起来“没有效果”**——另一个闸还堵着。

## 两条铁律

1. **先分诊，后归因。** 异常先归入三态之一：链路失败态 / 反爬拦截态 / 内容业务状态态。三者的对策互斥，误判一次，后面全错。
2. **单变量 + 真实客户端对照。** 一次只改一个因素，并且始终保留真实浏览器（或已知可用客户端）作对照组。没有对照的因果结论，只能标为“假设”。

## 流程

| 步骤 | 产出 | 关键动作 |
|---|---|---|
| ⓪ 可行性快筛 | 每条候选路径的判定 | `quick-check.sh` 一发请求先分流出「直接放弃」与「值得深挖」，再决定投多少成本 |
| ① 定口径 | URL / 路由集合、成功定义 | 先定义“可访问”和“完整”，再开始测 |
| ② 分诊 | 异常样本的态别 | 三态判据，排除链路与内容状态 |
| ③ 自底向上隔离 | 闸门图 + 证据矩阵 | L1→L5 逐层，单变量，留对照 |
| ④ 对策与回归 | 站点档案 + 双指标前后对比 | 选最低成本闸门，A/B 验证，防非确定性误判 |

对策等级另用 S0–S7 编号（见 [countermeasure-ladder.md](references/countermeasure-ladder.md)），与步骤编号区分开。

⓪ 是**便宜的前置**，不是结论：它只回答“这条路值不值得走”，跳过它容易在合规禁止或路由不存在的路上烧预算。自底向上：低层失败会让高层结论全部失效（链路不通时谈指纹毫无意义），且低层方案通常更便宜。

## 分层速查（L0 口径 + L1–L7）

| 层 | 核心问题 | 最小判据 | 详见 |
|---|---|---|---|
| L0 口径 | 测什么、怎么算成功 | URL/路由集合 + 双指标定义 | [metrics.md](references/metrics.md) |
| L1 链路 | 请求到源站了吗 | `finalUrl` 非错误页；CONNECT 200 + 握手成功 | [layers.md](references/layers.md) |
| L2 传输 | 边缘认这个客户端栈吗 | 同 UA 换 TLS/H2 栈，结果是否翻转 | [layers.md](references/layers.md) |
| L3 请求 | 这次请求“像不像”正常请求 | 导航 vs XHR、Sec-Fetch-*、cookie 注入通道 | [layers.md](references/layers.md) |
| L4 运行时 | 这个浏览器“像不像”真人 | 指纹自洽 + CDP 痕迹 + 时区/语言/几何 | [layers.md](references/layers.md) |
| L4.5 行为 | 交互“像不像”真人 | 鼠标轨迹 / 键盘节奏 / 滚动 / 事件时序 | [vectors.md](references/vectors.md) |
| L5 业务 | 凭证、签名、权限够不够 | token 三层：签名 / 时效 / 内容 | [layers.md](references/layers.md) |
| L6 服务端 | 决策模型如何组合这些信号 | 闸门图 + 路由 / 资源 / 出口维度对比 | [triage-and-gates.md](references/triage-and-gates.md) |
| L7 观测 | 结论可复现吗 | 多 pass、区间、噪声分离、A/B | [metrics.md](references/metrics.md) |

## 输出物

一次完整的 recon 交付五件东西。时间受限时先交 **P0 三件**，P1 标为待补——**P0 齐全才算本轮有结论**，不能拿 P1 未做当“没问题”。

| 优先级 | 产出 | 内容 |
|---|---|---|
| P0 | 闸门图 | 卡在哪几扇门，哪些已证通过，哪些还是假设 |
| P0 | 证据矩阵 | 客户端栈 × 身份 × 路由 的结果表，含对照组 |
| P0 | 站点档案 | `<项目根>/recon/<代号>/profile.yaml`，机器可读，可直接驱动执行侧；模板见 [assets/site-profile.template.yaml](assets/site-profile.template.yaml)。`metrics` 未测就写 `null`，不要写 0 |
| P1 | 双指标 | 可访问性成功率与内容完整率分开报，含虚高审计 |
| P1 | 报告 | 复现步骤、根因、最低成本方案、回归方式；不要手写，用 `scripts/report.py` 从档案生成，模板见 [assets/report-template.md](assets/report-template.md) |

P1 推迟时必须写明推迟原因与已覆盖的样本范围，否则双指标会被误读成全量结论。**工作区**：记录写 `<项目根>/recon/<代号>/`，骨架与忽略规则见 [assets/workspace/README.md](assets/workspace/README.md)；不写进 skill 目录——`skills update` 会整份重写。

## 按需加载

| 你要做的事 | 读这个 |
|---|---|
| 判定异常属于哪一态、画闸门图、判定面在客户端还是服务端 | [triage-and-gates.md](references/triage-and-gates.md) · [detection-surface.md](references/detection-surface.md) |
| 逐层的判据、探针、常见误判；这一层还有哪些信号没查 | [references/layers.md](references/layers.md) · [references/vectors.md](references/vectors.md) |
| 设计对比实验、算指标、防虚高、做回归 | [evidence-matrix.md](references/evidence-matrix.md) · [metrics.md](references/metrics.md) |
| 选最低成本对策 | [references/countermeasure-ladder.md](references/countermeasure-ladder.md) |
| 遇到 WAF：归类 + 选合规处理路径 | [references/waf-classes.md](references/waf-classes.md) |
| 落站点档案、生成报告 | [references/site-profile.md](references/site-profile.md) |
| 先装环境、知道用哪个工具、别踩版本坑 | [references/toolbox.md](references/toolbox.md) |
| 做指纹分析（采集 / 自洽 / 差分 / 归因） | [references/fingerprint-methods.md](references/fingerprint-methods.md) |
| 交付前自查：常见误判、这次诊断站不站得住 | [references/anti-patterns.md](references/anti-patterns.md) · [references/review-scorecard.md](references/review-scorecard.md) |
| 看真实案例的推理链 | [references/cases/](references/cases/) |

## 常用脚本

```bash
# 统一入口 recon <命令> <参数>：参数透传给对应脚本，加 --json 输出单个 JSON 对象（含 command 字段）
scripts/recon.py doctor

# 0) 可行性快筛：一批 URL 各发 1 请求，先分出可行 / 需授权 / 疑似拦截 / 合规禁止
scripts/recon.py quick https://example.com/a https://example.com/b --selector "#content"
scripts/recon.py quick --from urls.txt --min-content 500 --json

# 客户端栈矩阵：同一 URL 跑多种客户端，输出状态码/大小/内容区字符数/耗时/最终 URL
scripts/recon.py matrix https://example.com/page --selector "#content" --passes 3 --save-html recon/raw
scripts/recon.py matrix https://example.com/page --clients curl,node   # 目标有短窗限速时只打两栈

# 离线复核留存的原始 HTML（区分「容器为空」与「容器不存在」）
scripts/recon.py matrix --html recon/raw/p1-curl.html --selector "#content"

# 浏览器内指纹快照（含 CDP 泄漏探针），在页面上下文执行
scripts/fingerprint-probe.js

# 网络栈指纹：采集快照并逐字段 diff（配合浏览器侧与网络侧两个读数页）
scripts/recon.py fp collect --via cffi --impersonate chrome --out chrome.json
scripts/recon.py fp diff baseline.json chrome.json

# 按客户端阶梯取内容，失败时给出 WAF 机制归类
scripts/recon.py fetch 'https://example.com/a' --selector "#content"

# 站点 JS 采集面：它采了什么、往哪上报；先 hook 哪几个（判定面见 detection-surface.md）
scripts/recon.py surface https://example.com/page

# 从档案 + 证据表生成报告（缺的字段写「未记录」，不编数字）
scripts/recon.py report recon/forum/profile.yaml --out recon/forum/report.md
```

## 红线

- 只测**你有权测**的目标；遵守 robots、ToS 与该站限速要求。探测本身要有节流：连续探测会触发按 IP 的频率降权，把结论污染成“被反爬”。
- 不绕过付费墙、登录鉴权、访问控制；不提供验证码求解、账号池、凭证填充的做法（验证码是“上层没修好”的症状，优先修上层）。
- 不把站点内部常量（site id、hash、签名密钥）写进档案或提交到仓库。

## 判定置信度

每个结论都标注它到了哪一级，不许混用：

| 级别 | 含义 |
|---|---|
| observed | 观察到现象（可能是相关性） |
| mechanism | 机制已确证（能解释并复现因果） |
| efficacy | 方案在真实流量上验证有效（含前后对比与样本量） |

“机制触发”不等于“问题解决”：检测到了墙、重试真的跑了，并不代表这次重试转化成了内容。
