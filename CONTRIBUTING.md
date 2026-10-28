# 贡献指南

三类贡献，各自的门槛不同。**所有提交都要先跑 `python3 scripts/self-check.py` 并保持零失败。**

## 1. 案例投稿（最需要）

`references/cases/` 是这套方法的证据底座。投稿要满足四条：

| 要求 | 说明 |
|---|---|
| 六段结构 | 现象 / 误判（或分诊）/ 决定性实验 / 结论 / 可迁移规律 / 置信度。少了中间两段就只是故事 |
| 脱敏 | 不出现站点名、域名、厂商产品名、具体 Cookie 名、site id、hash、签名密钥。用「一个 SSG 内容站」「某边缘厂商」这种机制描述 |
| 可复现 | 给得出可执行的做法（用哪些客户端、怎么交叉、看哪个字段），不给真实 URL |
| 置信度 | 结论末尾标 `observed` / `mechanism` / `efficacy`，并说明样本范围 |

**机器会检查前两条**：`scripts/self-check.py` 校验每篇案例的必要章节与脱敏词表；章节缺失或命中敏感词直接失败。第三、四条由人工复核。

投稿方式：新增 `references/cases/NN-<slug>.md`（编号顺延），并在 `README.md` 的「案例」列表加一行。

## 2. 判据修正与新增结论

- 指出某条判据在什么情况下不成立：开 issue 讲清观测，别直接改。
- 新增结论请标置信度；没有证据写 `assumed`，不要伪装成 mechanism。
- 新增检测向量落到 `references/vectors.md`，并绑定层与对策级别。
- 结构性改动（分层、闸门图）请先开 issue 对齐口径，避免 PR 里来回返工。

## 3. 脚本与 CI

- 修 `scripts/` 下的问题：直接 PR，附最小复现。
- 新增脚本要同时满足：能被 `scripts/recon.py` 收成一个子命令、支持 `--json`、退出码有意义、自检能校验它的语法。
- 改动 `scripts/self-check.py` 的必检清单，要同步改 `README.md` 的「质量校验」一节。

## 不要提交的内容

- 真实站点标识与凭证：域名、Cookie 名 / 值、site id、签名密钥、抓包原文。
- 内部平台名、内网域名、内部产品代号。要拦的词写进 `~/.config/site-recon/denylist.local`（仓库外，每行一个正则），**不要放进仓库目录**——ignore 挡不住 `git add -f`、打包和整目录拷贝。
- 验证码求解、账号池、凭证填充的实现；这类 issue 会被直接关闭。
- 一次 PR 改多件事：拆开，一个 PR 一个主题。

## PR Checklist

- [ ] `python3 scripts/self-check.py` 零失败
- [ ] 案例：六段齐全、已脱敏、置信度已标
- [ ] 没有把 `recon/` 工作区内容、原始响应体、`.DS_Store` 带进来
- [ ] 版本相关改动同步更新了 `CHANGELOG.md`（若涉及 `SKILL.md` 的 `metadata.version`，还要同步 `README.md` 的「状态」）
- [ ] PR 描述写清：改了什么、为什么、怎么验证的
