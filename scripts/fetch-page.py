#!/usr/bin/env python3
"""fetch-page.py — 按客户端臂逐级取一页内容，失败时给出 WAF 机制归类。

阶梯（由低到高）：
    1. native   ：curl 原生 TLS/H2 栈
    2. cffi     ：curl_cffi 仿真浏览器协议栈（需要安装）
    3. (不内置) ：真实浏览器内核——脚本会给出提示，交给你的渲染服务

每一级都记录 http / bytes / 内容区 / final_url；成功即停。
不可归因的失败（限频、鉴权、内容不存在、链路噪声）也会停：换客户端臂救不了，
先处理那个问题，别把限频污染成“指纹被识别”。
全部失败时按 references/waf-classes.md 的六类机制给出归类与证据。

用法:
    scripts/fetch-page.py 'https://example.com/a' --selector '#content'
    scripts/fetch-page.py --from urls.txt --selector '#content' --out-dir recon/raw --json
    scripts/fetch-page.py 'https://example.com/a' --rung native   # 只跑一级

边界:
    - 默认执行 robots.txt 检查；--i-have-permission 仅用于已获站点方明确许可的场景。
    - 不做验证码求解、不轮换出口、不伪造挑战 cookie。
    - 只测你有权测的目标。
"""

import argparse
import json
import sys
import time

from reconlib import arms as armreg
from reconlib import fetch
from reconlib.policy import ALGORITHMS


def resolve_arms(args, registry):
    if args.arms:
        return armreg.parse_list(args.arms, registry, args.impersonate)
    rung = args.rung or ("native" if args.no_browser_stack else "both")
    if rung == "native":
        return ["native"]
    if rung == "cffi":
        return [f"cffi:{args.impersonate}"]
    return ["native", f"cffi:{args.impersonate}"]


def probe(url, args, registry, arm_list):
    from reconlib.session import ArmSession, display_arm

    record = {"url": url, "robots": "skipped" if args.i_have_permission else None,
              "rungs": [], "verdict": None, "waf_class": None, "evidence": []}
    require = armreg.parse_require(args.require)
    save = (lambda u, a, body: fetch.save_html(args.out_dir, u, a, body)
            if args.out_dir else None)
    session = ArmSession(
        arm_list, registry=registry, policy=args.policy, delay=0.0,
        timeout=args.timeout, ua=args.ua, save=save, require=require,
        permission=args.i_have_permission)
    page = session.probe(
        url, selector=args.selector, min_content=args.min_content,
        max_attempts=len(arm_list), require=require,
        permission=args.i_have_permission,
        warn=lambda host: print(f"# 警告：{url} 的 robots.txt 取不到，合规状态未知",
                                file=sys.stderr))
    record["robots"] = page["robots"]

    for t in page["tries"]:
        skipped = t.get("skipped", False)
        rung = {
            "rung": display_arm(registry.get(t["arm"])),
            "status": "-" if skipped else t.get("status", "-"),
            "bytes": "-" if skipped else t.get("bytes", "-"),
            "content": "-" if skipped else t.get("content", "-"),
            "final": t.get("final", url), "saved": t.get("saved"),
            "outcome": t["outcome"],
            "attributable": t.get("attributable", False),
            "rate_limited": t.get("rate_limited", False),
            "elapsed_ms": t.get("elapsed_ms", 0),
        }
        if skipped:
            rung["skipped"] = True
            rung["error"] = t.get("reason")
        record["rungs"].append(rung)

    if page["skipped"] == "robots-deny":
        record["verdict"] = "合规禁止"
        record["evidence"] = ["robots.txt 不允许抓取"]
        return record, False

    if page["evidence"]:
        if page["evidence"]["outcome"] == "ok":
            record["verdict"] = "已拿到内容"
            if len(record["rungs"]) > 1:
                record["waf_class"] = "2 协议栈白名单（低阶被拦、高阶放行）"
            return record, True
        last = page.get("last") or {}
        record["verdict"] = "未拿到内容"
        no = last.get("waf_no") or 0
        record["waf_class"] = (f"{no} {last.get('waf_class')}"
                               if no else last.get("waf_class"))
        record["evidence"] = last.get("evidence") or []
        return record, False

    record["verdict"] = "未拿到内容"
    record["waf_class"] = "0 未归类"
    if page["tries"] and all(t.get("skipped") for t in page["tries"]):
        record["evidence"] = [
            "没有可用的客户端臂；请安装 curl_cffi 或配置外部内核（--arms-file）"]
    else:
        record["evidence"] = ["没有可用的客户端臂；请安装 curl_cffi 或改用真实浏览器内核"]
    return record, False


