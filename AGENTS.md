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

## 工作流

```
⓪ 可行性快筛  →  ① 定口径  →  ② 三态分诊  →  ③ 自底向上逐层隔离（L1→L5）  →  ④ 对策 + A/B 回归

（对策等级另用 S0–S7 编号，见 references/countermeasure-ladder.md。）
```

⓪ 只回答“这条路值不值得走”，一发请求即可；它不是结论，跳过它容易在死路上烧预算。

分层顺序：链路 → 传输（TLS/H2）→ 请求语义（Header/路由）→ 运行时（headless/CDP/指纹）→ 业务（token/登录/权限）→ 服务端决策模型 → 观测。

## 按需读取

- 三态判据与闸门图：[references/triage-and-gates.md](references/triage-and-gates.md)
- 分层判据与探针：[references/layers.md](references/layers.md)
- 每层还有哪些信号没查：[references/vectors.md](references/vectors.md)
- 实验设计与非确定性：[references/evidence-matrix.md](references/evidence-matrix.md)
- 双指标与防虚高：[references/metrics.md](references/metrics.md)
- 对策阶梯与决策树：[references/countermeasure-ladder.md](references/countermeasure-ladder.md)
- 站点档案 schema 与报告生成：[references/site-profile.md](references/site-profile.md)
- 默认工具链与版本坑：[references/toolbox.md](references/toolbox.md)
- 遇到 WAF 的六类机制与合规路径：[references/waf-classes.md](references/waf-classes.md)
- 指纹分析手法：[references/fingerprint-methods.md](references/fingerprint-methods.md)
- 案例：[references/cases/](references/cases/)

## 脚本

```bash
scripts/preflight.sh                                             # 环境自检
scripts/quick-check.sh --from urls.txt --selector '#content'     # 可行性快筛（含 robots 检查）
scripts/client-matrix.sh <url> --selector '#content' --passes 3   # 客户端栈矩阵 + 内容区长度
scripts/fingerprint-probe.js                                     # 页面内指纹快照 + CDP 泄漏探针
scripts/fp-echo.py --diff a.json b.json                          # 网络指纹快照 + 逐字段 diff
scripts/fetch-page.py <url> --selector '#content'                # 客户端阶梯取内容 + WAF 归类
scripts/report.py --profile sites/<host>/profile.yaml            # 从档案生成报告
```

## 边界

仅用于授权范围内的可访问性诊断与可靠性工程。不绕过付费墙 / 登录鉴权 / 访问控制，不提供验证码求解与账号池方案，不把站点内部常量写入仓库。

---

## 维护本仓库时

- `SKILL.md` 是唯一入口，保持精简（≤150 行），细节下沉到 `references/`。
- 每个 `references/*.md` 只回答一类问题，避免与 `SKILL.md` 重复。
- 改动后跑 `python3 scripts/self-check.py`，必须零失败。
- 案例必须脱敏：不含内网域名、IP、工单号、站点密钥或指纹常量。
- 新增结论时标注置信度：observed / mechanism / efficacy。
