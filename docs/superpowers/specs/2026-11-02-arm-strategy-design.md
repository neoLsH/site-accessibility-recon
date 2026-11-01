# 策略臂深化设计：多内核、上下文成本感知与全能力点接入

日期：2026-11-02
状态：已确认，按两版实施（v1.14.0 / v1.15.0）

## 目标

把“客户端臂”从两个硬编码协议栈扩展成可注册的内核集合，并让每个会发请求的能力点共用同一套选臂、节流、归类与学习逻辑。策略从按 host 的无上下文老虎机升级为 `host + 路由类 + 响应类` 的上下文成本感知老虎机，用来探索“这类页面该用哪种内核”。

## 非目标

- 不在仓库内置浏览器内核；`browser:<engine>` 只是外部适配器名称。
- 不提供验证码求解、账号池、凭证填充、出口轮换、鉴权或付费墙绕过。
- 不改变 `measure` / `classify` 的结局词表与内容区口径；臂与策略不得另写一套。
- 不把 `canary` 改成逐 pass 换臂，也不改变 `matrix` 默认穷举的对照语义。

## 已确认的决策

1. 多内核 = 统一臂注册表 + 外部内核适配器（命令 / HTTP 双传输），仓库不内置内核。
2. 所有会发请求的能力点都接入：`quick` / `matrix` / `canary` / `fetch` / `crawl`。
3. 策略 = 上下文 + 成本感知；上下文取 host、路由类与响应类，奖励按结局加权并扣请求开销。
4. 上下文来自首响应，不额外发形态探测请求。
5. 注册表内置常用名称，自定义臂写在仓库外，通过 `--arms-file` 或 `RECON_ARMS_FILE` 加载。
6. `quick` / `matrix` 改为 Python 实现，原 `.sh` 保留为参数兼容薄壳。

## 架构

```
arms ── 注册表：名称 / 传输 / 能力 / 外部目标
render ─ 外部内核适配：cmd / http → observation
context ─ 上下文键：host + 路由类 + 响应类
policy ─ 上下文成本感知 Bandit（保留原按 host 接口）
session ─ 编排：robots → pacing → 选臂 → 取样 → 归类 → 更新 → 行
   ├─ quick / matrix / canary（先选臂，实验语义各自固定）
   ├─ fetch / crawl（页内选举）
   └─ evidence / trial 行 → stats / advise / bandit
```

`session` 是唯一新增的网络编排层；`fetch` / `robots` / `render` 仍是唯一网络副作用点。`policy` 只做内存计算。

## 1. 臂注册表 `reconlib/arms.py`

### 描述符

```
name          唯一名称，如 native / cffi:chrome / browser:chromium
transport     native | cffi | cmd | http
engine        外部内核名（browser 臂使用），可为空
profile       cffi 的 impersonate 目标，可为空
capabilities  tls / h2 / js / cookies 的子集
command       cmd 传输的 argv 列表，可含 {url} {engine} {ua} {timeout}
endpoint      http 传输的端点
```

### 内置名称

| 名称 | 传输 | 能力 | 可用条件 |
|---|---|---|---|
| `native` | native | tls, h2 | 始终可用 |
| `cffi:<profile>` | cffi | tls, h2, cookies | 装了 curl_cffi |
| `browser:chromium` / `browser:webkit` / `browser:firefox` | cmd 或 http | tls, h2, js, cookies | 仓库外配置提供了命令或端点 |

`cffi` 不带 profile 时展开成 `cffi:chrome`，沿用现有行为。未配置的 `browser:*` 在注册表中可见但标记 `available: false`；选中时记为 skipped，不发请求，也不更新后验。

### 外部配置

默认路径 `~/.config/site-recon/arms.json`（存在才加载），也可用 `RECON_ARMS_FILE` 指向别处或 `--arms-file` 显式指定。格式：

```json
{"arms": [
  {"name": "browser:chromium", "transport": "cmd",
   "command": ["my-render", "--engine", "{engine}", "{url}"],
   "capabilities": ["tls", "h2", "js", "cookies"]},
  {"name": "browser:webkit", "transport": "http",
   "endpoint": "http://127.0.0.1:9000/render",
   "capabilities": ["tls", "h2", "js", "cookies"]}
]}
```

规则：

- 同名外部臂覆盖内置描述；不允许空名称、重复名称、空命令、非 http(s) 端点。
- 端点带 userinfo（`http://user:pass@...`）直接拒绝，避免把凭证写进配置与日志。
- 配置只在运行时读取；仓库不提交任何外部端点、命令或内部标识。
- 加载失败退出 2，不静默回退。

