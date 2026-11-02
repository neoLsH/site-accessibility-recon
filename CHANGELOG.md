# 更新记录

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；版本号用 `主.次.修订`。

标签与提交不一定一一对应：v1.3.0 与 v1.4.0 的工作合并在同一个提交里，所以仓库里只有 v1.4.0 的标签。

## [1.19.0] — 2026-11-02

补上同一次运行内的会话连续性：服务端在首跳种下的会话标识，后续页面由同一条臂、同一个 host 沿用，减少把“正常会话”误判成拦截。

### 新增

- `reconlib/cookies.py`：`CookieStore` 按 `(臂, host)` 各存一份会话状态，放在临时目录（`recon-cookies-<随机>`），文件权限 `0600`，`close()` 或对象回收时删除。
- `native` 臂用 Netscape 格式 jar 配合 `curl -b/-c` 读写；`cffi:<profile>` 臂用 `curl_cffi` 会话对象；外部渲染臂由适配器自理，本层不注入。
- `ArmSession(cookies=True)` 默认持有会话并传给 HTTP 采样；`fetch_one(..., cookies=store)` 保留原有无状态调用方式。
- `tests/test_cookies.py`（11 个）：隔离、权限、关闭清理、禁用模式、服务端 cookie 回放、无状态对照、证据行不含会话值、CLI 默认与关闭路径。

### 变更

- `crawl` / `fetch` / `quick` 默认开启会话，`--no-cookies` 关闭；`matrix` 默认关闭，`--cookies` 显式开启（保持逐臂独立对照）；`canary` 不持有会话（基线要可复现）。
- 会话值不进入证据行、试验行、策略档案与表格输出；不跨进程、不读取外部 cookie 或凭证。
- 新增 [references/sessions.md](references/sessions.md)；`SKILL.md` 版本升到 1.19.0，`README.md`、`AGENTS.md`、`cli.md`、`crawl.md`、`atoms.md`、`strategy.md` 同步；自检新增会话门禁。

### 已知限制

- 只延续目标站点本轮主动下发的状态；登录、鉴权、挑战仍走业务授权与人工路径，本层不处理。
- `matrix` 与 `matrix --cookies` 是两种口径，不能把两轮结果直接放进同一张表比较；要看差异需分别跑并在报告中注明。
- 会话按 `(臂, host)` 隔离，因此同一 host 的多个路由共享状态；需要完全独立对照时用 `--no-cookies`。
- 它不放宽合规边界：robots、节流、host 级熔断与臂暂停照旧按请求计数执行。

## [1.18.0] — 2026-11-02

把探索从“敢试”推进到“会收手”：连续可归因 blocked 达到预算即暂停单臂，全暂停时整页 halt，不再为试满而继续撞墙。

### 新增

- `ContextualBandit` 的 `block_budget`（默认 `3`，`0` 关闭）：按 `host + 路由类 + 臂` 统计连续可归因 `blocked`，达阈值后该臂在本轮剩余请求里暂停；`ok` / `degraded` 清零，`auth` / `missing` / `noise` / 限频不计数也不清零。
- 快照新增 `suspended` / `block_streak` / `suspended_arms` / `block_budget`；候选臂全暂停时 `select` 返回 `None`，快照 `decision` 记为 `halt`。
- `ArmSession` 感知 `halt`：该页记 `all-arms-suspended`，不发额外请求；`crawl` 摘要新增 `suspended_pages`，`fetch` / `quick` / `matrix` 同步接入。
- `crawl` / `fetch` / `quick` / `matrix` / `explore` 新增 `--block-budget`；`explore` 回放时重演暂停，被暂停的臂不进 `steps`，整段上下文全暂停时进 `hold` 并追加停止规则。
- `tests/test_policy_safety.py`（10 个）与 `crawl` / `session` / `explore` 的暂停用例；自检新增安全探索门禁（暂停 / 清零 / 路由隔离 / halt / fixed 不受影响 / 档案仍为 5 元组 / 规划排除暂停臂）。

### 变更

- `references/strategy.md` 增加“安全探索：连续 blocked 暂停单臂”，`crawl.md`、`cli.md`、`explore.md`、`atoms.md`、`agent.md` 同步补暂停语义与 `halt` 停止条件。
- `SKILL.md` 版本升到 1.18.0；`README.md`、`AGENTS.md` 同步。

### 已知限制

- 暂停只作用于 `thompson` / `ucb1`：`fixed` / `round_robin` 是显式穷举模式，不做裁剪。
- 暂停状态只在内存里，不写进 `recon-policy/1`；下一轮恢复档案后重新计数，不构成永久拉黑。
- 暂停粒度是 `host + 路由类`，同一路由下的不同响应类共享暂停；路由分类粗分时可能连带暂停本可访问的页面。
- 它只约束请求节奏，不改变合规边界：robots、节流、host 级熔断照常执行；全暂停时应核查限速与出口，而不是关掉暂停继续试。

