#!/usr/bin/env python3
"""quick — 可行性快筛：每 URL 1 个页面请求（外加每 origin 1 次 robots.txt）。

用来在开深度 recon 之前先决定“哪条路值得走、哪条路直接放弃”。判定词表固定，
便于脚本消费。同一会话里跨 URL 学选臂：policy 默认 thompson，可用
--policy fixed 退回“总是第一个可用臂”。

判定词表：
  可行·HTTP      纯 HTTP 客户端就能拿到内容区
  可行·浏览器    静态 HTML 是空壳，需要浏览器执行 JS（尚未实测，属推断）
  疑似拦截       状态码/挑战页/极短响应体等反爬迹象
  需授权         401 或跳转到登录页
  不可达         DNS/连接/TLS/超时失败（L1）
  内容不存在     404/410（软 404 需按 triage-and-gates.md 复核）
  合规禁止       robots.txt 不允许
  待定           信号冲突或信息不足，需要更多样本

只测你有权测的目标；默认执行 robots.txt 检查，--skip-robots 仅用于你已获得
站点方明确许可的场景。1 次请求不足以判定概率门；要看翻转率请用 matrix --passes。

退出码：0 = 全部可由 HTTP 直接取得内容；1 = 存在需浏览器实测/进一步分析项；
2 = 用法错误。
"""

import argparse
import re
import sys

from reconlib import arms as armreg
from reconlib import fetch, measure
from reconlib.session import ArmSession

DEFAULT_ARMS = "native,cffi:chrome"

MARKERS = re.compile(
    r"captcha|challenge|verify you are human|are you a robot|unusual traffic|"
    r"access denied|access restricted|request blocked|人机验证|安全验证|访问验证|"
    r"滑动验证|环境异常|异常访问", re.I)
LOGIN_PATH = re.compile(r"/(login|signin|sign-in|auth|passport|sso)([/?#]|$)", re.I)
LOGIN_FORM = re.compile(r"type=[\"']?password", re.I)
SCRIPT = re.compile(r"<script", re.I)

HEADER = ("verdict", "http", "bytes", "content", "final_url", "url", "reason")

VOCABULARY = """\
判定词表：
  可行·HTTP    纯 HTTP 即可拿到内容区        → 走 http-document，最省
  可行·浏览器  静态是空壳、需 JS 渲染（推断）  → 需浏览器内核，先实测再定
  疑似拦截     状态码或挑战页有反爬迹象      → 按 triage-and-gates.md 做单变量隔离
  需授权       401 / 登录页                  → 走合法鉴权（S7），不绕过
  不可达       L1 链路失败                   → 先修链路/出口，别急着看指纹
  内容不存在   404/410                       → 换数据源（S0）或核对路由
  合规禁止     robots.txt 不允许             → 停；取得许可再测
  待定         信号冲突/样本不足             → 加 --selector 或多 pass 重测"""


def verdict_of(url, obs, content, selector, min_content):
    status = obs.get("status") or 0
    error = obs.get("error") or ""
    body = (obs.get("body") or b"").decode("utf-8", "replace")
    final = obs.get("final_url") or url
    ctype = obs.get("ctype") or ""
    size = obs.get("bytes") or len(body.encode("utf-8"))
    if not status or error.startswith(("curl rc=", "外部内核")):
        rc = re.search(r"rc=(\d+)", error)
        reason = {
            "6": "DNS 解析失败", "7": "连接失败（出口/端口不可达）",
            "28": "超时", "35": "TLS 握手失败（证书或栈不兼容）",
            "60": "TLS 握手失败（证书或栈不兼容）",
        }.get(rc.group(1) if rc else "", error or "链路失败")
        return "不可达", reason

    challenge = bool(MARKERS.search(body))
    login = bool(LOGIN_FORM.search(body))
    html = (not ctype) or ("html" in ctype)
    script = bool(SCRIPT.search(body))
    if status in (401, 407):
        return "需授权", f"HTTP {status}：需要凭证"
    if status in (403, 429, 451, 503):
        if challenge:
            return "疑似拦截", f"HTTP {status} 且响应体含挑战页特征"
        return "疑似拦截", f"HTTP {status}（无挑战页特征，需按分诊复核）"
    if status in (404, 410):
        return "内容不存在", f"HTTP {status}（软 404 需对照同源静态资源复核）"
    if 200 <= status < 300:
        if LOGIN_PATH.search(final):
            return "需授权", "重定向到登录页"
        if not html:
            if size > 0:
                return "可行·HTTP", f"非 HTML（{ctype or '未知类型'}），{size} 字节"
            return "待定", f"HTTP {status} 但响应体为空"
        if selector and content == "?unsupported":
            return "待定", "选择器过于复杂，改用简单选择器或用真实浏览器取值"
        if selector and isinstance(content, int) and content >= min_content:
            return "可行·HTTP", f"内容区 {content} 字符（下限 {min_content}）"
        if not selector and size >= min_content * 4:
            return "可行·HTTP", f"HTML {size} 字节（未量内容区，给 --selector 更准）"
        if login:
            return "需授权", "响应体含登录表单"
        if challenge:
            return "疑似拦截", "响应体含挑战页特征"
        if script and size >= 1024:
            return "可行·浏览器", "静态 HTML 无正文但有脚本（CSR 空壳，需实测浏览器）"
        if not selector:
            return "待定", f"HTML {size} 字节，低于下限 {min_content * 4}（给 --selector 更准）"
        if content == "missing":
            return "待定", "选择器未命中，且无 CSR 迹象（路由/结构可能变了）"
        if content == 0:
            return "待定", "内容区为空且无 CSR 迹象（降级页或抽取失败）"
        return "待定", f"内容区 {content} 低于下限 {min_content}，且无 CSR 迹象"
    if 300 <= status < 400:
        if LOGIN_PATH.search(final):
            return "需授权", "重定向到登录页"
        return "待定", f"HTTP {status} 未跟随到终态"
    return "待定", f"HTTP {status}"


