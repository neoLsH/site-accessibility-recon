"""fetch — 单次网络取样。

一个客户端臂 = 一套传输协议栈。这里只内置两类合规臂：
    native        curl 原生 TLS/H2 栈
    cffi:<profile> curl_cffi 仿真浏览器协议栈（需安装 curl_cffi）

不做验证码求解、不轮换出口、不伪造挑战 cookie。真实浏览器内核同样属于
“客户端臂”，但由外部渲染服务承担，不在本模块内伪装。

观测字典（普通 dict，可 JSON 化；body 除外）：
    arm, status, bytes, final_url, ctype, headers, body, error, elapsed_ms, skipped
"""

import os
import re
import subprocess
import tempfile
import time
from urllib.parse import urlsplit

from . import UA_DEFAULT


def _observation(arm, **kw):
    out = {"arm": arm, "status": 0, "bytes": 0, "final_url": "", "ctype": "",
           "headers": "", "body": b"", "error": None, "elapsed_ms": 0,
           "skipped": False}
    out.update(kw)
    return out


def parse_arm(arm):
    """'native' -> ('native', None)；'cffi:chrome' -> ('cffi', 'chrome')。"""
    name = (arm or "native").strip()
    if name == "native":
        return "native", None
    if name.startswith("cffi:"):
        profile = name[5:].strip()
        if not profile:
            raise ValueError(f"客户端臂缺少 profile: {arm!r}")
        return "cffi", profile
    raise ValueError(f"未知客户端臂: {arm!r}（应为 native 或 cffi:<profile>）")


def display_arm(arm):
    kind, profile = parse_arm(arm)
    return "curl(native)" if kind == "native" else f"curl_cffi({profile})"


def run_curl(url, ua=UA_DEFAULT, timeout=20, headers=(), cookie_file=None):
    started = time.monotonic()

    def elapsed():
        return int((time.monotonic() - started) * 1000)

    with tempfile.TemporaryDirectory(prefix="recon-fetch-") as tmp:
        body_path = os.path.join(tmp, "body")
        hdr_path = os.path.join(tmp, "hdr")
        cmd = ["curl", "-sS", "-L", "--max-time", str(timeout), "-A", ua,
               "-o", body_path, "-D", hdr_path,
               "-w", "%{http_code}\t%{size_download}\t%{url_effective}\t%{content_type}"]
        if cookie_file:
            cmd += ["-b", cookie_file, "-c", cookie_file]
        for h in headers:
            cmd += ["-H", h]
        cmd.append(url)
        out = subprocess.run(cmd, capture_output=True, text=True)
        if out.returncode != 0:
            detail = (out.stderr or "").strip()
            msg = f"curl rc={out.returncode}" + (f": {detail[:200]}" if detail else "")
            return _observation("native", error=msg, elapsed_ms=elapsed())
        parts = out.stdout.split("\t")
        if len(parts) < 4:
            return _observation("native", error="无法解析 curl 输出", elapsed_ms=elapsed())
        code, _, final, ctype = parts[0], parts[1], parts[2], parts[3]
        try:
            with open(body_path, "rb") as f:
                payload = f.read()
        except OSError:
            payload = b""
        try:
            with open(hdr_path, encoding="utf-8", errors="replace") as f:
                raw_headers = f.read()
        except OSError:
            raw_headers = ""
        return _observation("native", status=int(code or 0), bytes=len(payload),
                            final_url=final, ctype=ctype, headers=raw_headers,
                            body=payload, elapsed_ms=elapsed())


def run_cffi(url, ua=UA_DEFAULT, impersonate="chrome", timeout=20, session=None):
    arm = f"cffi:{impersonate}"
    started = time.monotonic()

    def elapsed():
        return int((time.monotonic() - started) * 1000)

    try:
        from curl_cffi import requests as creq
    except ImportError:
        return _observation(arm, error="未安装 curl_cffi", skipped=True, elapsed_ms=elapsed())
    getter = session if session is not None else creq
    try:
        r = getter.get(url, impersonate=impersonate, timeout=timeout,
                       headers={"user-agent": ua}, allow_redirects=True)
    except Exception as e:  # 网络层失败也要记录，不能静默
        return _observation(arm, error=f"{type(e).__name__}: {e}", elapsed_ms=elapsed())
    return _observation(
        arm, status=r.status_code, bytes=len(r.content), final_url=str(r.url),
        ctype=r.headers.get("content-type", ""),
        headers="\n".join(f"{k}: {v}" for k, v in r.headers.items()),
        body=r.content, elapsed_ms=elapsed())


def fetch_one(url, arm, timeout=20, ua=UA_DEFAULT, headers=(), cookies=None):
    kind, profile = parse_arm(arm)
    host = urlsplit(url).netloc or url
    if kind == "native":
        jar = cookies.curl_jar(arm, host) if cookies is not None else None
        return run_curl(url, ua=ua, timeout=timeout, headers=headers,
                        cookie_file=jar)
    session = cookies.cffi_session(arm, host) if cookies is not None else None
    return run_cffi(url, ua=ua, impersonate=profile, timeout=timeout,
                    session=session)


def save_html(out_dir, url, tag, body):
    if not out_dir or not body:
        return None
    os.makedirs(out_dir, exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9]+", "_", url)[-60:].strip("_") or "page"
    tag = re.sub(r"[^A-Za-z0-9]+", "_", tag)
    path = os.path.join(out_dir, f"{name}--{tag}.html")
    with open(path, "wb") as f:
        f.write(body)
    return path