def print_table(records):
    print(f"{'url':<44} {'robots':<8} {'rung':<22} {'http':<5} {'bytes':<8} {'content':<10} verdict")
    for rec in records:
        if not rec["rungs"]:
            print(f"{rec['url'][:44]:<44} {str(rec['robots']):<8} {'-':<22} {'-':<5} {'-':<8} "
                  f"{'-':<10} {rec['verdict']}")
            print()
            continue
        for i, rung in enumerate(rec["rungs"]):
            if i == 0:
                url, rob = rec["url"][:44], str(rec["robots"])
            else:
                url, rob = "", ""
            status = rung.get("status", "-") if not rung.get("error") else "ERR"
            print(f"{url:<44} {rob:<8} {rung['rung']:<22} {str(status):<5} "
                  f"{str(rung.get('bytes', '-')):<8} {str(rung.get('content', '-')):<10} "
                  f"{'' if i < len(rec['rungs']) - 1 else rec['verdict']}")
        if rec.get("waf_class"):
            print(f"{'':<44} {'':<8} 归类: {rec['waf_class']}  证据: {'; '.join(rec['evidence'])}")
        print()


def main():
    ap = argparse.ArgumentParser(description="按客户端臂取内容，失败时给出 WAF 归类")
    ap.add_argument("urls", nargs="*", help="目标 URL")
    ap.add_argument("--from", dest="from_file", help="从文件读 URL，一行一个，# 为注释")
    ap.add_argument("--selector", default="", help="内容区选择器（#id / .class / 标签名）")
    ap.add_argument("--min-content", type=int, default=200, help="内容区字符下限，默认 200")
    ap.add_argument("--ua", default=fetch.UA_DEFAULT)
    ap.add_argument("--impersonate", default="chrome", help="curl_cffi 的仿真目标")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--rung", choices=("both", "native", "cffi"),
                       help="只跑某一级客户端臂，默认 both（native → cffi）")
    group.add_argument("--no-browser-stack", action="store_true",
                       help="等价于 --rung native（保留旧参数）")
    group.add_argument("--arms", help="逗号分隔的客户端臂（可用 browser:<engine>）")
    ap.add_argument("--arms-file",
                    help="外部臂配置 JSON（默认 ~/.config/site-recon/arms.json）")
    ap.add_argument("--require", default="", help="硬性能力要求，如 js（逗号分隔）")
    ap.add_argument("--policy", choices=ALGORITHMS, default="fixed",
                    help="选臂策略，默认 fixed（按给定顺序）")
    ap.add_argument("--timeout", type=int, default=20)
    ap.add_argument("--delay", type=float, default=5.0, help="URL 之间的间隔，默认 5s")
    ap.add_argument("--out-dir", help="保存每级响应体")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而不是表格")
    ap.add_argument("--i-have-permission", action="store_true",
                    help="跳过 robots 检查（仅在你已获站点方许可时使用）")
    args = ap.parse_args()

    urls = list(args.urls)
    if args.from_file:
        try:
            with open(args.from_file, encoding="utf-8") as f:
                for line in f:
                    line = line.split("#", 1)[0].strip()
                    if line:
                        urls.append(line)
        except OSError:
            sys.exit(f"找不到文件: {args.from_file}")
    if not urls:
        ap.print_help()
        return 2
    urls = [u if u.startswith(("http://", "https://")) else f"https://{u}" for u in urls]

    try:
        registry = armreg.load(args.arms_file)
        arm_list = resolve_arms(args, registry)
        armreg.parse_require(args.require)
    except ValueError as e:
        ap.error(str(e))

    records, any_ok = [], False
    for i, url in enumerate(urls):
        if i:
            time.sleep(args.delay)
        rec, ok = probe(url, args, registry, arm_list)
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


if __name__ == "__main__":
    sys.exit(main())
