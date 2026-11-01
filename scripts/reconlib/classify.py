"""classify — 一次响应观测 → 证据行 outcome + WAF 机制类别。

输出直接对接 stats 的 outcome 词表（ok|degraded|blocked|auth|missing|noise），
并回答两个后续编排要用的问题：
    attributable  这次差异能不能归到“客户端栈”上（限频、鉴权、内容不存在不能）
    rate_limited  是不是速率/配额信号（下一臂换栈没用，应先降速）

只描述机制、不指认厂商；类别编号与 references/waf-classes.md 一致。
"""

import re

from .measure import content_len, is_html

# 通用拦截/验证特征词：不绑定具体厂商
CHALLENGE = re.compile(
    r"captcha|challenge|verify you are human|are you a robot|unusual traffic|"
    r"access denied|access restricted|request blocked|人机验证|安全验证|访问验证|"
    r"滑动验证|请完成验证|环境异常|异常访问",
    re.I,
)

# curl 的链路类退出码：DNS / 连接 / 超时 / TLS 握手
LINK_RC = re.compile(r"rc=(6|7|28|35)\b")


def _result(outcome, no, name, evidence, attributable=True, rate_limited=False):
    return {
        "outcome": outcome,
        "waf_no": no,
        "waf_class": name,
        "evidence": list(evidence),
        "attributable": attributable,
        "rate_limited": rate_limited,
    }


def classify(observation, selector="", min_content=200, content=None):
    """observation: {status, bytes, ctype, headers, body, error}。

    content 可由调用方预先量好（避免重复解析）；不给则在 HTML + 有选择器时自量。
    """
    body = observation.get("body") or b""
    if isinstance(body, str):
        body = body.encode("utf-8")
    text = body.decode("utf-8", "replace")
    status = observation.get("status") or 0
    headers = observation.get("headers") or ""
    ctype = observation.get("ctype") or ""
    error = observation.get("error")
    size = len(body)
    html = is_html(ctype)
    if content is None and html and selector:
        content = content_len(body, selector)

    evidence = []
    if error:
        evidence.append(error)
        if LINK_RC.search(error):
            return _result("noise", 1, "边缘/链路不可达", evidence, attributable=False)
        return _result("noise", 0, "未归类", evidence, attributable=False)

    if CHALLENGE.search(text):
        evidence.append("响应体含验证/挑战特征")
        if re.search(r"滑块|请完成[^，。；]{0,6}验证|captcha", text, re.I):
            return _result("blocked", 4, "交互验证", evidence)
        return _result("blocked", 3, "JS 挑战 / 计算型", evidence)

    if status in (403, 406, 451) and size < 1024:
        evidence.append(f"HTTP {status} 且响应体 {size} 字节")
        return _result("blocked", 1, "边缘硬阻断", evidence)
    if status == 429 or "retry-after" in headers.lower():
        evidence.append(f"HTTP {status}，含 Retry-After" if status == 429 else "含 Retry-After")
        return _result("blocked", 6, "速率与配额", evidence,
                       attributable=False, rate_limited=True)
    if status in (401, 407):
        evidence.append(f"HTTP {status}")
        return _result("auth", 0, "需授权（不是 WAF）", evidence, attributable=False)
    if status in (404, 410):
        evidence.append(f"HTTP {status}")
        return _result("missing", 0, "内容不存在（不是 WAF）", evidence, attributable=False)

    if 200 <= status < 300:
        if not html:
            if size > 0:
                return _result("ok", 0, "已拿到内容", evidence)
            evidence.append("HTTP 2xx 但响应体为空")
            return _result("degraded", 5, "无感评分 / 降级", evidence)
        if selector:
            if isinstance(content, int) and content >= min_content:
                return _result("ok", 0, "已拿到内容", evidence)
        elif size >= min_content * 4:
            return _result("ok", 0, "已拿到内容", evidence)
        if "<script" in text.lower() and size >= 1024:
            evidence.append(f"静态 HTML {size} 字节、有脚本、无足量正文")
            return _result("blocked", 3, "JS 挑战 / 计算型", evidence)
        shown = content if content is not None else "-"
        evidence.append(f"HTTP {status} 但内容区 {shown} 低于 {min_content}")
        return _result("degraded", 5, "无感评分 / 降级", evidence)

    if status >= 500:
        evidence.append(f"HTTP {status}")
        return _result("noise", 0, "未归类", evidence, attributable=False)
    evidence.append(f"HTTP {status}")
    return _result("blocked", 0, "未归类", evidence, attributable=False)