## 2. 外部内核适配器 `reconlib/render.py`

统一签名：`fetch(url, arm_descriptor, timeout=20, ua=UA_DEFAULT) -> observation`。

### cmd 传输

- 参数按列表传递，不经过 shell；只替换 `{url}` `{engine}` `{ua}` `{timeout}` 四个占位符。
- 标准输出必须是 JSON 对象，至少含 `status` 与 `body`；可选 `final_url` `ctype` `headers` `elapsed_ms`。
- `body` 为字符串；二进制内容用 `body_b64`。退出码非 0、JSON 解析失败、超时都转成带 `error` 的 observation，交由 `classify` 归为 `noise`。

### http 传输

- `POST` JSON：`{"url", "engine", "ua", "timeout"}`；响应与 cmd 的 JSON 契约相同。
- 只接受 http/https；连接失败、非 2xx、超时都转成 error observation。

### 共同约束

- 响应体上限 16 MiB，超出截断并标记 `truncated`。
- 不向适配器传 Cookie、凭证、代理或出口参数；适配器只负责渲染。
- 任何外部臂调用前，robots 与 pacing 已由 session 执行。

## 3. 上下文 `reconlib/context.py`

- `route_class(url)`：`article` / `list` / `api` / `other`，按路径形状的通用启发式，不使用站点常量。
- `response_class(verdict, content)`：`content` / `shell` / `challenge` / `login` / `missing` / `noise` / `unknown`。
- `key(host, route, response)` → `host|route|response`。
- `fallbacks(key)` → `[完整键, host|route|*, host|*|*, *]`，用于样本不足时回退。

首请求上下文为 `host|route|unknown`；拿到响应后，同一 URL 的后续尝试与同 host 后续页面使用校正后的上下文。

## 4. 策略 `reconlib/policy.py`

保留现有 `Bandit` 类与行为，新增 `ContextualBandit`；`crawl` / `session` 使用新类，旧测试与 `bandit` 默认路径不变。

### 状态与奖励

- 每个 `(host, context)` 维护 `pulls`、加权成功、加权失败、成本累计。
- `ok=1`、`degraded=0.5`、`blocked=0`；`auth` / `missing` / `noise` 与 `rate_limited` 不更新。
- 成本按 `elapsed_ms` 归一化到 `[0,1]`（5000ms 封顶），选臂分数减去 `cost_weight × 成本`；`--cost-weight 0` 关闭。
- 后验用 Beta，成功 / 失败计数允许小数。
- `min_pulls` 按 context 计算；该 context 样本不足时回退到 host 级，再回退到全局；回退来源写入快照。

### 选臂

- `select(host, context, candidates)` 只在候选内选择；候选先按可用性与显式 `--require` 过滤。
- `thompson` 从回退层后验采样；`ucb1` 用回退层的均值与拉取数；`fixed` 取候选第一个；`round_robin` 按 context 轮转。
- 只有显式 `--require js` 才硬过滤无 JS 能力的臂；不自动按形态封死候选。

## 5. 编排 `reconlib/session.py`

`ArmSession` 持有注册表、策略、节流器与预算，提供 `probe(url, selector, min_content, max_attempts, require)`：

1. robots：`deny` 直接返回 skipped，不发请求；`unknown` 只告警一次。
2. pacing：按 host 等待；熔断中的 host 返回 skipped。
3. 选臂：初始上下文 `host|route|unknown`；候选可用且满足 `require`。
4. 取样：`fetch_one` 或 `render.fetch`；外部臂错误也是 observation。
5. 归类：复用 `classify`；skipped 尝试不计请求、不更新后验。
6. 更新：可归因的 `ok` / `degraded` / `blocked` 更新策略；限频等不更新。
7. 记录：每次尝试一行试验行（含 `context`、`cost_ms`），每页一行证据行（最佳结局）。
8. 停止：`ok`、不可归因失败、候选耗尽、预算耗尽、熔断。

`session` 不写文件，返回行；调用方决定落盘路径，保持副作用显式。

## 6. 能力点接入

