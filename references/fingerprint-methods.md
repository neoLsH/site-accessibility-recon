# 指纹分析的手法

目标不是「拿高分」，而是回答**目标站到底看哪一项**。流程固定四步：采集 → 自洽 → 差分 → 归因。跳过任何一步，都会把无关差异当成「被识别」。

## 1 采集：同页面、同时刻、同一客户端

- 同一 URL、同一状态（登录/匿名要分开）、尽量 1 分钟内完成两侧采集，避免服务端策略漂移。
- 两侧都要采：**真实浏览器**（baseline）与**你的客户端**（被测）。
- 一次拿全量，用 [fingerprint-probe.js](../scripts/fingerprint-probe.js)，不要逐项手抄。

```js
// 页面上下文
await fingerprintProbe()

// CDP Runtime.evaluate
expression: "(async () => JSON.stringify(await fingerprintProbe()))()"
awaitPromise: true, returnByValue: true
```

## 2 自洽：先证明「像一个身份」

| 维度 | 期望关系 | 矛盾的典型含义 |
|---|---|---|
| UA ↔ `navigator.platform` | Windows UA ↔ `Win32` | 只改了 UA，没动平台 |
| UA ↔ Client Hints | 版本号、平台一致 | 只补了一半请求头 |
| UA ↔ WebGL 图形栈 | Windows UA ↔ Direct3D 系 | 图形栈来自内核默认身份 |
| 移动 UA ↔ `maxTouchPoints` | > 0 | 桌面内核套移动 UA |
| `screen` ↔ `outer` ↔ `inner` | 依次递减 | 虚拟显示或未设几何 |
| `locale` ↔ `language` | 主语言一致 | 只改了其中一个 |
| `screen.colorDepth` | ≥ 24 | 虚拟显示特征 |
| 时区 ↔ 出口地域 | 大致一致 | 需用出口回显的 geo 判断，页面内测不出来 |

自洽只说明「没有明显矛盾」，**不说明能过**。见 [layers.md](layers.md) 的 L4。

## 3 差分：和真实浏览器逐字段比

三组对照，缺一不可：

1. baseline：真实浏览器
2. 全量：你的客户端，未做任何修改
3. 单变量：只改你认为有问题的那一项

只有第 3 组相对第 2 组发生**目标站层面**的翻转，这项差异才算被利用。字段层面的不同本身不是证据。

## 4 归因：现象 → 层

| 现象 | 层 | 先查 |
|---|---|---|
| 同 UA 换栈结果翻转 | L2 | ClientHello / H2 参数 |
| 同栈换 UA 翻转 | L3 | 请求头一致性 |
| 真实浏览器过、自动化不过 | L4 | 运行时卫生、CDP |
| 只有带交互才过 | L4.5 | 行为时序 |
| 同一环境两次结果不同 | L7 | 概率门 / 稳定性 |

## 具体手法

### TLS ClientHello

- 读数：JA3 / JA3N / JA4。**同 UA 不同栈**才是有效对照。
- 命令：`scripts/client-matrix.sh <url> --tls-echo`。
- 陷阱：同一份 curl 在 macOS（LibreSSL）与 Linux（OpenSSL）指纹不同；TLS 1.3 下 JA3 受扩展顺序影响更大。

### H2 与协议参数

- 看 SETTINGS 帧的参数与顺序、窗口大小、伪头顺序。
- headless 内核常见缺口：缺 GREASE、多出 `ENABLE_PUSH`、并发流上限不同。
- 判据：TLS 层已确认放行、但目标仍拒，才轮到 H2。

### JS 指纹

- **canvas**：同一环境连跑两次应得到相同哈希；不同说明噪声补丁过度，反而暴露。
- **WebGL**：`vendor` / `renderer` 要与 UA 平台同源（Windows UA 不该出现 Apple 图形栈）。
- **字体**：宽度差分法（探针已内置），命中数量应与操作系统类型相符。
- **audio**：`OfflineAudioContext` 渲染后取固定区间的和；被噪声化的环境每次值都不同。
- **WebRTC**：只看候选类型。host 候选未使用 mDNS、或出现 srflx，说明可能绕过代理解析。

### CDP 痕迹

- 主流判据：页面在 `Error.stack` 上埋 getter，客户端 `console` 事件生成预览时会触发它。
- 次要判据：`Proxy` 的 `ownKeys` / `getOwnPropertyDescriptor` 陷阱（部分内核版本已无效）。
- 做法：**开/关你怀疑的那条 CDP 调用**各跑一次探针，对比 `stackAccessed`。
- 探针要小：不要为了探测额外引入新的全局对象或定时器。

### 稳定性

同一探针连跑 ≥3 次，记录每个哈希是否漂移。稳定是隐性要求——一个每次自变的指纹比一个固定但少见的指纹更容易被标记。

## 常见误区

- 把「字段不同」当「被识别」。不同是常态，被利用才要处理。
- 用公开检测页的分数代替目标站实测。检测页只覆盖已知向量。
- 在策略层伪造 JS 指纹，却不检查内核真身，结果两层对撞（UA 说 Mac、platform 说 Win、GPU 说 Windows）。
- 只看 webdriver，不看调试协议侧与协议参数。
- 指纹还没对齐就上行为模拟——上层没通，下层做得再好也没用。
