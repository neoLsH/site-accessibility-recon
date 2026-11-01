# 端到端走查：从一条 URL 到档案、计划与报告

这条走查**不访问任何真实站点**，用仓库里的 `assets/example/site/` 跑通全链路，10 分钟内可复现。示例档案里的 L3 失败是人为注明的，只为演示 lint 与执行计划推导，**不要当成真实站点结论**。

前置：`python3`、`curl`；curl_cffi 与 node 可选，缺了 `matrix` 会降级并在输出里说明。先跑一次 `recon doctor` 看环境。

```bash
cd <skill 目录>
python3 -m http.server 8080 --directory assets/example/site
# 另开一个终端，后续命令都在仓库根目录执行
```

## 1. 定口径（L0）

先写清楚“可访问”和“完整”：

| 项 | 本次取值 |
|---|---|
| 路由集合 | `/article/`（示例站落盘为 `/article/1.html`、`/article/2.html`） |
| 内容选择器 | `#content` |
| 内容下限 | 200 字符（按真实浏览器基线取，不取最小值） |
| 成功定义 | 拿到内容区且长度达下限；噪声单列 |

## 2. 认形态

```bash
recon shape http://127.0.0.1:8080/article/1.html --explain
recon shape http://127.0.0.1:8080/article/2.html
```

第一份应报 `shape: static-html`（服务端直出），`gates:` 行显示 `static-html=yes`；第二份内容区只有一句“正文尚未加载”，报 `thin-page`。

`thin-page` 不是“被反爬”的同义词：它也可能是业务空态或软拦截，下一步要用内容选择器与真实浏览器基线把它分开。

## 3. 概率门定性 + 落基线

```bash
recon canary http://127.0.0.1:8080/article/1.html \
  --selector '#content' --passes 3 --delay 2 \
  --save /tmp/demo-canary.json
```

示例输出（本地站恒定）：

```
verdict: deterministic-pass
flips: 0  rate: 0.0  noise: 0/3
content: min 223 / median 223 / max 223
```

三 pass 都稳定才算确定性；出现翻转就按 `probability-gate` 报区间。基线文件留给第 8 步做回归对比。

注意 `shape` 的整页文本量与 `canary` 的内容区长度不是同一个数：前者含导航与页脚，后者只量 `#content`。

## 4. 客户端栈矩阵（L2–L4）

```bash
recon matrix http://127.0.0.1:8080/article/1.html \
  --selector '#content' --passes 3
```

本地站对所有栈一致放行，矩阵的用途在这里是**确认对照组**：如果连真实浏览器或默认栈都拿不到内容，就不要进入对策，先回 L1。

## 5. 双指标结算

`assets/example/evidence.jsonl` 是演示用证据行（不是本次实测结果），用来展示结算口径：

```bash
recon stats assets/example/evidence.jsonl
```

```
route       n  noise  missed  access_n  access_rate  access_ci   complete_rate  complete_ci
/article/1  3  0      0       3         1.000        0.438-1.000 1.000          0.438-1.000
/article/2  3  0      0       3         1.000        0.438-1.000 0.333          0.061-0.792
/article/3  2  1      1       0         0.000        0.000-0.793 n/a            n/a
ALL         8  1      1       6         0.857        0.487-0.974 0.667          0.300-0.903
```

读法：`/article/2` 可访问但只有 1/3 完整，说明需要查抽取或渲染，不是拦截；`/article/3` 的 5xx 已从分母剔除，不冒充“被反爬”。

### 5.1 改前改后

两份示例证据行（`evidence-before.jsonl` / `evidence-after.jsonl`）演示同一批路由的对策前后：

```bash
recon stats assets/example/evidence-before.jsonl \
  --compare assets/example/evidence-after.jsonl
```

