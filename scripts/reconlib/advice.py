"""advice — 从证据行推出“下一步做什么”，供 Agent 或 LLM 消费。

只做规则化建议，不联网、不执行命令。建议永远绑定它看到的计数与路由，
并给出停止条件；没有证据就建议补样本，而不是猜。

优先级遵循先分诊：业务状态（auth / missing）先于反爬，反爬里先分限频，
再看是否换客户端臂；噪声单列，不参与归因。
"""

from .evidence import OUTCOMES

NON_NOISE = ("ok", "degraded", "blocked", "auth", "missing")
FAILURE_ORDER = ("auth", "missing", "blocked", "degraded")


def _tally(rows):
    counts = {k: 0 for k in OUTCOMES}
    for row in rows:
        outcome = row.get("outcome")
        if outcome not in counts:
            raise ValueError(f"未知 outcome: {outcome!r}")
        counts[outcome] += 1
    return counts


def _sample_url(rows):
    for row in rows:
        if row.get("final_url"):
            return row["final_url"]
        if row.get("url"):
            return row["url"]
    return "<url>"


def _arms(rows):
    seen = []
    for row in rows:
        arm = row.get("arm")
        if arm and arm not in seen:
            seen.append(arm)
    return seen


def _routes(rows):
    out = {}
    for row in rows:
        route = row.get("route") or "/"
        got = out.setdefault(route, {k: 0 for k in OUTCOMES})
        got[row["outcome"]] += 1
    return [{"route": route, "outcomes": got} for route, got in sorted(out.items())]


def _contexts(rows):
    out = {}
    for row in rows:
        ctx = row.get("context")
        if ctx:
            out[ctx] = out.get(ctx, 0) + 1
    return out


def _plan(action, layer, countermeasure, why, confidence, commands, stop):
    return {
        "action": action,
        "layer": layer,
        "countermeasure": countermeasure,
        "why": why,
        "confidence": confidence,
        "commands": list(commands),
        "stop": list(stop),
    }


