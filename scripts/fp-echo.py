#!/usr/bin/env python3
"""fp-echo.py — 采集并比对「网络栈指纹」快照。

读数来自 tls.peet.ws/api/all（JA3/JA4、H2 SETTINGS、Akamai 指纹）。
用途是把「真实浏览器 vs 你的客户端」的网络栈差异变成可 diff 的 JSON，
而不是盯着一个分数。

用法:
    # 采集当前 curl 栈（与 client-matrix.sh 的 curl 行同栈）
    scripts/fp-echo.py --out baseline.json

    # 采集浏览器级栈（需要 curl_cffi）
    scripts/fp-echo.py --via cffi --impersonate chrome --out chrome.json

    # 比对两份快照
    scripts/fp-echo.py --diff baseline.json chrome.json

说明:
    - 回显服务看到的是**你这一侧**的指纹与出口，不代表目标站如何判定。
    - 默认不落 IP；确实要记录出口时加 --include-ip。
    - 一次采集 = 1 个请求；别把它放进高频循环。
"""

import argparse
import json
import os
import subprocess
import sys

DEFAULT_URL = "https://tls.peet.ws/api/all"


def _get(obj, *path, default=None):
    cur = obj
    for key in path:
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return default
    return cur


def fetch(url, via, impersonate, timeout):
    if via == "curl":
        out = subprocess.run(
            ["curl", "-sS", "--max-time", str(timeout), "-H", "Accept: application/json", url],
            capture_output=True, text=True,
        )
        if out.returncode != 0:
            sys.exit(f"curl 失败: {out.stderr.strip()}")
        return out.stdout
    if via == "cffi":
        try:
            from curl_cffi import requests as creq
        except ImportError:
            sys.exit("--via cffi 需要 curl_cffi（python3 -m pip install --user curl-cffi）")
        r = creq.get(url, impersonate=impersonate, timeout=timeout,
                     headers={"Accept": "application/json"})
        return r.text
    sys.exit(f"未知 --via: {via}")


def snapshot(raw, include_ip):
    data = json.loads(raw)
    frames = _get(data, "http2", "sent_frames", default=[]) or []
    settings = []
    for f in frames:
        if f.get("frame_type") == "SETTINGS":
            settings = f.get("settings") or []
            break
    snap = {
        "http_version": data.get("http_version"),
        "tls_version_negotiated": _get(data, "tls", "tls_version_negotiated"),
        "ja3_hash": _get(data, "tls", "ja3_hash"),
        "ja4": _get(data, "tls", "ja4"),
        "ja4_r": _get(data, "tls", "ja4_r"),
        "peetprint_hash": _get(data, "tls", "peetprint_hash"),
        "ciphers_count": len(_get(data, "tls", "ciphers", default=[]) or []),
        "extensions_count": len(_get(data, "tls", "extensions", default=[]) or []),
        "h2_akamai": _get(data, "http2", "akamai_fingerprint"),
        "h2_akamai_hash": _get(data, "http2", "akamai_fingerprint_hash"),
        "h2_settings": settings,
        "user_agent": data.get("user_agent"),
    }
    if include_ip:
        snap["exit"] = data.get("ip")
    return snap


def diff(a, b):
    keys = sorted(set(a) | set(b))
    same, rows = 0, []
    for k in keys:
        va, vb = a.get(k), b.get(k)
        eq = va == vb
        same += eq
        rows.append((k, va, vb, eq))
    width = max(len(r[0]) for r in rows) if rows else 0

    def fmt(v):
        if isinstance(v, (list, dict)):
            v = json.dumps(v, ensure_ascii=False)
        return str(v)

    print(f"{'字段'.ljust(width)}  {'A':<34}  {'B':<34}  结果")
    for k, va, vb, eq in rows:
        fa, fb = fmt(va), fmt(vb)
        print(f"{k.ljust(width)}  {fa[:34]:<34}  {fb[:34]:<34}  {'同' if eq else '异'}")
    differs = len(rows) - same
    print(f"\n{len(rows)} 个字段，{differs} 个不同")
    return differs


def main():
    ap = argparse.ArgumentParser(description="采集 / 比对网络栈指纹快照")
    ap.add_argument("--url", default=DEFAULT_URL, help=f"回显地址，默认 {DEFAULT_URL}")
    ap.add_argument("--via", choices=["curl", "cffi"], default="curl", help="用哪个栈发请求")
    ap.add_argument("--impersonate", default="chrome", help="--via cffi 时的仿真目标")
    ap.add_argument("--timeout", type=int, default=20)
    ap.add_argument("--include-ip", action="store_true", help="把出口 IP 写进快照")
    ap.add_argument("--out", help="写入文件；缺省打印到 stdout")
    ap.add_argument("--diff", nargs=2, metavar=("A", "B"), help="比对两份已保存的快照")
    args = ap.parse_args()

    if args.diff:
        for p in args.diff:
            if not os.path.exists(p):
                sys.exit(f"找不到快照: {p}")
        with open(args.diff[0], encoding="utf-8") as f:
            a = json.load(f)
        with open(args.diff[1], encoding="utf-8") as f:
            b = json.load(f)
        sys.exit(1 if diff(a, b) else 0)

    snap = snapshot(fetch(args.url, args.via, args.impersonate, args.timeout), args.include_ip)
    text = json.dumps(snap, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"已写入 {args.out}")
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