## [1.17.0] — 2026-11-02

把后验从“诊断结论”推进到“下一批请求的执行计划”：新增只读探索规划器，回答预算该花在哪个上下文、哪条臂、还差多少样本。

### 新增

- `reconlib/explore.py`：`replay` 把试验行按原顺序回放进 `ContextualBandit`（只取可归因、非限频行），`from_policy` 恢复 `recon-policy/1`，`plan` 输出 `recon-explore/1`。
- `scripts/explore.py` 与 `recon explore`：`--trials` / `--policy-in` 二选一，`--arms` 只列臂名，支持 `--budget`、`--max-per-context`、`--target-pulls`、先验 / 遗忘 / 成本参数与 `--seed`。
- 规划顺序固定：未见臂 → 补样本 → 探索次优臂 → `hold`；预算按“权重 /（已分配 + 1）”贪心分配，单上下文有上限。
- 输出 `contexts` / `steps` / `hold` / `unseen_arms` / `notes` / `stop`，每条动作带请求数与理由。
- `references/explore.md`；`advise` 的 `switch-arm` / `differentiate` 动作补上 `--trial-out` 与 `explore` 命令。
- `tests/test_explore.py`（16 个）；自检新增探索规划门禁（只读、预算封顶、未试臂可识别、同输入可复现）。

### 变更

- `recon.py` 的 `TOOLS` 与 JSON 汇总接入 `explore`。
- `SKILL.md` 版本升到 1.17.0；`README.md`、`AGENTS.md`、`atoms.md`、`strategy.md`、`crawl.md`、`bandit.md`、`cli.md` 同步。
- 试验行缺少 `context` 时按 `url` + 路由形状推导为 `host|路由类|unknown`；缺少 `url` 的行无法定位 host，直接剔除。

### 已知限制

- 预算单位是请求数，不含 `--delay`、退避与 robots 请求；规划结果不能喂 `stats`，也不写进站点档案的 `metrics`。
- `pages` 是样本权重（试验行按 URL 去重、档案按 pulls 合计），不是精确页数。
- 推导出的 `unknown` 响应类可能合并不同结局；结论反复翻转时先补 `context` 再规划。

## [1.16.0] — 2026-11-02

把选臂从“本轮学”推进到“跨轮学”：冷启动有先验、规则漂移可遗忘、诊断能回答“继续试还是收手”，后验可落盘并在下一轮热启动。

### 新增

- `ContextualBandit` 的分层经验先验：`--prior-strength`（默认 `4.0`）从相邻上下文借伪计数；先验只参与打分，不改本地 `pulls`，`--min-pulls` 判断仍看本地样本。
- 指数遗忘：`--half-life N` 在每次更新前按 `0.5 ** (1/N)` 衰减全部计数；默认 `0`（不遗忘）。
- 诊断字段：Wilson `ci95`、确定性蒙特卡洛 `p_best`（400 次）、`utility`、`n_eff`、`confidence` 与 `decision`（`>= 0.8` 且有效样本 `>= 4` 才 `exploit`，单臂恒 `exploit`）。
- `reconlib/policy_store.py`：策略档案 `recon-policy/1`，原子写（临时文件 + fsync + rename）、大小上限、结构与状态校验；`restore` 忽略未知臂，`context_rates` 把档案转成 `--rates-json` 同构输入。
- `crawl` / `fetch` / `quick` / `matrix` 的 `--policy-in` / `--policy-out`；`bandit --policy-in` 作为第四个胜率来源。
- `references/strategy.md` 增加分层先验、遗忘窗口、诊断、跨轮热启动四节。
- `tests/test_policy_prior.py`（10 个）、`tests/test_policy_store.py`（7 个），并在 `crawl` / `quick` / `matrix` / `bandit` 补档案与来源用例。
- 自检新增策略记忆门禁：先验 / 遗忘 / 诊断 / 档案往返，以及四个采集入口与 `bandit` 的参数接入。

### 变更

- `crawl` 摘要新增 `policy_in` / `policy_out`（失败时 `policy_out_error`）；档案默认写 `recon/`，与证据行、试验行同一工作区。
- `ArmSession` 把 `prior_strength` / `half_life` 传给 `ContextualBandit`；档案里的同名配置只作记录，不覆盖本轮 CLI。
- `SKILL.md` 版本升到 1.16.0；`README.md`、`AGENTS.md`、`atoms.md`、`arms.md`、`cli.md`、`crawl.md`、`bandit.md`、`agent.md`、`forms.md`、`toolbox.md`、`assets/workspace/README.md` 同步。

### 已知限制