```
== 改前 -> 改后 ==

可访问性成功率
route       before       after        delta   ci95        status
ALL         45/60 0.750  54/61 0.885  +0.135  0.000-0.274 inconclusive
/article/1  20/20 1.000  20/20 1.000  +0.000  -0.161-0.161 inconclusive
/article/2  18/20 0.900  18/20 0.900  +0.000  -0.214-0.214 inconclusive
/article/3  6/18  0.333  13/18 0.722  +0.389  0.160-0.714 improved
/article/4  1/2   0.500  3/3   1.000  +0.500  0.095-1.000 insufficient

内容完整率
route       before       after        delta   ci95        status
ALL         36/45 0.800  52/54 0.963  +0.163  0.022-0.290 improved
/article/1  17/20 0.850  19/20 0.950  +0.100  -0.114-0.310 inconclusive
/article/2  12/18 0.667  17/18 0.944  +0.278  0.044-0.542 improved
/article/3  6/6   1.000  13/13 1.000  +0.000  -0.390-0.228 insufficient
/article/4  1/1   1.000  3/3   1.000  +0.000  -0.793-0.562 insufficient

== 说明 ==
下限: 每侧分母 >= 10 才给结论；低于此值标 insufficient，区间照给
区间: Newcombe 95%（两个 Wilson 区间合成）；不含 0 才判 improved / regressed
注意: /article/3 complete: 分母低于下限 10（改前 6 / 改后 13）
注意: /article/4 access: 分母低于下限 10（改前 2 / 改后 3）
注意: /article/4 complete: 分母低于下限 10（改前 1 / 改后 3）
回归: 无
```

三处值得对照读：

- ALL 的可访问性 +0.135 但区间起点贴 0，判 `inconclusive`——点估计涨了，还不能说“显著改善”。
- `/article/3` 可访问性 `improved`，完整率却 `insufficient`（改前分母 6）：一个指标可信不代表另一个也可信。
- `/article/4` 两边都只有个位数样本，差值再大也不出方向。

只要有任一路由判 `regressed` 即退出 1；`--min-den` 可调下限，但要写进结论，别暗改。

## 6. 建档与 lint

```bash
cp -R assets/workspace/. ./recon/
cp assets/example/profile.yaml ./recon/demo/profile.yaml   # 示例，真实项目自己填
recon plan ./recon/demo/profile.yaml
```

示例档案会得到 1 条 WARN（缺改前指标）与 0 个 ERROR；执行计划从最早的失败门（L3）推出 **S1**：

```
阻塞闸门: 请求语义 (L3, fail) → S1
1. 复现对照      recon matrix http://127.0.0.1:8080/article/ --selector "#content" --passes 3
2. 概率门定性    recon canary http://127.0.0.1:8080/article/ --selector "#content" --passes 5
3. 单变量（S1）  recon fetch http://127.0.0.1:8080/article/ --selector "#content"
4. 回归          recon matrix ...
5. 回写档案      recon report ./recon/demo/profile.yaml
```

`plan` 不会替你执行高风险动作；它只把“先做哪一级、怎么验证、什么时候停”写成可核对的结构。

## 7. 报告

```bash
recon report ./recon/demo/profile.yaml --out ./recon/demo/report.md
```

报告只渲染档案里已有的内容，缺字段写“未记录”，不编造数字。外发前按 [CONTRIBUTING.md](../CONTRIBUTING.md) 脱敏。

## 8. 回归对比

改完对策后，对同一份基线复测：

```bash
recon canary http://127.0.0.1:8080/article/1.html \
  --selector '#content' --passes 3 --delay 2 \
  --compare /tmp/demo-canary.json
```

```
== 对比基线 ==
baseline: /tmp/demo-canary.json  saved 2026-11-01T13:05:38Z
verdict: deterministic-pass -> deterministic-pass  flip_delta 0.0  median_delta 0.0%
回归: 无
```

判定阈值（结论翻转 / 翻转率 +0.2 / 内容中位数 -20% / 噪声占比 +0.2）见 [cli.md](cli.md#概率门与回归基线)。再用 `stats <改前行> --compare <改后行>` 出双指标差值与 Newcombe 区间（见 [5.1](#51-改前改后)），才算验证过“有效”。

## 9. 回写与复验

- 把 `metrics.after`、`gates[].status/evidence` 与证据表对齐，再 bump `last_verified`；超过 30 天会被 lint 标为待复验。
- 结论标置信度：只观察到现象写 `observed`，能解释并复现因果写 `mechanism`，真实流量验证有效写 `efficacy`。
- 换到真实站点时，把示例里的门、路由与指标全部替换掉；示例结论不能外推。

## 这条走查没有做的事

- 没有登录、没有付费墙、没有验证码求解，也没有账号池——这些都在边界之外。
- 没有对第三方站点发请求；全部流量指向 `127.0.0.1`。
- 没有把示例数据当成测量结果：`evidence.jsonl` 只用于展示结算口径。