def read_urls(path):
    urls = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line:
                urls.append(line)
    return urls


def build_parser():
    ap = argparse.ArgumentParser(
        description="可行性快筛：每 URL 1 请求 + robots，跨 URL 学选臂")
    ap.add_argument("urls", nargs="*", help="目标 URL")
    ap.add_argument("--from", dest="from_file",
                    help="从文件读 URL，一行一个，# 开头为注释")
    ap.add_argument("--selector", default="",
                    help="内容区选择器（#id / .class / 标签名）；不给则只看状态码与字节数")
    ap.add_argument("--min-content", type=int, default=200,
                    help="内容区字符数下限，默认 200；低于此值不算“拿到内容”")
    ap.add_argument("--arms", default=DEFAULT_ARMS,
                    help=f"逗号分隔的客户端臂，默认 {DEFAULT_ARMS}")
    ap.add_argument("--arms-file",
                    help="外部臂配置 JSON（默认 ~/.config/site-recon/arms.json）")
    ap.add_argument("--require", default="", help="硬性能力要求，如 js（逗号分隔）")
    ap.add_argument("--impersonate", default="chrome", help="裸 cffi 臂使用的仿真目标")
    ap.add_argument("--policy", default="thompson",
                    choices=("thompson", "ucb1", "fixed", "round_robin"),
                    help="跨 URL 选臂策略，默认 thompson；fixed 恒选第一个可用臂")
    ap.add_argument("--seed", type=int, help="thompson 的随机种子（复现用）")
    ap.add_argument("--delay", type=float, default=5,
                    help="同 host 请求间隔，默认 5s")
    ap.add_argument("--cost-weight", type=float, default=0.1,
                    help="成本惩罚权重（5000ms 归一化），默认 0.1；0 关闭")
    ap.add_argument("--timeout", type=int, default=20, help="单请求超时，默认 20s")
    ap.add_argument("--ua", default=fetch.UA_DEFAULT, help="统一 User-Agent")
    ap.add_argument("--skip-robots", action="store_true",
                    help="跳过 robots.txt 检查（仅在你已获得明确许可时使用）")
    ap.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.delay < 0:
        ap.error("--delay 不能为负")
    if args.min_content < 0:
        ap.error("--min-content 不能为负")
    if args.cost_weight < 0:
        ap.error("--cost-weight 不能为负")

    urls = list(args.urls)
    if args.from_file:
        try:
            urls.extend(read_urls(args.from_file))
        except OSError:
            sys.exit(f"找不到文件: {args.from_file}")
    if not urls:
        ap.print_help()
        return 2
    urls = [u if u.startswith(("http://", "https://")) else f"https://{u}"
            for u in urls]

    try:
        registry = armreg.load(args.arms_file)
        arm_list = armreg.parse_list(args.arms, registry, args.impersonate)
        require = armreg.parse_require(args.require)
    except ValueError as e:
        ap.error(str(e))

    session = ArmSession(
        arm_list, registry=registry, policy=args.policy, seed=args.seed,
        delay=args.delay, timeout=args.timeout, ua=args.ua,
        permission=args.skip_robots, require=require,
        cost_weight=args.cost_weight)

    def warn(host):
        print(f"# 警告：{host} 的 robots.txt 取不到，合规状态未知，继续探测",
              file=sys.stderr)

    rows, bad = [], 0
    for url in urls:
        page = session.probe(
            url, selector=args.selector, min_content=args.min_content,
            max_attempts=1, require=require,
            permission=args.skip_robots, warn=warn, keep_body=True)
        if page["skipped"] == "robots-deny":
            row = ("合规禁止", "-", "-", "-", "-", url, "robots.txt 不允许抓取")
        else:
            obs = page.get("observation")
            if obs is None:
                reason = page.get("reason") or page.get("skipped") or "未发出请求"
                row = ("待定", "-", "-", "-", "-", url,
                       f"{page.get('halt') or 'no-attempt'}: {reason}")
            else:
                content = ("-" if not measure.is_html(obs.get("ctype"))
                           else measure.content_len(obs.get("body") or b"",
                                                    args.selector))
                verdict, reason = verdict_of(url, obs, content, args.selector,
                                             args.min_content)
                row = (verdict, str(obs.get("status") or "-"),
                       str(obs.get("bytes") or "-"), str(content),
                       obs.get("final_url") or url, url, reason)
        rows.append(row)
        if row[0] != "可行·HTTP":
            bad += 1

    stream = sys.stdout
    stream.write("\t".join(HEADER) + "\n")
    for row in rows:
        stream.write("\t".join(str(x) for x in row) + "\n")
    if args.skip_robots:
        print("# 已跳过 robots.txt 检查（请确认你有该站点的明确许可）",
              file=sys.stderr)
    print()
    print(VOCABULARY)
    print()
    if bad == 0:
        print(f"结论：{len(rows)}/{len(rows)} 条可直接由 HTTP 取得内容")
        return 0
    print(f"结论：{len(rows) - bad}/{len(rows)} 条可直接由 HTTP 取得内容，"
          f"{bad} 条需浏览器实测或进一步分析")
    return 1


if __name__ == "__main__":
    sys.exit(main())
