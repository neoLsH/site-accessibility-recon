# Agent 上下文预算：有界、可复用的 brief 契约

日期：2026-11-15
状态：已实现（v1.23.0）

## 背景

这个仓库的产物是给 Agent 读的：`profile.yaml`、`evidence.jsonl`、`stats --json`、`advise --json`、`report.md`。每个都只对一类问题给出答案，但没有一个回答 Agent 每轮真正要问的两个问题：**这一轮该读多少**、**和上一轮比有没有变**。

现状的代价可以量出来（示例工作区 `assets/example`）：

| 原料 | 字符数 | 说明 |
|---|---|---|
| `profile.yaml` | 1 005 | 新目标还会更长 |
| `evidence.jsonl` | 502 | 真实工作区是数百行 |
| `stats --json` | 2 305 | 随路由数线性增长 |
| `advise --json` | 947 | 固定量级 |
| 合计 | **4 759** | 还没算 `agent.json` / `policy.json` / 必读文档 |

（字符数为 `len(text)`，不是字节数；中文按 1 字符计。）

再叠加 31 份 references 共 108951 字符，Agent 只能凭 SKILL.md 的路由表猜「该读哪份」——表里没有成本，也没有「这次动作是否真的需要读文档」。

## 目标

新增只读命令 `recon brief`：

1. 把一个工作区收敛成**有界**的上下文包：`--budget` 以 JSON 序列化字符数计，超预算按固定优先级裁剪，并显式报告 `used` / `dropped` / `truncated`。
2. 输出**字节稳定**、无时间戳、无绝对路径、无随机序，可直接作为 prompt cache 的稳定前缀。
3. 给出 **`digest`**：同内容的指纹。Agent 下一轮带 `--expect-digest` 命中时只回 ~120 字符，不重发正文——这是跨轮复用判据，且不需要任何磁盘缓存。
4. 按 `advise` 的动作只指路 **≤2 份必需文档**，字符成本运行时实测，避免「先读 3 份共 30 KB 再发现只要 1 份」。

## 非目标

- 不做语义摘要、不调用 LLM、不做「AI 压缩」。brief 只重排与裁剪既有结构化结果，不改写计数。
- 不引入磁盘缓存目录、TTL、常驻状态；复用判据由调用方持有 digest 完成。
- 不新增第二份统计、归类或建议逻辑：一律复用 `advice.recommend` / `stats.block` / `report.load_profile`。
- 不替代 `report`（人读报告）、`plan`（档案推导计划）、`advise`（动作契约）。
- 不改 `advise/1` / `agent/1` / `brief/1` 之外的既有契约。

## 已确认的决策

1. 命令名 `brief`，挂在 `recon.py` 的 `TOOLS` 表，参数透传风格与其它子命令一致。
2. 输入是一个工作区目录（`recon/<代号>/`），按约定文件名发现产物；文件缺失就跳过该节并写进 `gaps`，不报错。
3. 预算口径用字符（客观、不依赖 tokenizer）；另给 `tokens_est`，显式标注 estimate。
4. `digest` 覆盖**未裁剪**简报的全部信息内容，不包含预算视图；`--budget` 不影响 digest。
5. `--expect-digest` 命中时短路输出，未命中时输出全量并在 `cache.hit` 标 `false`。
6. 不提供 `--budget-tokens`、不提供 `recon cache` 子命令（YAGNI；命中率由调用方按 hit/miss 自计）。

## 架构

```
recon brief <workspace>
   ├─ evidence.read_jsonl      → rows
   ├─ advice.recommend(rows)   → evidence / findings / next     （原样复用）
   ├─ stats.block(rows)        → metrics.overall / routes       （原样复用）
   ├─ report.load_profile()    → gates / gaps                   （只读派生）
   ├─ policy.json / agent.json / trials.jsonl → 计数与停止原因   （只回显计数）
   └─ reconlib/brief.py        → 组装 → digest → 预算裁剪 → 渲染
```

新增两个文件：`scripts/reconlib/brief.py`（全部逻辑，纯函数、无 IO 副作用）与 `scripts/brief.py`（参数解析与输出）。CLI 只做编排，符合仓库「原子层在 reconlib，CLI 不重写度量」的规则。

## 契约 `brief/1`

