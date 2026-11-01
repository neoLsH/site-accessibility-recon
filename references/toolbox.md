# 默认工具链

原则：能用 `curl` / Node 解决的，不引入 Python **第三方依赖**；Python 标准库用于编排与解析。第三方依赖只用两处——浏览器级 TLS 指纹仿真（`curl_cffi`）和报告生成的可选 YAML 解析（`pyyaml`）。量内容区、解析 robots 这类解析用 Python 标准库，不需要装包。

## 按任务选工具

| 任务 | 首选 | 备选 | 说明 |
|---|---|---|---|
| DNS / 连通性 | `dig` + `curl -v` | `nslookup` | 先看解析，再看握手 |
| TLS 版本 / 证书 / SNI | `openssl s_client` | `curl -v` | `-servername` 别省 |
| HTTP 客户端对照 | `scripts/matrix.py` | 手工 curl | 默认穷举臂，一次跑多栈并自动节流 |
| 浏览器级 TLS 指纹 | `curl_cffi`（`impersonate`） | 回显服务 | 唯一需要第三方 Python 包的地方 |
| H2 参数指纹 | 回显服务 | 本地抓包 | 自建 H2 客户端成本高，不值得 |
| 页面内指纹 | `scripts/fingerprint-probe.js` | 浏览器侧读数页 | 探针已含 CDP 泄漏检测；读数页见 fingerprint-methods.md |
| 网络栈指纹快照 | `scripts/fp-echo.py` | tls 回显 + `jq` | 归一化成可 diff 的 JSON |
| 站点 JS 采集面 | `scripts/js-surface.py` | 手工 grep bundle | 静态命中 ≠ 运行时调用，需动态确认 |
| 取内容（阶梯） | `scripts/fetch-page.py` | 手工逐级 curl | 失败时顺带给出 WAF 归类；`--rung` 只跑一级 |
| 批量取内容 | `scripts/crawl.py` | 循环 curl + 手写重试 | MAB 选臂 + 预算 / 节流 / 熔断，见 [crawl.md](crawl.md) |
| 选臂上下文与成本 | `reconlib/context.py` + `ContextualBandit` | 手写按路由分支 | 键、回退链、先验与遗忘见 [strategy.md](strategy.md) |
| 跨轮复用选臂后验 | `--policy-in` / `--policy-out`（`recon-policy/1`） | 手抄胜率 | 原子写、损坏即报错；只存计数，不含响应体 / Cookie |
| 推演选臂策略 | `scripts/bandit.py` | 直接换策略上线 | 离线比较请求开销，不发请求，见 [bandit.md](bandit.md) |
| 证据行 → 下一步动作 | `scripts/advise.py` | 自己凭感觉选下一步 | 只读、带停止条件，供 Agent / LLM 消费，见 [agent.md](agent.md) |
| 复用量测 / 归类 / 取样 | `scripts/reconlib/` | 复制脚本片段 | 原子层只有一份口径，见 [atoms.md](atoms.md) |
| 浏览器自动化 | 你的渲染服务 / Playwright | 直连 CDP | 优先复用已有渲染链路 |
| 外部浏览器内核接入 | `reconlib/render.py` 的 `cmd` / `http` 适配 | 自己包一层 CLI | 仓库不内置内核；契约见 [arms.md](arms.md) |
| 内容区度量 | `matrix.py --selector` / `--html` | 浏览器 evaluate | 整页长度不可用 |
| 可行性快筛 | `scripts/quick.py` | 手工三步 | 有界：每 URL 1 次 + 每 origin 1 次 robots；跨 URL 学选臂 |
| 结论报告 | `scripts/report.py` | 手写 | 从档案 + 证据表渲染 |
| JSON / CSV 处理 | `jq` / `awk` | `node -e` | 别为此装 Python 库 |

## 安装

```bash
# macOS
brew install curl jq openssl

# Debian / Ubuntu
sudo apt-get install -y curl jq openssl dnsutils

# 浏览器级 TLS 指纹（唯一的必需 Python 依赖）
python3 -m pip install --user curl-cffi

# 可选：report.py 优先用它解析 YAML；没有时用内置受限解析
python3 -m pip install --user pyyaml
```

先跑 `scripts/preflight.sh` 看本机缺什么、缺了会降级成什么。

浏览器内核不是本仓库的依赖：把已有的渲染服务按 `arms.json` 的 `cmd` 或 `http` 契约接进来即可。仓库只校验 JSON、16 MiB 上限与错误归 `noise`，不替适配器背书；不向适配器传 Cookie、凭证、代理或出口参数。

## 版本与环境陷阱

- **同一份 curl 在不同系统上的指纹不同**：macOS 自带 LibreSSL、Linux 多为 OpenSSL，JA3 可能完全不同。跨机器搬结论前先重测。
- **curl 版本影响 H2**：老版本不支持或默认关闭 HTTP/2，会误判「目标不支持 H2」。
- **Node 18 以下 `fetch` 不稳定**；矩阵脚本的 undici 行会给出错误结果。
- **系统 python3 可能禁止 `pip install --user`**（受管环境），此时改用虚拟环境，别硬改系统。
- **回显服务看到的是你的出口**：只把它当指纹读数，不要用它判断目标站。

## 每个工具能证明什么、不能证明什么

| 工具 | 能证明 | 不能证明 |
|---|---|---|
| 原生 curl | 该 TLS 栈是否被放行 | 真实浏览器是否可行 |
| curl_cffi | 指纹仿真的效果 | 仿真 ≠ 等同，仍有残余差异 |
| headless 浏览器 | 运行时是否被识别 | 真机行为、真实用户行为 |
| 回显服务 | 你这一侧的指纹是什么 | 目标站点如何判定这个指纹 |
| JS 采集面扫描 | 站点代码里出现了哪些采集调用 | 这些调用运行时是否执行、是否进了模型 |
| 一次成功请求 | 该路径当前可行 | 长期可行（可能只是概率门的一次命中） |