- 分层先验会把相邻上下文的胜率带进来；站点内差异很大或外部内核质量参差时，用 `--prior-strength 0` 关掉再比较。
- 遗忘默认关闭；开启后旧证据按半衰期衰减，样本很少时会让后验收缩过快。
- `decision` 只是诊断提示，不改变 `select` 的探索行为；要强制收手需显式换 `fixed`。
- 策略档案含 host 与路由键，不是匿名配置；跨目标复用前先确认作用域，且它不覆盖本轮 `--prior-strength` / `--half-life`。
- `bandit --policy-in` 只取有本地样本的格；档案里没有样本的上下文不会进入推演。

## [1.15.0] — 2026-11-02

把选臂从“按 host 学胜率”推进到“按上下文 + 成本学”，并让每个发请求的能力点都走同一套选臂。

### 新增

- `reconlib/context.py`：上下文键 `host|路由类|响应类`，路由类只看 URL 路径形状，响应类由首次结局映射；提供 `key` / `fallbacks`。样本不足时沿 `host|route|response → host|route|* → host|*|* → *` 聚合。
- `ContextualBandit`（`reconlib/policy.py`）：`ok=1.0` / `degraded=0.5` / `blocked=0.0`；`auth` / `missing` / `noise` / 限频不更新后验；按 5000ms 归一化的成本惩罚，`--cost-weight` 默认 `0.1`，`0` 关闭。
- `simulate_contexts`（`reconlib/simulate.py`）与 `bandit --rates-json`：按上下文分组、每组独立选臂与更新的离线推演。
- `advise` 增加 `context-split`：同一 host 下 `ok` 与 `blocked` 落在不同上下文时，提示先分组再换臂。
- `references/strategy.md`：上下文键、奖励与成本、各能力点的选臂语义、读法与边界。
- `tests/test_context.py`（3 个）、`tests/test_policy_context.py`（6 个）、`tests/test_quick.py`（6 个）、`tests/test_matrix.py`（4 个）。
- 自检新增策略上下文门禁：上下文与回退链可用、`quick` / `matrix` 的 shell 入口指向 Python 实现。

### 变更

- `scripts/quick.py` 与 `scripts/matrix.py` 成为 Python 实现，`quick-check.sh` / `client-matrix.sh` 变薄壳；`quick` 每条 URL 只发一个请求、跨 URL 学习（默认 `thompson`），`matrix` 默认穷举全部臂，新增 `--stop-on-ok` / `--clients` / `--html`。
- `ArmSession` 记录 `context` 与 `cost_ms`：证据行带 `context`，试验行另带 `cost_ms`；`fetch` / `crawl` / `quick` / `matrix` 均传入 `--cost-weight`。
- `SKILL.md` 版本升到 1.15.0；`README.md`、`AGENTS.md`、`atoms.md`、`arms.md`、`cli.md`、`crawl.md`、`bandit.md`、`forms.md`、`agent.md`、`toolbox.md` 同步。

### 已知限制

- 上下文只按 URL 路径与首次结局粗分，可能把相似页面合并统计；发现结论反复翻转时先核对路由类。
- 成本只在同一上下文内排序，不跨上下文比较绝对耗时；`cost_ms` 缺失时成本惩罚不生效。
- `quick` 单 URL 单请求，不能在同一条 URL 内试探多臂；需要逐臂对照时用 `fetch` 或 `matrix`。
- 推演不产生证据：`--rates-json` 的胜率仍须来自实测，策略比较不替代 `stats --compare`。

## [1.14.0] — 2026-11-02

把“客户端臂”从两个硬编码名字变成可扩展注册表，并把 robots、节流、选臂、归类、更新收敛到唯一编排层。

### 新增

- `reconlib/arms.py`：臂注册表。内置 `native` / `cffi:<profile>` / `browser:<engine>`；外部条目可覆盖内置，同名重复被拒；能力 `tls/h2/js/cookies` 供 `--require` 硬过滤。
- `reconlib/render.py`：外部内核适配器，支持 `cmd`（argv，不经 shell，占位符 `{url} {engine} {ua} {timeout}`）与 `http`（POST JSON）两种传输；响应体上限 16 MiB；协议错误统一归 `noise`。
- `reconlib/session.py`：`ArmSession` 编排层（robots → pacing → 选臂 → 取样 → 归类 → 更新 → 证据行 / 试验行），供 `fetch` / `crawl` / `canary` 共用。
- `references/arms.md`：臂模型、外部配置优先级、双传输契约、能力过滤与安全边界。
- `tests/test_arms.py`（9 个）、`tests/test_render.py`（9 个）、`tests/test_session.py`（8 个）。
- 自检新增臂注册表门禁：未配置的 `browser` 不可用、裸 `browser` 被拒、`render` 不含 `shell=True`、三个能力点已接入。

### 变更