```json
{
  "schema": "brief/1",
  "command": "brief",
  "ok": true,
  "digest": "sha256:<64 hex>",
  "cache": {"hit": true},
  "verdict": {
    "action": "collect-more",
    "layer": "L7",
    "countermeasure": "S0",
    "confidence": "observed",
    "why": "最大一类只占 29%，结局分散，先补样本再归因",
    "commands": ["recon canary <url> --passes 5"],
    "stop": ["样本达到至少 3 pass 或 20 页后再看主导结局"]
  },
  "evidence": {
    "n": 8, "noise_excluded_n": 7, "routes": 3,
    "outcomes": {"ok": 4, "degraded": 2, "blocked": 1, "auth": 0, "missing": 0, "noise": 1},
    "arms": [],
    "trials": {"n": 4},
    "policy": {"contexts": 2, "arms": 3},
    "agent": {"stop": "max-rounds", "rounds": 2}
  },
  "metrics": {
    "overall": {"n": 8, "access_rate": 0.857, "access_ci": [0.487, 0.974],
                "complete_rate": 0.667, "complete_ci": [0.3, 0.903]},
    "routes": [
      {"route": "/article/2", "n": 3, "access_rate": 1.0, "complete_rate": 0.333},
      {"route": "/article/3", "n": 2, "access_rate": 0.0, "complete_rate": null}
    ]
  },
  "gates": {"first_fail": {"name": "请求语义", "layer": "L3"},
            "pass": 1, "fail": 1, "unknown": 0},
  "gaps": ["metrics.baseline 未测", "请求语义门无证据"],
  "next_reads": [
    {"path": "references/evidence-matrix.md", "chars": 2987, "why": "collect-more：先补样本再归因的实验设计"}
  ],
  "artifacts": {"profile": "profile.yaml", "evidence": "evidence.jsonl", "trials": "trials.jsonl"},
  "budget": {"limit": 2000, "used": 1240, "dropped": [], "tokens_est": 410},
  "truncated": false
}
```

字段规则：

| 字段 | 规则 |
|---|---|
| `digest` | 见「digest 算法」；永远存在，不可能被裁剪 |
| `cache` | 仅当传了 `--expect-digest` 时出现；`hit=true` 时其余字段一律省略 |
| `verdict` | 原样复用 `advise.next`，不改写 `action` / `why` / `confidence` |
| `evidence` | 复用 `advise.evidence`；`trials` / `policy` / `agent` 只给计数与停止原因 |
| `metrics.overall` | 复用 `stats.block`；`access_ci` / `complete_ci` 保留 Wilson 区间 |
| `metrics.routes` | 每条带 `n` / `access_rate` / `complete_rate`；按「最差在前」排序，裁剪时留头尾 |
| `gates` | 从 `profile.gates` 数 `status`；`first_fail` 取档案书写顺序里第一个 `fail`（模板按层序书写） |
| `gaps` | 从档案派生，≤3 条：`metrics.baseline` 为空、`gates` 缺 `evidence`、`plan.level` 缺失、缺 profile |
| `next_reads` | 见「文档路由」；`chars` 为运行时实测文件大小 |
| `artifacts` | 只给文件名，不给路径与内容 |
| `budget` | `used` 为**不含 `budget` 节**的 JSON 缩进输出字符数（避免自引用）；`tokens_est` 为估算 |
| `truncated` | 核心字段已无法再压仍超预算时为 `true` |

文本模式渲染同一份裁剪后对象（~40 行 Markdown），不引入第二套裁剪规则。

## 计算规则

### metrics.routes 排序与裁剪

排序：`access_rate` 升序 → `complete_rate` 升序（`null` 视为最差）→ `route` 字典序。裁剪时保留前 3 与后 3（不足则全留），其余弃掉并在 `dropped` 记 `metrics.routes[6:]`。

### gates / gaps

`first_fail` 用与 `plan.py` 相同的 layer 排序（L0 < L1 < … < L7 < L4.5 由 `layer_key` 决定，直接复用该函数，不复制一份）。`gaps` 只报「档案明确缺失」的项，不对站点下判断。

### 文档路由

`next_reads` 按 `verdict.action` 固定映射，最多 2 条，`chars` 运行时从 `references/` 实测（文件改名/变长不会让文档失真）：

| action | 文档 |
|---|---|
| `collect-more` | `evidence-matrix.md` |
| `separate-noise` | `triage-and-gates.md` |
| `check-authorization` | `layers.md` |
| `check-routing` | `triage-and-gates.md` |
| `check-extraction` | `triage-and-gates.md` + `metrics.md` |
| `backoff` | `strategy.md` |
| `switch-arm` | `strategy.md` + `arms.md` |
| `differentiate` | `detection-surface.md` |
| `maintain` | `review-scorecard.md` |

映射只覆盖既有动作词表；新增动作必须先扩动作表与测试，`brief` 遇到未知动作时退化为 `next_reads: []` 并在 `gaps` 记一条，不猜。

### digest 算法

```
LOGIC = "brief/1:r1"                       # 简报逻辑版本，改算法即手动更新
body  = 未裁剪简报去掉 digest / cache / budget / truncated
digest = "sha256:" + sha256(
    LOGIC + "\n" + canonical(body)
).hexdigest()
```

`canonical()` 用 `json.dumps(..., sort_keys=True, ensure_ascii=False, separators=(",", ":"))`。

- 证据行进入 `evidence` / `metrics` 前先按 `(route, outcome, arm, context)` 聚合，**行序不影响 digest**；重跑同一内容、打乱行序，digest 与输出都不变。
- 语义字段（如 `outcome`）变化必然改变 digest；`elapsed_ms` / 时间戳不进聚合，自然不影响。
- `--budget` 不影响 digest（digest 基于未裁剪 body），所以「换个预算看同一份简报」不会让缓存失效。
- 放进 digest 的 `next_reads[].chars` 使仓库文档更新会触发失效——这是有意的：读文档的计划变了。

### 预算裁剪顺序

