#!/usr/bin/env python3
"""js-surface.py — 静态扫描站点的 JS 采集面：它到底在采集什么、往哪里上报。

回答一个问题：**前端采集了哪些信号**。它不判断这些信号有没有进决策模型——
那只能靠 ablation（拔掉一个看是否改判）来证明，见 references/detection-surface.md。

用法:
    scripts/js-surface.py https://example.com/page
    scripts/js-surface.py --html recon/raw/p1-curl.html
    scripts/js-surface.py --js recon/raw/app.bundle.js --json
    scripts/js-surface.py https://example.com/page --max-scripts 5 --all-origins

输出: 六类采集面（自动化痕迹 / 图形 / 环境 / 行为 / 上报通道 / 混淆与加密）的命中，
      加"先 hook 哪几个"的建议。命中不等于运行时会调用。

退出码: 0 正常, 2 用法错误, 3 robots.txt 禁止（未加 --i-have-permission）
"""

import argparse
import json
import os
import re
import subprocess
import sys
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

UA_CHROME = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
MAX_BYTES = 3 * 1024 * 1024

# 采集面清单：左边是给人看的名字，右边是源码里出现时的特征。
# 只收"能被 JS 读到"的信号；TLS/H2 指纹不在此列（握手早于 JS，边缘侧才看得到）。
SURFACES = [
    ("automation", "自动化痕迹", [
        ("navigator.webdriver", r"navigator\s*\.\s*webdriver"),
        ("playwright/puppeteer", r"__playwright|__puppeteer|domAutomation|\bcdc_[A-Za-z0-9]{4,}"),
        ("chrome.runtime", r"chrome\s*\.\s*runtime"),
        ("permissions.query", r"permissions\s*\.\s*query"),
        ("plugins/mimeTypes", r"navigator\s*\.\s*(plugins|mimeTypes)"),
    ]),
    ("graphics", "图形", [
        ("canvas.toDataURL", r"toDataURL\s*\("),
        ("getImageData", r"getImageData\s*\("),
        ("webgl renderer", r"WEBGL_debug_renderer_info|UNMASKED_(VENDOR|RENDERER)_WEBGL"),
        ("getSupportedExtensions", r"getSupportedExtensions\s*\("),
        ("audio", r"OfflineAudioContext|createOscillator|createAnalyser|createDynamicsCompressor"),
        ("measureText", r"measureText\s*\("),
        ("getContext(2d/webgl)", r"getContext\s*\(\s*['\"](2d|webgl2?|experimental-webgl)['\"]"),
    ]),
    ("env", "环境", [
        ("hardwareConcurrency", r"hardwareConcurrency"),
        ("deviceMemory", r"\bdeviceMemory\b"),
        ("screen geometry", r"availWidth|availHeight|colorDepth|devicePixelRatio"),
        ("Intl/timezone", r"Intl\.DateTimeFormat|resolvedOptions\s*\("),
        ("languages", r"navigator\s*\.\s*(language|languages)"),
        ("platform", r"navigator\s*\.\s*platform"),
        ("userAgentData", r"userAgentData|getHighEntropyValues"),
        ("enumerateDevices", r"enumerateDevices\s*\("),
        ("font detect", r"checkFont|fontAvailable|fontFamily\s*[:=]"),
        ("webrtc", r"RTCPeerConnection|createDataChannel|localDescription"),
        ("connection", r"navigator\s*\.\s*connection|effectiveType|downlink"),
    ]),
    ("behavior", "行为", [
        ("mouse", r"mousemove|mousedown|mouseup|mouseleave"),
        ("keyboard", r"keydown|keyup|keypress"),
        ("scroll", r"['\"]scroll['\"]|onscroll|scrollY"),
        ("touch", r"touchstart|touchmove|touchend"),
        ("visibility", r"visibilitychange"),
    ]),
    ("report", "上报通道", [
        ("sendBeacon", r"sendBeacon\s*\("),
        ("image beacon", r"new\s+Image\s*\(\s*\)"),
        ("fetch", r"\bfetch\s*\("),
        ("XMLHttpRequest", r"XMLHttpRequest"),
        ("WebSocket", r"new\s+WebSocket\s*\("),
        ("collect/report path", r"/collect|/report|/beacon|/track|/risk|/verify|/challenge"),
    ]),
    ("obfuscation", "混淆与加密", [
        ("CryptoJS", r"\bCryptoJS\b"),
        ("RSA", r"JSEncrypt|setPublicKey|\bRSA\b"),
        ("base64", r"\batob\s*\(|\bbtoa\s*\("),
        ("eval/Function", r"\beval\s*\(|new\s+Function\s*\("),
        ("_0x names", r"_0x[0-9a-f]{4,}"),
        ("hex/unicode blob", r"(?:\\x[0-9a-f]{2}){8,}|(?:\\u[0-9a-f]{4}){8,}"),
    ]),
]