| 能力点 | 选臂语义 | 变更 |
|---|---|---|
| `fetch` | 页内逐臂选举；默认臂仍为 `native` → `cffi:chrome` | 保留 `--rung` 兼容，新增 `--arms` / `--policy` / `--arms-file` |
| `crawl` | 每页选举；预算 / 节流 / 熔断不变 | 内部改用 session，输出字段只增不改 |
| `canary` | 先按 `host|route|unknown` 选一条臂，整个 pass 序列固定 | 新增 `--arms` / `--policy`；结果记录 `arm` |
| `quick` | 每 URL 默认 1 次请求，policy 跨 URL 学习 | 新增 `quick.py`，`quick-check.sh` 变薄壳 |
| `matrix` | 默认穷举指定臂，policy 只排顺序 | 新增 `matrix.py`，`client-matrix.sh` 变薄壳；`--stop-on-ok` 才提前停 |
| `bandit` | 离线推演，可选按 context 给胜率 | 新增 `--rates-json`，支持 context 分组 |
| `advise` | 读证据行，不选臂 | 证据快照加 `contexts`；上下文差异归入 `switch-arm` 的理由 |

`quick` / `matrix` 的薄壳只负责参数透传与退出码，逻辑只在 Python 实现里保留一份。原有输出列与判定词表保持不变，新增列放在末尾。

## 7. 错误处理与护栏

- 未配置或不可用的臂：`skipped`，不计请求、不更新策略；错误信息指出缺什么配置。
- 外部渲染器超时 / 非零退出 / 协议错误：归为 `noise`，不换臂、不算拦截。
- 配置错误、未知臂、`--require` 无候选：退出 2，不静默降级。
- robots `deny`：零请求；`unknown`：告警后继续，沿用现有口径。
- 限频：只推迟下一次请求，不重试、不换臂；连续 `blocked` 熔断仍只封当前 host。
- 预算按实际发出的请求计数，外部渲染请求同样计入 `max-requests`。
- 适配器不接收 Cookie / 凭证；日志与试验行只记录臂名称，不记录命令与端点。

## 8. 测试

新增：

- `test_arms.py`：内置解析、外部覆盖、非法配置、不可用 browser 臂、仓库外路径。
- `test_render.py`：cmd 成功 / 非零退出 / 超时 / JSON 错误；http 成功 / 5xx / 超时；16 MiB 截断；参数不经 shell。
- `test_context.py`：路由类、响应类、键与回退链。
- `test_policy_context.py`：小数奖励、成本惩罚、context 回退、同 seed 可复现。
- `test_session.py`：robots 优先级、skipped 不计数、停止条件、预算、外部臂错误归 noise。
- `test_quick.py` / `test_matrix.py`：本地 server 上的判定词表、默认穷举与 `--stop-on-ok`。

更新：`test_fetch.py`、`test_crawl.py`、`test_canary.py`、`test_bandit.py`、`test_advice.py`。

自检新增：必需文件；`render` 不含 `shell=True`；`browser` 臂未配置不可用；session 先 robots 后取样；`quick` / `matrix` 薄壳已指向 Python；上下文与成本字段在试验行里；现有护栏常量不变。

## 9. 文档

- 新增 `references/arms.md`：注册表 schema、两种传输、能力声明、配置位置、安全边界。
- 新增 `references/strategy.md`：上下文、奖励、成本、回退链、各能力点语义、读法。
- 更新 `atoms.md`、`crawl.md`、`bandit.md`、`cli.md`、`toolbox.md`、`forms.md`、`agent.md`、`SKILL.md`、`README.md`、`AGENTS.md`、`CHANGELOG.md`。

## 10. 实施分期

### v1.14.0 — 多内核与统一编排

- `arms` 注册表、`render` 双传输、外部配置加载。
- `session` 编排；`fetch` / `crawl` 接入；`canary` 固定臂选臂。
- 臂与渲染测试、自检、`arms.md`、CLI 文档。

### v1.15.0 — 上下文成本感知与全能力点

- `context` 原子、`ContextualBandit`、session 上下文更新与成本记录。
- `quick` / `matrix` Python 实现与薄壳；`bandit` 上下文推演；`advise` 上下文发现。
- 策略测试、`strategy.md`、全量文档与发布。

两版都可独立运行：v1.14.0 默认行为与现版一致，只多出可选臂；v1.15.0 默认策略仍可用 `--policy fixed` 退回无上下文行为。

## 11. 风险与限制

- 外部内核的质量取决于适配器；仓库只能保证协议与失败可见，不能保证渲染成功。
- 上下文按响应类粗分，不是完整八形态；形态误判会带来选臂偏差，因此不做自动硬过滤。
- 成本权重是经验值，不同站点耗时分布差异大；默认保守，读结论要看快照里的成本与样本数。
- `quick` / `matrix` 重写存在行为回归风险，用本地 server 对照旧输出列与退出码。
- 两版之间文档与测试必须同步；v1.14.0 不暴露上下文参数，避免半成品接口。
