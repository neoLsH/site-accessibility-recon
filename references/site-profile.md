# 站点档案 schema

一次 recon 的最终产物不是一篇报告，而是一份**机器可读、可被执行侧消费**的档案。报告给人看，档案给系统用。

## 目录约定

`sites/` 放在**你的项目根**，与 skill 目录分开；skill 目录只带模板，不存任何目标站点的档案。

```
sites/
└── example.com/
    ├── profile.yaml     # 结论与策略
    ├── evidence.csv     # 证据矩阵原始行
    └── notes.md         # 推理链、误判、未解问题
```

## profile.yaml

```yaml
host: example.com
profile_version: 1
last_verified: 2026-10-28
confidence: mechanism        # observed | mechanism | efficacy
owner: ""
sources: []                  # 参考链接或内部报告引用（不含密钥）
conclusion: "边缘只认浏览器 TLS 栈；凭证层已通过"

triage:                      # 三态分诊样本数，report.py 直接渲染
  A: 0
  B: 41
  C: 12
  noise: 3

routes:
  - pattern: "^/article/"
    kind: detail
    content_selector: "#content"   # 用于量内容区完整率
    min_content_len: 800           # 该路由正常量级的下限
    notes: "SSR 直出，无需水合"

fetcher:
  mode: http-document        # http-document | browser
  reason: "Node TLS 栈被边缘放行；浏览器模式无额外收益"
  user_agent: ""             # 留空表示用运行时默认
  headers: {}
  cookies: []                # 只写用途与生命周期，不写名称与值

network:
  proxy: none                # none | required | sticky-per-ip
  rate_limit: "1 req / 2s"
  notes: "单出口连打 6 次后进入短窗拒绝，静置约 1min 恢复"

gates:
  - name: "边缘 TLS 栈白名单"
    layer: L2
    dimension: client_stack
    orthogonal: yes          # yes | no | unknown
    status: pass             # pass | fail | assumed
    evidence: "chrome-JA3+Safari-UA=放行 / node-undici=拦截"
  - name: "一次性滚动 cookie"
    layer: L5
    dimension: credential
    orthogonal: yes
    status: pass
    evidence: "冻结重放第 2 发失效；写回链 6/6 成功"

metrics:
  baseline: null               # P0 阶段可留空；不要写一堆 0 冒充数据
  # baseline:
  #   date: 2026-09-01
  #   accessibility_sample: 120
  #   accessibility_rate: 0.21
  #   accessibility_range: "0.16-0.27"
  #   completeness_sample: 25     # 分母 = 可访问成功的样本
  #   completeness_rate: 0.86
  #   completeness_range: "0.80-0.91"
  #   pass_count: 3
  after: null
  # after: 同上结构
  inflation_audit: "抽样 20 例，原始 88%，修正 71%"

plan:
  level: S3                  # S0..S7，见 countermeasure-ladder.md
  reason: "同 UA 换 TLS 栈即可放行，不必换内核"
  cost: "延迟 +0；出口无额外消耗"
  blast_radius: "站点级"      # 站点级 | 全局

known_issues:
  - "降级软拦截下，内容区残留广告卡时长度阈值会误判为完整"
  - "概率门：同 URL 跨 pass 翻转率约 37%，未稳定"

non_goals:
  - "不逆向签名模块"
  - "不使用账号池"
```

## 规则

1. **不写密钥与站点常量。** site id、hash、签名密钥一律不入库；Cookie 只写用途与生命周期，不写名称和值。
2. **每个 `status: pass/fail` 都要有 `evidence`。** 没有证据的写 `assumed`。
3. **`last_verified` 必填。** 档案会过期，超过 30 天视为待复验。
4. **`confidence` 反映最强结论的级别**，不要因为“机制确证”就升格为“效果验证”。
5. **两个指标分开记样本与区间。** `accessibility_*` 的分母是全部请求，`completeness_*` 的分母只算可访问成功的样本；共用一个 `sample` 会让两率互相污染。没有样本量时写 `null`，不要写 0。
6. **`known_issues` 要写“未解”和“误判”，不只是成功经验。**
7. **`plan.level` 用 S0–S7 对策等级**（见 [countermeasure-ladder.md](countermeasure-ladder.md)），写清代价与影响面，否则无法判断该不该落到全局。

## 与执行侧的契约

档案只描述**结论与约束**，不描述实现：

- 执行侧读 `fetcher.mode` 决定走 HTTP 还是浏览器。
- 读 `routes[].content_selector` 与 `min_content_len` 做完整性判定。
- 读 `network` 决定出口与节流。
- 读 `gates` 做回归：任何 `pass` 的门重新变 `fail`，即触发复验。

站点级配置优先于全局默认；全局改动必须评估对全部档案的影响面。

## 生成报告

档案是源，报告是投影。不要手写报告，否则两份必然对不上：

```bash
python3 scripts/report.py --profile sites/example.com/profile.yaml \
    --evidence sites/example.com/evidence.csv \
    --out sites/example.com/report.md
```

- 只渲染档案里已有的结论，缺字段写「未记录」，不会编造数字。
- 优先用 PyYAML；没装时用内置受限解析（覆盖上面全部语法），遇到块标量等不支持写法会直接报错并提示安装，而不是猜。
- `evidence.csv` 缺省取档案同目录下的同名文件；表头用 `client,identity,route,egress,http,bytes,final_url,content_region_len,marker`。
- 报告结构固定为 [assets/report-template.md](../assets/report-template.md) 的七节，便于跨站点横向对比。