- `fetch` / `crawl` 新增 `--arms-file`、`--require`；`fetch` 新增 `--arms` 与 `--policy`；`canary` 新增 `--arms` / `--policy` / `--seed` / `--arms-file` / `--require` / `--impersonate`。
- `crawl` 的护栏（pages / requests / 间隔 / 熔断）与输出字段不变；跳过臂保留在尝试序列末尾，不计请求预算。
- `canary` 在整个 pass 序列固定一条臂，避免选臂差异污染翻转率。
- `SKILL.md` 版本升到 1.14.0；`README.md`、`AGENTS.md`、`atoms.md`、`cli.md`、`toolbox.md`、`crawl.md` 同步。

### 已知限制

- 外部内核质量取决于适配器；仓库只校验契约，不保证渲染结果与真实浏览器一致。
- 适配器不接收 Cookie、凭证、代理或出口参数；需要登录的路径仍走业务授权。
- 未配置的外部臂只出现在尝试序列里，不会自动安装或回退到内置浏览器。

## [1.13.0] — 2026-11-02

把“看到证据后该做什么”和“这套能力以什么形态交付”一起补齐：建议层只读、可校验、带停止条件，四种形态共用一份原子口径。

### 新增

- `recon advise <evidence.jsonl>`（`scripts/advise.py`）：输出 `advise/1` 契约（`evidence` → `findings` → `next`），只读、不联网、不执行建议命令；`--route` 只看单个路由，`--json` 给机器读。
- `reconlib/advice.py`：先分诊的动作表——噪声单列 → 鉴权 → 路由不存在 → 抽取不足 → 限频退避 → 臂间差异 → 单变量对照；每条带层、S 级、理由、置信度与停止条件。
- `references/agent.md`：Agent 循环、`advise/1` 字段、动作优先级、LLM 提示骨架与护栏。
- `references/forms.md`：Skill / CLI / Agent / LLM 四种形态的分工、共享契约与扩展方式。
- `tests/test_advice.py`（13 个）、`tests/test_advise.py`（5 个）。
- 自检新增建议层检查：只读、示例判 `collect-more`、噪声剔出分母、试验行被拒、建议必须带停止条件。

### 变更

- `references/atoms.md` 扩到九个原子；`SKILL.md`、`README.md`、`AGENTS.md`、`cli.md`、`toolbox.md` 同步 `advise` 与四种形态。
- `SKILL.md` 版本升到 1.13.0。

### 已知限制

- 动作仍是规则启发式，只覆盖现有结局词表；新增结局要先扩词表再扩动作表。
- `advise` 不选臂、不发请求、不写证据、不改档案；执行与新证据回写仍由调用方负责。
- 仓库只提供契约与 CLI，不含常驻的 Agent 调度器；宿主需要自己驱动循环，并且不得自动执行 `commands`。
- 提示骨架降低越界概率，但不保证模型不改写计数；仍以 `advise/1` 为事实层并保留人工确认。

## [1.12.0] — 2026-11-02

把“换不换选举策略”变成离线可算的问题：给定每臂胜率，比较各策略的请求开销与成功率，不用再拿真实流量试错。

### 新增

- `recon bandit`（`scripts/bandit.py`）：`--arms native=0.4,cffi:chrome=0.75` 或 `--trials <试验行>` 二选一，推演 `thompson` / `ucb1` / `fixed` / `round_robin`，并给出 `best-single` 期望基线。
- 试验行来源只取可归因、非限频的尝试：`ok` 记成功，`degraded` / `blocked` 记失败，`noise` / `auth` / `missing` 与 `rate_limited` 剔除。
- `reconlib/simulate.py`：`parse_rates` / `rates_from_trials` / `simulate` / `best_single`；不发请求，同 `--seed` 可复现。
- `assets/example/trials.jsonl`：示例试验行，`bandit` 估出 `native=0.40`、`cffi:chrome=0.75`。
- `tests/test_simulate.py`（11 个）、`tests/test_bandit.py`（8 个）。
- `references/bandit.md`：指标读法、自适应采样偏差、点估计与不模拟节流的限制。
- 自检新增离线推演检查：`bandit` 不含网络调用、同 seed 可复现、示例胜率口径正确。

### 变更

- `references/atoms.md` 扩到八个原子；`crawl.md`、`cli.md`、`toolbox.md`、`SKILL.md`、`README.md`、`AGENTS.md` 同步。
- `SKILL.md` 版本升到 1.12.0。

### 已知限制

- 每臂只用一个点估计胜率，不建模概率门、时间漂移与缓存；胜率本身仍须实测。
- `--trials` 的样本由上一轮策略自适应采集，弱臂样本更少，估计更不稳；样本不足时建议手给 `--arms`。
- 推演不产生证据行，不能喂 `stats`，也不写进档案的 `metrics`。

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