`--budget` 默认 2000，最小 200。裁剪按下列固定顺序执行，每一步后重新序列化计算字符数，达标即停：

1. `metrics.routes` 只留最差与最好各 3 条
2. `evidence.outcomes` 删零值项
3. `verdict.commands` 与 `verdict.stop` 各只留 1 条
4. `gaps` 只留 1 条
5. `evidence.trials` / `policy` / `agent` 只保留 `n` 或 `stop`
6. `next_reads` 只留第一条

**永不裁剪**：`digest` / `verdict.action|layer|countermeasure|confidence|why` / `evidence.n` / `evidence.noise_excluded_n` / `evidence.outcomes` 非零项 / `metrics.overall` / `budget.limit`。六步走完仍超预算，置 `truncated: true`，输出核心字段并退出码 0——宁可标截断，也不假装装得下。

### tokens_est

```
cjk   = 中日韩字符数（U+3400–U+9FFF / U+3040–U+30FF / U+AC00–U+D7AF）
tokens_est = round(cjk * 1.0 + (chars - cjk) / 3.5)
```

只作量级参考，输出里字段名就叫 `tokens_est`，文档写明是 estimate；验收以字符数为准。

## CLI

```bash
recon brief recon/<代号>                       # 人读 Markdown
recon brief recon/<代号> --json                # 机器读，单对象
recon brief recon/<代号> --budget 1200         # 更紧的上下文
recon brief recon/<代号> --expect-digest sha256:…   # 命中则只回 digest
```

退出码：`0` 正常（含 `truncated` 与 `cache.hit`）；`2` 输入错误（目录不存在、证据行混入 `kind: trial`、JSON 无法解析、`--budget` < 200 或非整数）。

## 测试

`tests/test_brief.py`（全部本地 fixture，无外网）：

1. 空工作区 → `collect-more`、`gaps` 提示缺档案、`truncated=false`
2. 示例工作区 → schema / 字段齐全、`metrics.overall` 与 `stats --json` 一致
3. 同输入跑两次 → stdout 字节完全一致
4. 证据行打乱 → digest 与输出完全一致
5. `outcome` 改一条 → digest 改变
6. `elapsed_ms` 改一条 → digest 不变
7. `--budget` 换值 → digest 不变、`used` 改变
8. 小预算（800）→ `dropped` 非空、核心字段仍在、序列化字符数 ≤ 800
9. 极小预算（200）→ `truncated=true`、核心字段仍在
10. `--expect-digest` 命中 → `cache.hit=true`、输出 ≤ 200 字符、不含 `verdict`
11. `--expect-digest` 未命中 → `cache.hit=false` 且输出全量
12. 证据行混入 `kind: trial` / 目录不存在 → 退出码 2
13. 档案缺 `gates` / 缺 profile → 不崩，`gaps` 有说明

`self-check.py` 增加 brief 冒烟门禁：示例工作区在默认预算内、两次运行字节一致、digest 长度与格式正确。

## 文档同步

- `SKILL.md`：按需加载表加一行、常用脚本加一行、`metadata.version` → 1.23.0
- `AGENTS.md`：脚本清单 + 一段复用循环说明
- `references/cli.md`：命令、选项、退出码、`brief/1` 字段
- `references/agent.md`：`brief/1` 契约、复用循环（digest → 命中短路）、prompt-cache 稳定性说明
- `assets/workspace/README.md`：工作区用法补一条
- `README.md` 状态、`CHANGELOG.md` 1.23.0 条目
- `references/forms.md`：在四种形态的分工里补一句「brief 是 Agent 形态的入口上下文」

## 风险与取舍

- **与 report 重叠**：report 是人读长文，brief 是 Agent 读的有界包；两者都从 profile 派生，但 brief 复用 `stats` / `advise` 函数，不产生第二份口径。
- **token 估算不精确**：不引入 tokenizer 依赖，字段名与文档都标 estimate；验收以字符数为口径。
- **裁剪可能丢掉关键路由**：保留最差与最好各 3 条已覆盖异常侧；`dropped` 明示丢了什么，调用方可加大 `--budget`。
- **digest 含文档字符数**：仓库更新会让旧 digest 失效，代价是重读一次简报，换来「读文档计划确实变了」的正确性。
- **YOLO 无关**：本设计与成就无关；成就路径是 PR 合并流程的外部动作。

## 验收标准

1. `python3 scripts/self-check.py` 零失败；`python3 -m unittest discover -s tests` 全绿，新增测试 ≥ 13 个。
2. 示例工作区默认预算下：原料 4 759 字符 → `brief --json` 1 954 字符（降幅 58.9%，`budget.used` 1 689，`tokens_est` 519）。设计初稿估的「≤1 500（≥60%）」基于更长的假设档案；实测下永不裁剪的核心字段加缩进序列化，下限约 1.8k，故以实测值为准。
3. 两次运行字节一致；证据行打乱后 digest 与输出一致。
4. `--expect-digest` 命中输出 ≤ 200 字符，且不含 `verdict` / `metrics`。
5. 文档与版本号同步，自检的版本 / changelog 门禁通过。
