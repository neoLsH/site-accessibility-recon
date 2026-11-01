#!/usr/bin/env python3
"""matrix — 同一 URL 跑多种客户端臂，输出状态码 / 字节数 / 内容区 / 耗时 / 最终 URL。

默认穷举指定臂（policy 只排顺序）；加 --stop-on-ok 才在第一个 ok 后提前停。
用来隔离 L2 传输层（TLS/H2 指纹）与 L3 身份层（UA）的影响：同 UA 不同栈翻转
看 L2，同栈不同 UA 翻转看 L3。

只测你有权测的目标。连续探测本身可能触发按 IP 的短窗限速，默认 5s 间隔；
已知目标有短窗限速时，先用 --arms 只打 2 发。--selector 只支持 #id / .class /
标签名及其简单组合；复杂选择器输出 ?unsupported，请改用真实浏览器取值。
"""

import argparse
import json
import sys
from urllib import request as urlrequest

from reconlib import arms as armreg
from reconlib import fetch, measure
from reconlib.session import ArmSession

DEFAULT_ARMS = "native,cffi:chrome"

CLIENTS = {
    "curl": ("native",),
    "cffi": ("cffi:chrome", "cffi:safari"),
    "node": (),
    "py": (),
}

READING = """\
读法：
  1) 同 UA、不同栈结果翻转      → 存在 L2 传输层闸门，按栈选客户端
  2) 同栈、不同 UA 结果翻转      → 存在 L3 身份规则
  3) 两类都不翻转但都失败        → 继续看 L4 运行时 / L1 链路 / 出口
  4) 2xx 但 content 为 0/极小     → 降级软拦截或抽取下游丢失，先按
     triage-and-gates.md「样本类型 1」判别序列排除 CSR / 抽取问题
  5) 4xx + 字节数极小            → 先看有无站点特征，无特征按链路失败复核
  6) 连打后整体变差             → 多半是频率降权，静置或换出口后重测
  7) 多 pass 出现通过↔拦截翻转   → 概率门，报区间与翻转率，别报单次点估计"""

CONTENT_LEGEND = """\
content 列的取值：
  N             命中容器，可见字符数约 N
  0             容器存在但无文字（典型空壳）
  missing       选择器没命中（页面结构里没有这个容器）
  ?unsupported  选择器过于复杂，换简单选择器或改用真实浏览器取值"""


def _rows_for(page, registry, url, selector):
    rows = []
    for t in page["tries"]:
        arm = registry.get(t["arm"])
        skipped = bool(t.get("skipped"))
        rows.append({
            "client": t["arm"],
            "identity": arm.engine or arm.profile or "-",
            "http": "SKIP" if skipped else t.get("status", "-"),
            "bytes": "-" if skipped else t.get("bytes", "-"),
            "content": "-" if skipped else t.get("content", "-"),
            "time_s": f"{(t.get('elapsed_ms') or 0) / 1000:.3f}",
            "final_url": t.get("final") or url,
            "_status": None if skipped else t.get("status"),
        })
    return rows


def _feasible(row, selector):
    try:
        status = int(row["_status"])
    except (TypeError, ValueError):
        return False
    if not 200 <= status < 300:
        return False
    if not selector:
        return True
    content = str(row["content"])
    return content.isdigit() and int(content) > 0