# 建议顺序：先查最廉价、区分度最高的 tell
SUGGEST_ORDER = ["automation", "graphics", "env", "report"]


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.srcs = []
        self.inline = []
        self._buf = None

    def handle_starttag(self, tag, attrs):
        if tag != "script":
            return
        src = (dict(attrs).get("src") or "").strip()
        self._buf = None if src else []
        if src:
            self.srcs.append(src)

    def handle_data(self, data):
        if self._buf is not None:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._buf is not None:
            self.inline.append("".join(self._buf))
            self._buf = None


def curl(url, limit=MAX_BYTES):
    r = subprocess.run(
        ["curl", "-sS", "-L", "--compressed", "--max-time", "25",
         "--max-filesize", str(limit), "-A", UA_CHROME, url],
        capture_output=True,
    )
    if r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", "replace")


def robots_allows(url, timeout=10):
    m = re.match(r"^(https?://[^/]+)", url)
    if not m:
        return "unknown"
    origin = m.group(1)
    path = url[len(origin):] or "/"
    out = subprocess.run(
        ["curl", "-sS", "-L", "--max-time", str(timeout), "-A", UA_CHROME,
         "-w", "\n%{http_code}", f"{origin}/robots.txt"],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        return "unknown"
    text, _, code = out.stdout.rpartition("\n")
    if code.startswith(("401", "403")):
        return "deny"
    if not code.startswith("2"):
        return "allow" if code.startswith("4") else "unknown"
    rp = RobotFileParser()
    rp.parse(text.splitlines())
    return "allow" if rp.can_fetch(UA_CHROME, path) else "deny"


def scan(text, name):
    """返回 {surface_key: [(api, line, snippet)]}。"""
    found = {}
    lines = text.splitlines()
    for key, _, patterns in SURFACES:
        hits = found.setdefault(key, [])
        compiled = [(api, re.compile(rx)) for api, rx in patterns]
        for i, line in enumerate(lines, 1):
            if not line:
                continue
            for api, rx in compiled:
                if rx.search(line):
                    snippet = re.sub(r"\s+", " ", line).strip()[:110]
                    hits.append((api, i, snippet))
    return found


def summarize(found, sources):
    out = []
    for key, label, _ in SURFACES:
        hits = found.get(key, [])
        per = {}
        first = None
        for api, line, snippet in hits:
            per[api] = per.get(api, 0) + 1
            if first is None:
                first = {"api": api, "line": line, "snippet": snippet}
        out.append({
            "key": key, "label": label, "hits": len(hits),
            "apis": [{"api": a, "count": c} for a, c in
                     sorted(per.items(), key=lambda kv: -kv[1])],
            "first": first, "file": sources,
        })
    return out


def suggest(cats):
    tips = []
    for key in SUGGEST_ORDER:
        item = next((c for c in cats if c["key"] == key), None)
        if item and item["apis"]:
            tips.append((item["label"], [a["api"] for a in item["apis"][:3]]))
    return tips


def main(argv):
    ap = argparse.ArgumentParser(
        description="静态扫描站点 JS 的采集面（采集了什么、往哪上报）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="命中不等于运行时调用；用页面内代理确认后，再用 ablation 证明它进了决策模型。",
    )
    ap.add_argument("url", nargs="?", help="页面 URL")
    ap.add_argument("--html", help="离线复核：已保存的 HTML 文件")
    ap.add_argument("--js", help="直接扫一个 JS 文件")
    ap.add_argument("--max-scripts", type=int, default=8,
                    help="最多取多少个外链脚本（默认 8）")
    ap.add_argument("--all-origins", action="store_true",
                    help="也取跨域脚本（默认只取同源；风控 SDK 常在第三方域）")
    ap.add_argument("--i-have-permission", action="store_true",
                    help="跳过 robots 检查（仅在你已获站点方许可时使用）")
    ap.add_argument("--json", action="store_true", help="输出单个 JSON 对象")
    args = ap.parse_args(argv)

    if not (args.url or args.html or args.js):
        ap.print_help()
        return 2

    page_origin = urlparse(args.url).netloc if args.url else ""
    sources = []          # [{name, bytes}]
    blobs = []            # [(name, text)]
    skipped = []          # 取不到的脚本（跨域被限、或 URL 不可解析）

    if args.js:
        try:
            with open(args.js, encoding="utf-8", errors="replace") as f:
                text = f.read()[:MAX_BYTES]
        except OSError as e:
            print(f"读不到 {args.js}: {e}", file=sys.stderr)
            return 2
        name = os.path.basename(args.js)
        blobs.append((name, text))
        sources.append({"name": name, "bytes": len(text)})
    else:
        if args.html:
            try:
                with open(args.html, encoding="utf-8", errors="replace") as f:
                    html = f.read()[:MAX_BYTES]
            except OSError as e:
                print(f"读不到 {args.html}: {e}", file=sys.stderr)
                return 2
            source_label = os.path.basename(args.html)
        else:
            if not args.i_have_permission:
                verdict = robots_allows(args.url)
                if verdict == "deny":
                    print("robots.txt 不允许抓取该路径；已获许可时加 --i-have-permission",
                          file=sys.stderr)
                    return 3
                if verdict == "unknown":
                    print("# 警告：robots.txt 取不到，合规状态未知", file=sys.stderr)
            html = curl(args.url)
            if html is None:
                print(f"取页面失败: {args.url}", file=sys.stderr)
                return 2
            source_label = args.url

        coll = Scripts()
        coll.feed(html)
        for i, code in enumerate(coll.inline[:args.max_scripts], 1):
            if not code.strip():
                continue
            name = f"inline#{i}"
            blobs.append((name, code))
            sources.append({"name": name, "bytes": len(code)})

        # 离线 HTML 没有 base，外链脚本取不了；要看它们就直接给 --url 或 --js
        picked = [] if args.html else coll.srcs
        if not args.all_origins:
            picked = [s for s in picked
                      if urlparse(urljoin(source_label, s)).netloc in (page_origin, "")]
        for src in picked[:args.max_scripts]:
            absolute = urljoin(source_label, src) if not os.path.isabs(src) else src
            code = curl(absolute)
            if code is None:
                skipped.append(src)
                continue
            blobs.append((src, code))
            sources.append({"name": src, "bytes": len(code)})

    found = {}
    for name, text in blobs:
        piece = scan(text, name)
        for key, hits in piece.items():
            found.setdefault(key, []).extend(hits)

    cats = summarize(found, [b[0] for b in blobs])
    tips = suggest(cats)
    total = sum(c["hits"] for c in cats)

    if args.json:
        print(json.dumps({
            "source": args.js or args.html or args.url,
            "scripts": sources,
            "skipped": skipped,
            "total_hits": total,
            "categories": cats,
            "suggest": [{"surface": k, "apis": v} for k, v in tips],
            "note": "静态命中不等于运行时会调用；用 ablation 证明它在决策路径上",
        }, ensure_ascii=False, indent=2))
        return 0

    print(f"来源: {args.js or args.html or args.url}")
    total_bytes = sum(s["bytes"] for s in sources)
    print(f"脚本: {len(blobs)} 个（{total_bytes / 1024:.1f} KB）")
    if args.html:
        print("离线模式只扫内联脚本；外链脚本请用 --url 或单独 --js")
    if skipped:
        print(f"取不到: {', '.join(skipped[:5])}")
    print()
    if not cats:
        print("没有命中任何已知采集面。")
        return 0
    print(f"{'类':<14}{'命中':<6}{'API（次数）':<56}首个位置")
    for c in cats:
        if not c["hits"]:
            continue
        apis = " ".join(f"{a['api']}({a['count']})" for a in c["apis"])[:54]
        loc = f"{c['first']['api']} @ {c['first']['line']}"
        print(f"{c['label']:<14}{c['hits']:<6}{apis:<56}{loc}")
    print()
    if tips:
        print("建议先 hook（按判定面的优先级）:")
        for label, apis in tips:
            print(f"  {label}: {', '.join(apis)}")
    print("\n下一步: 静态命中 → 页面内装计数代理确认真的被调用 → 逐项 ablation 看是否改判")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