def recommend(rows, evidence_path="<evidence.jsonl>"):
    """返回 {"evidence": ..., "findings": ..., "next": ...}。"""
    rows = list(rows or [])
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("证据行必须是对象")
        if row.get("kind") == "trial":
            raise ValueError("这是试验行（kind: trial），advise 只吃证据行")

    counts = _tally(rows)
    n = len(rows)
    noise = counts["noise"]
    den = n - noise
    arms = _arms(rows)
    url = _sample_url(rows)
    evidence = {
        "n": n,
        "routes": len(_routes(rows)),
        "outcomes": counts,
        "noise_excluded_n": den,
        "arms": arms,
        "contexts": _contexts(rows),
    }
    findings = []

    if n == 0:
        findings.append({"code": "no-evidence", "count": 0,
                         "message": "没有任何证据行，先跑一次可行性快筛"})
        return {
            "evidence": evidence,
            "findings": findings,
            "next": _plan(
                "collect-more", "L0", "S0",
                "没有证据，任何归因都是猜测",
                "observed",
                ["recon quick --from urls.txt --selector '#content'",
                 f"recon canary {url} --passes 5"],
                ["拿到至少 3 条证据行再判断"]),
        }

    if den == 0:
        findings.append({"code": "noise-only", "count": noise,
                         "message": "全部是链路 / 5xx 噪声，没有可归因样本"})
        return {
            "evidence": evidence,
            "findings": findings,
            "next": _plan(
                "separate-noise", "L1", "S0",
                "基础设施噪声不是反爬；先修链路或等上游恢复",
                "observed",
                ["recon doctor",
                 f"recon quick {url} --selector '#content'"],
                ["噪声持续时不要换客户端臂，也不要把噪声算进拦截率"]),
        }

    non_noise = {k: counts[k] for k in NON_NOISE}
    dominant = max(FAILURE_ORDER, key=lambda k: (non_noise[k], -FAILURE_ORDER.index(k)))
    dom_count = non_noise[dominant]
    share = dom_count / den

    for code in ("auth", "missing", "blocked", "degraded"):
        if counts[code]:
            findings.append({"code": code, "count": counts[code],
                             "message": {
                                 "auth": "出现鉴权要求，属于业务状态",
                                 "missing": "内容不存在或路由不存在",
                                 "blocked": "出现被拦截样本",
                                 "degraded": "拿到响应但内容不足",
                             }[code]})
    if noise:
        findings.append({"code": "noise", "count": noise,
                         "message": "链路 / 5xx 噪声，已从分母剔除"})

    if counts["ok"] == den:
        return {
            "evidence": evidence,
            "findings": findings,
            "next": _plan(
                "maintain", "L7", "S0",
                f"{den} 条可归因行全部拿到内容；转为防回归",
                "observed",
                [f"recon canary {url} --passes 5 --save recon/<代号>/baseline.json",
                 f"recon stats {evidence_path}"],
                ["内容区或可访问性指标下降超过阈值时再启动排查"]),
        }

    if share < 0.5:
        return {
            "evidence": evidence,
            "findings": findings,
            "next": _plan(
                "collect-more", "L7", "S0",
                f"最大一类只占 {share:.0%}，结局分散，先补样本再归因",
                "observed",
                [f"recon canary {url} --passes 5",
                 f"recon crawl --from urls.txt --selector '#content' --max-pages 20"],
                ["样本达到至少 3 pass 或 20 页后再看主导结局"]),
        }

    if dominant == "auth":
        return {
            "evidence": evidence,
            "findings": findings,
            "next": _plan(
                "check-authorization", "L5", "S7",
                "多数失败是鉴权要求，不是反爬；换栈无解",
                "observed",
                ["# 使用站点方授权的凭证或官方接口，不要用更多客户端臂试探"],
                ["无合法授权时停止该路由", "不要把登录墙记成反爬拦截"]),
        }

    if dominant == "missing":
        return {
            "evidence": evidence,
            "findings": findings,
            "next": _plan(
                "check-routing", "L0", "S0",
                "多数失败是内容不存在；先确认 URL 与路由口径",
                "observed",
                [f"recon quick {url} --selector '#content'",
                 f"recon shape {url} --explain"],
                ["路由本身失效时换数据源，不要继续探测"]),
        }

    if dominant == "degraded":
        return {
            "evidence": evidence,
            "findings": findings,
            "next": _plan(
                "check-extraction", "L0", "S0",
                "响应可达但内容区不足；先排除抽取下游丢失与选择器错位",
                "observed",
                [f"recon shape {url} --explain",
                 f"recon stats {evidence_path}"],
                ["内容区口径未定前不要归因为反爬"]),
        }

    # dominant == blocked
    ctx_ok = {row.get("context") for row in rows
              if row.get("outcome") == "ok" and row.get("context")}
    ctx_blocked = {row.get("context") for row in rows
                   if row.get("outcome") == "blocked" and row.get("context")}
    split = bool(ctx_ok and ctx_blocked and ctx_ok != ctx_blocked)
    if split:
        findings.append({"code": "context-split",
                         "count": len(ctx_ok | ctx_blocked),
                         "message": "同一站点在不同上下文里结局不同；换臂前先按上下文分组"})

    limited = sum(1 for row in rows if row.get("waf_no") == 6)
    if limited and limited / counts["blocked"] >= 0.5:
        return {
            "evidence": evidence,
            "findings": findings,
            "next": _plan(
                "backoff", "L6", "S4",
                "被拦样本里过半是限频信号；继续加请求只会加深限频",
                "observed",
                [f"recon crawl --from urls.txt --selector '#content' --delay 15 --max-pages 10"],
                ["Retry-After 未到不要重试", "同一 host 连续被拦 3 次即熔断"]),
        }

    ok_arms = {row.get("arm") for row in rows if row.get("outcome") == "ok"}
    blocked_arms = {row.get("arm") for row in rows if row.get("outcome") == "blocked"}
    better = sorted(blocked_arms - ok_arms)
    if ok_arms and better:
        why = (f"{', '.join(sorted(ok_arms))} 已拿到内容，"
               f"{', '.join(better)} 被拦；换臂是最便宜的候选")
        if split:
            why += "；证据里上下文结局不一致，先按上下文分组再换臂"
        return {
            "evidence": evidence,
            "findings": findings,
            "next": _plan(
                "switch-arm", "L2", "S2",
                why,
                "assumed",
                [f"recon fetch {url} --selector '#content' --rung both",
                 f"recon crawl --from urls.txt --selector '#content' --arms "
                 f"{','.join(sorted(ok_arms | blocked_arms))} --max-pages 20 "
                 f"--trial-out recon/<代号>/trials.jsonl",
                 "recon explore --trials recon/<代号>/trials.jsonl"],
                ["同 URL 单变量对照确认翻转后再推广", "不可归因的失败不要换臂",
                 "预算先按 explore 的计划分配，不要无节制重试"]),
        }

    return {
        "evidence": evidence,
        "findings": findings,
        "next": _plan(
            "differentiate", "L6", "S1",
            "被拦但尚无可归因的臂间差异；先做单变量对照，不要堆伪装",
            "assumed",
            [f"recon shape {url} --explain",
             f"recon canary {url} --passes 5",
             f"recon fetch {url} --selector '#content' --rung both",
             "recon crawl --from urls.txt --selector '#content' --max-pages 20 "
             "--trial-out recon/<代号>/trials.jsonl",
             "recon explore --trials recon/<代号>/trials.jsonl"],
            ["干净出口 + 真实浏览器仍 4xx 时停手，转为降级或换数据源",
             "先补样本再换臂；explore 不绕过 robots 与节流"]),
    }