def _echo_tls():
    print()
    print("出口 TLS 指纹（第三方回显，按需使用）:")
    try:
        with urlrequest.urlopen("https://tls.peet.ws/api/all", timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        print("  (回显服务不可达)")
        return
    tls = data.get("tls") or {}
    for key, value in (("ip", data.get("ip")), ("ja3", tls.get("ja3_hash")),
                       ("ja4", tls.get("ja4"))):
        print(f"  {key}={value if value is not None else '-'}")


def _arm_list(args, ap):
    registry = armreg.load(args.arms_file)
    if args.clients:
        if args.arms:
            ap.error("--clients 与 --arms 只能给一个")
        names = []
        for raw in args.clients.split(","):
            key = raw.strip()
            if not key:
                continue
            if key not in CLIENTS:
                ap.error(f"--clients 取值无效: {key!r}（可选 curl,cffi；"
                         "node/py 改用 --arms 或外部内核）")
            mapped = CLIENTS[key]
            if not mapped:
                ap.error(f"--clients {key} 不在臂模型内：请用 --arms 指定等价臂，"
                         "或通过 --arms-file 配置外部内核")
            for name in mapped:
                if name not in names:
                    names.append(name)
        if not names:
            ap.error("--clients 至少需要一个取值")
        text = ",".join(names)
    else:
        text = args.arms or DEFAULT_ARMS
    try:
        return registry, armreg.parse_list(text, registry, args.impersonate)
    except ValueError as e:
        ap.error(str(e))


def build_parser():
    ap = argparse.ArgumentParser(
        description="客户端栈矩阵：同 URL 跑多种客户端臂，量内容区")
    ap.add_argument("url", nargs="?", help="目标 URL")
    ap.add_argument("--html", help="离线量已有 HTML 的内容区（需同时给 --selector）")
    ap.add_argument("--selector", default="",
                    help="量内容区（#id / .class / 标签名），默认不量")
    ap.add_argument("--min-content", type=int, default=200,
                    help="内容区字符下限，默认 200（用于判定 ok）")
    ap.add_argument("--passes", type=int, default=1,
                    help="重复跑几轮，默认 1；疑似概率门用 3")
    ap.add_argument("--clients", default="",
                    help="兼容旧参数：curl → native，cffi → cffi:chrome,safari")
    ap.add_argument("--arms", default="",
                    help=f"逗号分隔的客户端臂（默认 {DEFAULT_ARMS}）")
    ap.add_argument("--arms-file",
                    help="外部臂配置 JSON（默认 ~/.config/site-recon/arms.json）")
    ap.add_argument("--require", default="", help="硬性能力要求，如 js（逗号分隔）")
    ap.add_argument("--impersonate", default="chrome", help="裸 cffi 臂使用的仿真目标")
    ap.add_argument("--policy", default="fixed",
                    choices=("thompson", "ucb1", "fixed", "round_robin"),
                    help="排臂顺序，默认 fixed（按给定顺序）")
    ap.add_argument("--seed", type=int, help="thompson 的随机种子（复现用）")
    ap.add_argument("--stop-on-ok", action="store_true",
                    help="第一个 ok 后提前停（默认穷举全部指定臂）")
    ap.add_argument("--save-html", help="把每次尝试的响应体存到该目录")
    ap.add_argument("--delay", type=float, default=5,
                    help="同 host 请求间隔，默认 5s")
    ap.add_argument("--cost-weight", type=float, default=0.1,
                    help="成本惩罚权重（5000ms 归一化），默认 0.1；0 关闭")
    ap.add_argument("--timeout", type=int, default=20, help="单请求超时，默认 20s")
    ap.add_argument("--ua", default=fetch.UA_DEFAULT, help="统一 User-Agent")
    ap.add_argument("--tls-echo", action="store_true",
                    help="额外探测出口 TLS 指纹（发送到第三方回显服务）")
    ap.add_argument("--i-have-permission", action="store_true",
                    help="跳过 robots 检查（仅在你已获站点方许可时使用）")
    ap.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)

    if args.html:
        if not args.selector:
            ap.error("--html 需要同时给 --selector")
        try:
            with open(args.html, "rb") as f:
                body = f.read()
        except OSError:
            sys.exit(f"找不到文件: {args.html}")
        value = measure.content_len(body, args.selector)
        print(f"content\t{value}")
        return 0
    if not args.url:
        ap.print_help()
        return 2
    if args.delay < 0:
        ap.error("--delay 不能为负")
    if args.passes < 1:
        ap.error("--passes 必须 >= 1")
    if args.min_content < 0:
        ap.error("--min-content 不能为负")
    if args.cost_weight < 0:
        ap.error("--cost-weight 不能为负")

    registry, arm_list = _arm_list(args, ap)
    try:
        require = armreg.parse_require(args.require)
    except ValueError as e:
        ap.error(str(e))

    def warn(host):
        print(f"# 警告：{host} 的 robots.txt 取不到，合规状态未知，继续探测",
              file=sys.stderr)

    pass_no = {"value": 1}

    def save(url, arm, body):
        if not args.save_html:
            return None
        return fetch.save_html(args.save_html, url,
                               f"p{pass_no['value']}-{arm}", body)

    session = ArmSession(
        arm_list, registry=registry, policy=args.policy, seed=args.seed,
        delay=args.delay, timeout=args.timeout, ua=args.ua,
        permission=args.i_have_permission, require=require, save=save,
        cost_weight=args.cost_weight)

    rows = []
    halted = None
    for i in range(1, args.passes + 1):
        pass_no["value"] = i
        page = session.probe(
            args.url, selector=args.selector, min_content=args.min_content,
            max_attempts=len(arm_list), require=require,
            permission=args.i_have_permission, warn=warn,
            stop_on_ok=args.stop_on_ok)
        rows.extend(_rows_for(page, registry, args.url, args.selector))
        if page.get("halt"):
            halted = page["halt"]
            print(f"# 已停止：{halted}"
                  f"{(': ' + page['reason']) if page.get('reason') else ''}",
                  file=sys.stderr)
            break

    header = ["client", "identity", "http", "bytes"]
    if args.selector:
        header.append("content")
    header += ["time_s", "final_url"]
    print("\t".join(header))
    for row in rows:
        cells = [row["client"], row["identity"], str(row["http"]),
                 str(row["bytes"])]
        if args.selector:
            cells.append(str(row["content"]))
        cells += [row["time_s"], row["final_url"]]
        print("\t".join(cells))

    if args.selector:
        print()
        print(CONTENT_LEGEND)
    print()
    print(READING)
    if args.tls_echo:
        _echo_tls()

    if halted:
        return 1
    return 0 if any(_feasible(row, args.selector) for row in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
