#!/usr/bin/env python3
"""fetch-page.py — 按客户端阶梯取一页内容，失败时给出 WAF 机制归类。

阶梯（由低到高）：
    1. native   ：curl 原生 TLS/H2 栈
    2. browser  ：curl_cffi 仿真浏览器协议栈（需要安装）
    3. (不内置) ：真实浏览器内核——脚本会给出提示，交给你的渲染服务

每一级都记录 http / bytes / 内容区 / final_url；成功即停。
全部失败时按 references/waf-classes.md 的六类机制给出归类与证据。

用法:
    scripts/fetch-page.py 'https://example.com/a' --selector '#content'
    scripts/fetch-page.py --from urls.txt --selector '#content' --out-dir recon/raw --json

边界:
    - 默认执行 robots.txt 检查；--i-have-permission 仅用于已获站点方明确许可的场景。
    - 不做验证码求解、不轮换出口、不伪造挑战 cookie。
    - 只测你有权测的目标。
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from html.parser import HTMLParser

UA_CHROME = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36")

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr"}

# 通用拦截/验证特征词：不绑定具体厂商
CHALLENGE = re.compile(
    r"captcha|challenge|verify you are human|are you a robot|unusual traffic|"
    r"access denied|access restricted|request blocked|人机验证|安全验证|访问验证|"
    r"滑动验证|请完成验证|环境异常|异常访问",
    re.I,
)


class RegionLen(HTMLParser):
    """量选择器命中的容器内可见字符数（近似）。"""

    def __init__(self, sel):
        super().__init__(convert_charrefs=True)
        self.sel = sel
        self.depth = 0
        self.active = False
        self.matched = False
        self.total = 0
        self.skip = 0

    def _match(self, tag, attrs):
        a = dict(attrs)
        if self.sel.startswith("#"):
            return a.get("id") == self.sel[1:]
        if self.sel.startswith("."):
            return self.sel[1:] in (a.get("class") or "").split()
        return tag == self.sel

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
            return
        if not self.active:
            if self._match(tag, attrs):
                self.active = self.matched = True
                self.depth = 1
        elif tag not in VOID:
            self.depth += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
            return
        if self.active and self.depth > 0:
            self.depth -= 1
            if self.depth == 0:
                self.active = False

    def handle_data(self, data):
        if self.active and self.skip == 0:
            self.total += len(data.strip())


def content_len(body, selector):
    if not selector:
        return "-"
    if not re.fullmatch(r"([A-Za-z][\w-]*)?(#[\w-]+)?(\.[\w-]+)*", selector):
        return "?unsupported"
    try:
        html = body.decode("utf-8", "replace")
    except Exception:
        return "?"
    if not html:
        return "missing"
    p = RegionLen(selector)
    try:
        p.feed(html)
    except Exception:
        pass
    return p.total if p.matched else "missing"


def is_html(ctype):
    return (not ctype) or ("html" in ctype) or ("xml" in ctype)


def run_curl(url, ua, timeout, headers=()):
    body = os.path.join(TMP, "body")
    hdr = os.path.join(TMP, "hdr")
    cmd = ["curl", "-sS", "-L", "--max-time", str(timeout), "-A", ua,
           "-o", body, "-D", hdr,
           "-w", "%{http_code}\t%{size_download}\t%{url_effective}\t%{content_type}"]
    for h in headers:
        cmd += ["-H", h]
    cmd.append(url)
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        return {"rung": "curl(native)", "error": f"curl rc={out.returncode}", "body": b""}
    parts = out.stdout.split("\t")
    if len(parts) < 4:
        return {"rung": "curl(native)", "error": "无法解析 curl 输出", "body": b""}
    code, size, final, ctype = parts[0], parts[1], parts[2], parts[3]
    with open(body, "rb") as f:
        payload = f.read()
    try:
        with open(hdr, encoding="utf-8", errors="replace") as f:
            raw_headers = f.read()
    except OSError:
        raw_headers = ""
    return {
        "rung": "curl(native)", "status": int(code or 0), "bytes": int(size or 0),
        "final": final, "ctype": ctype, "headers": raw_headers, "body": payload,
    }


def run_cffi(url, ua, impersonate, timeout):
    try:
        from curl_cffi import requests as creq
    except ImportError:
        return {"rung": f"curl_cffi({impersonate})", "error": "未安装 curl_cffi",
                "skipped": True, "body": b""}
    try:
        r = creq.get(url, impersonate=impersonate, timeout=timeout,
                     headers={"user-agent": ua}, allow_redirects=True)
    except Exception as e:  # 网络层失败也要记录，不能静默
        return {"rung": f"curl_cffi({impersonate})",
                "error": f"{type(e).__name__}: {e}", "body": b""}
    return {
        "rung": f"curl_cffi({impersonate})", "status": r.status_code,
        "bytes": len(r.content), "final": str(r.url),
        "ctype": r.headers.get("content-type", ""),
        "headers": "\n".join(f"{k}: {v}" for k, v in r.headers.items()),
        "body": r.content,
    }


def robots_allows(url, timeout):
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
    from urllib.robotparser import RobotFileParser
    rp = RobotFileParser()
    rp.parse(text.splitlines())
    return "allow" if rp.can_fetch(UA_CHROME, path) else "deny"


def classify(result, selector, min_content):
    """返回 (类编号, 名称, 证据列表)。只描述机制，不指认厂商。"""
    evidence = []
    if result.get("error"):
        evidence.append(result["error"])
        if "rc=6" in result["error"] or "rc=28" in result["error"]:
            return 1, "边缘/链路不可达", evidence
    body = result.get("body") or b""
    text = body.decode("utf-8", "replace")
    status = result.get("status") or 0
    clen = content_len(body, selector)
    headers = result.get("headers") or ""
    challenge = bool(CHALLENGE.search(text))
    script = "<script" in text.lower()
    short = len(body) < 1024

    if challenge:
        evidence.append("响应体含验证/挑战特征")
        if re.search(r"滑块|请完成验证|captcha", text, re.I):
            return 4, "交互验证", evidence
        return 3, "JS 挑战 / 计算型", evidence
    if status in (403, 406, 451) and short:
        evidence.append(f"HTTP {status} 且响应体 {len(body)} 字节")
        return 1, "边缘硬阻断", evidence
    if status == 429 or "retry-after" in headers.lower():
        evidence.append(f"HTTP {status}，含 Retry-After" if status == 429 else "含 Retry-After")
        return 6, "速率与配额", evidence
    if status in (401, 407):
        evidence.append(f"HTTP {status}")
        return 0, "需授权（不是 WAF）", evidence
    if status in (404, 410):
        evidence.append(f"HTTP {status}")
        return 0, "内容不存在（不是 WAF）", evidence
    if 200 <= status < 300:
        if isinstance(clen, int) and clen >= min_content:
            return 0, "已拿到内容", evidence
        if script and len(body) >= 1024:
            evidence.append(f"静态 HTML {len(body)} 字节、有脚本、无足量正文")
            return 3, "JS 挑战 / 计算型", evidence
        evidence.append(f"HTTP {status} 但内容区 {clen} 低于 {min_content}")
        return 5, "无感评分 / 降级", evidence
    evidence.append(f"HTTP {status}")
    return 0, "未归类", evidence


def save_html(out_dir, url, rung, body):
    if not out_dir or not body:
        return None
    os.makedirs(out_dir, exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9]+", "_", url)[-60:].strip("_") or "page"
    tag = re.sub(r"[^A-Za-z0-9]+", "_", rung)
    path = os.path.join(out_dir, f"{name}--{tag}.html")
    with open(path, "wb") as f:
        f.write(body)
    return path


def probe(url, args):
    record = {"url": url, "robots": "skipped" if args.i_have_permission else None,
              "rungs": [], "verdict": None, "waf_class": None, "evidence": []}

    if not args.i_have_permission:
        record["robots"] = robots_allows(url, args.timeout)
        if record["robots"] == "deny":
            record["verdict"] = "合规禁止"
            record["evidence"] = ["robots.txt 不允许抓取"]
            return record, False
        if record["robots"] == "unknown":
            print(f"# 警告：{url} 的 robots.txt 取不到，合规状态未知", file=sys.stderr)

    ladder = [lambda: run_curl(url, args.ua, args.timeout)]
    if not args.no_browser_stack:
        ladder.append(lambda: run_cffi(url, args.ua, args.impersonate, args.timeout))

    last_full = None
    for step in ladder:
        r = step()
        last_full = r
        r["content"] = (content_len(r.get("body") or b"", args.selector)
                        if is_html(r.get("ctype", "")) else "-")
        r["saved"] = save_html(args.out_dir, url, r["rung"], r.get("body"))
        rec_rung = {k: v for k, v in r.items() if k != "body"}
        rec_rung["bytes"] = rec_rung.get("bytes", len(r.get("body") or b""))
        record["rungs"].append(rec_rung)

        ok = 200 <= (r.get("status") or 0) < 300
        if not is_html(r.get("ctype", "")):
            got = ok and (r.get("bytes") or 0) > 0
        elif not args.selector:
            got = ok and (r.get("bytes") or 0) >= args.min_content * 4
        else:
            got = ok and isinstance(r["content"], int) and r["content"] >= args.min_content
        if got:
            record["verdict"] = "已拿到内容"
            if len(record["rungs"]) > 1:
                record["waf_class"] = "2 协议栈白名单（低阶被拦、高阶放行）"
            return record, True

    cls_no, cls_name, ev = classify(last_full or {}, args.selector, args.min_content)
    record["waf_class"] = f"{cls_no} {cls_name}" if cls_no else cls_name
    record["evidence"] = ev
    record["verdict"] = "未拿到内容"
    return record, False


def print_table(records):
    print(f"{'url':<44} {'robots':<8} {'rung':<22} {'http':<5} {'bytes':<8} {'content':<10} verdict")
    for rec in records:
        if not rec["rungs"]:
            print(f"{rec['url'][:44]:<44} {str(rec['robots']):<8} {'-':<22} {'-':<5} {'-':<8} "
                  f"{'-':<10} {rec['verdict']}")
            print()
            continue
        for i, r in enumerate(rec["rungs"]):
            if i == 0:
                url, rob = rec["url"][:44], str(rec["robots"])
            else:
                url, rob = "", ""
            status = r.get("status", "-") if not r.get("error") else "ERR"
            print(f"{url:<44} {rob:<8} {r['rung']:<22} {str(status):<5} "
                  f"{str(r.get('bytes', '-')):<8} {str(r.get('content', '-')):<10} "
                  f"{'' if i < len(rec['rungs']) - 1 else rec['verdict']}")
        if rec.get("waf_class"):
            print(f"{'':<44} {'':<8} 归类: {rec['waf_class']}  证据: {'; '.join(rec['evidence'])}")
        print()


def main():
    ap = argparse.ArgumentParser(description="按客户端阶梯取内容，失败时给出 WAF 归类")
    ap.add_argument("urls", nargs="*", help="目标 URL")
    ap.add_argument("--from", dest="from_file", help="从文件读 URL，一行一个，# 为注释")
    ap.add_argument("--selector", default="", help="内容区选择器（#id / .class / 标签名）")
    ap.add_argument("--min-content", type=int, default=200, help="内容区字符下限，默认 200")
    ap.add_argument("--ua", default=UA_CHROME)
    ap.add_argument("--impersonate", default="chrome", help="curl_cffi 的仿真目标")
    ap.add_argument("--no-browser-stack", action="store_true", help="只跑 native 一级")
    ap.add_argument("--timeout", type=int, default=20)
    ap.add_argument("--delay", type=float, default=5.0, help="URL 之间的间隔，默认 5s")
    ap.add_argument("--out-dir", help="保存每级响应体")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而不是表格")
    ap.add_argument("--i-have-permission", action="store_true",
                    help="跳过 robots 检查（仅在你已获站点方许可时使用）")
    args = ap.parse_args()

    urls = list(args.urls)
    if args.from_file:
        if not os.path.exists(args.from_file):
            sys.exit(f"找不到文件: {args.from_file}")
        with open(args.from_file, encoding="utf-8") as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if line:
                    urls.append(line)
    if not urls:
        ap.print_help()
        return 2
    urls = [u if u.startswith(("http://", "https://")) else f"https://{u}" for u in urls]

    records, any_ok = [], False
    for i, url in enumerate(urls):
        if i:
            time.sleep(args.delay)
        rec, ok = probe(url, args)
        records.append(rec)
        any_ok = any_ok or ok

    if args.json:
        print(json.dumps(records, ensure_ascii=False, indent=2, default=str))
    else:
        print_table(records)
        if not any_ok:
            print("建议：先看 references/waf-classes.md 对应类别的合规路径；"
                  "第 3/4 类请改用真实浏览器内核或官方接口，不要在本脚本上继续加参数。")
    return 0 if any_ok else 1


TMP = ""
if __name__ == "__main__":
    import tempfile
    TMP = tempfile.mkdtemp(prefix="fetch-page-")
    try:
        sys.exit(main())
    finally:
        import shutil
        shutil.rmtree(TMP, ignore_errors=True)
