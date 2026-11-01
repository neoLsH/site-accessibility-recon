# 客户端臂：注册表与外部内核

臂是**协议栈描述**，不是绕过手段。它回答“这一页用哪套客户端去取”，不回答“怎么骗过风控”。
仓库不内置浏览器内核，也不替你维护登录态、Cookie 池或出口；外部渲染器只作为适配器接入。

## 内置臂

| 臂 | 传输 | 能力 | 可用性 |
|---|---|---|---|
| `native` | `curl` | `tls` `h2` | 始终可用 |
| `cffi:<profile>` | `curl_cffi` | `tls` `h2` `cookies` | 装了 `curl_cffi` 才可用；裸写 `cffi` 等价 `cffi:chrome` |
| `browser:<engine>` | 外部 | `tls` `h2` `js` `cookies` | **默认不可用**，要在仓库外配置 `cmd` 或 `http` 端点 |

`browser` 必须带引擎名（`browser:chromium` / `browser:webkit` / `browser:firefox`）；裸写 `browser` 是用法错误。

能力标记只用于硬过滤：`--require js` 表示“没有 JS 运行时就不要发这次请求”，不会自动升级臂，
也不会因为缺能力而放宽 robots 或节流。

## 外部配置

查找顺序（先命中先用）：

1. `--arms-file <path>`（显式给出但文件不存在 → 用法错误）
2. `$RECON_ARMS_FILE`
3. `~/.config/site-recon/arms.json`（存在才读）
4. 内置表

文件格式：

```json
{
  "arms": [
    {
      "name": "browser:chromium",
      "transport": "cmd",
      "engine": "chromium",
      "capabilities": ["tls", "h2", "js", "cookies"],
      "command": ["/opt/render/render", "--url", "{url}", "--engine", "{engine}"]
    },
    {
      "name": "browser:webkit",
      "transport": "http",
      "engine": "webkit",
      "endpoint": "http://127.0.0.1:8787/render"
    }
  ]
}
```

同名外部条目覆盖内置条目，但同名重复出现会被拒绝。`endpoint` 只允许 `http(s)`，且禁止
userinfo（`http://user:pass@host/`）——适配器不接受凭证。

## 两种传输契约

**cmd**：`command` 是 argv 列表，按原样执行，**不经 shell**。四个占位符会被替换：

```
{url}  {engine}  {ua}  {timeout}
```

`command` 必须至少包含一个 `{url}`，否则配置被拒绝。

**http**：向 `endpoint` 发 `POST`，JSON 请求体：

```json
{"url": "...", "engine": "chromium", "ua": "...", "timeout": 20}
```

两种传输的响应必须是同一个 JSON 对象：

```json
{"status": 200, "body": "<html>…</html>", "final_url": "…", "ctype": "text/html",
 "headers": "…", "elapsed_ms": 123}
```

二进制响应用 `body_b64` 代替 `body`。`status` 缺失时 http 传输用外层 HTTP 状态码补齐。

## 上限与错误

- 响应体上限 **16 MiB**；超出的部分截断并标记 `truncated`。
- 非零退出、超时、非 JSON、`body_b64` 解码失败、端点 5xx、连接失败，全部转成带 `error`
  的观测；归类层把它们记作 `noise`，不归因给客户端栈。
- 外部臂未配置时给出 `skipped`，**不计入请求预算**，也不会触发熔断。
- 外部内核的质量由适配器负责；仓库只校验契约，不为渲染结果背书。

## 安全边界

- 不向适配器传 Cookie、凭证、代理或出口参数；要登录的路径走业务授权，不走臂。
- 不提供验证码求解、账号池、凭证填充；臂之间只比较“是否拿到内容”，不比较“谁更能绕过”。
- robots 与节流在调用臂之前完成；`robots: deny` 时一个请求都不发。
- 外部命令按 argv 传递，禁止 `shell=True`；端点禁止 userinfo。

## 上下文与成本

臂的胜率不是全局一个数，而是按 `host|路由类|响应类` 分别累计；样本不足时沿
`host|route|response → host|route|* → host|*|* → *` 回退聚合。慢臂按 5000ms
归一化扣分，`--cost-weight` 默认 `0.1`，`0` 关闭；`--prior-strength` 默认 `4`
从相邻上下文借先验，`--half-life` 默认 `0`（不遗忘）。`--policy-in` / `--policy-out`
可把后验跨轮复用（`recon-policy/1`，仓库外档案）。

`degraded` 记半个奖励，`auth` / `missing` / `noise` / 限频不更新后验。完整规则、
各能力点的选臂语义与读法见 [strategy.md](strategy.md)。

## 与能力点的关系

`fetch` / `crawl` / `canary` / `quick` / `matrix` 共用同一份注册表与编排：

| 能力点 | 选臂语义 |
|---|---|
| `fetch` | 按 `--policy` 逐页选臂，默认 `fixed`（给定顺序） |
| `crawl` | 按 host 在线学习，默认 `thompson` |
| `canary` | 整个 pass 序列**固定一条臂**，否则翻转率会被选臂差异污染 |
| `quick` | 每条 URL 一次请求，跨 URL 学习，默认 `thompson` |
| `matrix` | 默认穷举全部臂，用来看翻转而不是省请求 |
| `bandit` | 离线推演，只消费胜率，不发请求 |

传 `--arms-file` 后，`--arms browser:chromium` 才会真正发请求；不配置时它会作为
`skipped` 出现在尝试序列里，而不是静默消失。
