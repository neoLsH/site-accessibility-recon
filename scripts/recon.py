#!/usr/bin/env python3
"""recon — 一个入口跑完可访问性排查的全部动作。

把 scripts/ 下的脚本收敛成子命令，输出契约统一：
默认人读（原样透传），加 --json 输出**一个** JSON 对象（含 command 字段），供 CI 与其他 Agent 消费。
退出码与底层脚本一致，--json 不改变退出码。

用法:
    recon doctor                                    # 环境自检
    recon quick https://a https://b --selector '#content'
    recon matrix <url> --selector '#content' --passes 3
    recon fetch <url> --selector '#content'
    recon fp collect --via cffi --out chrome.json
    recon fp diff baseline.json chrome.json
    recon report recon/forum/profile.yaml --out recon/forum/report.md
    recon check

    recon quick --from urls.txt --json              # 任一子命令都可加 --json

子命令与脚本的对应：
    doctor → preflight.sh    quick → quick-check.sh    matrix → client-matrix.sh
    fetch  → fetch-page.py   fp    → fp-echo.py        report → report.py
    check  → self-check.py
"""

import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

TOOLS = {
    "doctor": ("bash", "preflight.sh", "环境自检：缺什么、缺了会降级成什么"),
    "quick": ("bash", "quick-check.sh", "可行性快筛：每 URL 1 请求 + robots，先分掉死路"),
    "matrix": ("bash", "client-matrix.sh", "客户端栈矩阵：同 URL 跑多种客户端，量内容区"),
    "fetch": ("python", "fetch-page.py", "客户端阶梯取内容，失败时给 WAF 机制归类"),
    "fp": ("python", "fp-echo.py", "网络栈指纹：采集快照 / 逐字段比对"),
    "report": ("python", "report.py", "从站点档案生成报告"),
    "check": ("python", "self-check.py", "仓库自检：结构、脱敏、脚本语法"),
}


def skill_version():
    try:
        with open(os.path.join(ROOT, "SKILL.md"), encoding="utf-8") as f:
            m = re.search(r"^\s+version:\s*(\S+)\s*$", f.read(), re.M)
        return m.group(1) if m else "unknown"
    except OSError:
        return "unknown"


def usage():
    lines = [
        f"recon {skill_version()} — 站点可访问性排查",
        "",
        "用法: recon <命令> [参数...] [--json]",
        "",
        "命令:",
    ]
    for name, (_, _, desc) in TOOLS.items():
        lines.append(f"  {name:<8} {desc}")
    lines += [
        "",
        "  fp 另有两个动作：fp collect [选项] / fp diff A.json B.json",
        "",
        "参数直接透传给底层脚本：recon quick --help 查看该命令的全部选项。",
        "--json  输出单个 JSON 对象（含 command 字段），退出码不变。",
        "",
        f"脚本目录: {HERE}",
    ]
    print("\n".join(lines))


def run(interp, script, args):
    path = os.path.join(HERE, script)
    if not os.path.exists(path):
        return 127, "", f"找不到 {script}"
    cmd = [sys.executable if interp == "python" else "bash", path] + args
    try:
        p = subprocess.run(cmd, capture_output=True, text=True)
    except OSError as e:
        return 127, "", str(e)
    return p.returncode, p.stdout, p.stderr


def table_rows(text, stop_at_blank=True):
    """解析这些脚本共用的 TAB 表：首行表头，字段两侧有空格填充。"""
    lines = []
    for line in text.splitlines():
        if not line.strip():
            if stop_at_blank:
                break
            continue
        lines.append(line)
    if not lines:
        return []
    header = [c.strip() for c in lines[0].split("\t")]
    if len(header) < 2:
        return []
    rows = []
    for line in lines[1:]:
        cells = [c.strip() for c in line.split("\t")]
        if len(cells) != len(header):
            continue
        rows.append(dict(zip(header, cells)))
    return rows


def to_json(command, rc, out, err):
    """把各脚本的输出收敛成一个 JSON 对象。"""
    body = {"command": command, "ok": rc == 0}
    if command in ("quick", "matrix"):
        body["rows"] = table_rows(out)
    elif command == "doctor":
        body["tools"] = [r for r in table_rows(out, stop_at_blank=False) if r.get("tool") != "tool"]
    elif command == "fetch":
        try:
            body["records"] = json.loads(out)
        except ValueError:
            body["records"] = []
            body["ok"] = False
    elif command == "fp":
        try:
            body["diff"] = json.loads(out)
        except ValueError:
            try:
                body["snapshot"] = json.loads(out)
            except ValueError:
                body["ok"] = False
    elif command == "report":
        body["report"] = out if out.strip() and not out.startswith("已生成") else None
        body["out"] = out.strip()[4:] if out.startswith("已生成") else None
    elif command == "check":
        body["failures"] = [l.split("FAIL", 1)[1].strip()
                            for l in out.splitlines() if "FAIL" in l]
    if err.strip():
        body["stderr"] = [l for l in err.splitlines() if l.strip()]
    return body


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        usage()
        return 0
    if argv[0] in ("-V", "--version", "version"):
        print(f"recon {skill_version()}")
        return 0

    cmd, rest = argv[0], argv[1:]
    if cmd not in TOOLS:
        print(f"未知命令: {cmd}", file=sys.stderr)
        usage()
        return 2

    as_json = "--json" in rest
    rest = [a for a in rest if a != "--json"]

    # fp 有两个动作；diff 走 fp-echo 的 --diff
    if cmd == "fp":
        action = rest[0] if rest and not rest[0].startswith("-") else "collect"
        if action in ("collect", "diff"):
            rest = rest[1:]
        if action == "diff":
            rest = ["--diff"] + rest
            if as_json:
                rest.append("--json")
    elif cmd == "fetch" and as_json:
        rest.append("--json")

    # report 的档案既是位置参数也是 --profile；本 CLI 允许 recon report <档案>
    if cmd == "report" and rest and not rest[0].startswith("-") and "--profile" not in rest:
        rest = ["--profile", rest[0]] + rest[1:]

    interp, script, _ = TOOLS[cmd]
    rc, out, err = run(interp, script, rest)

    if as_json:
        print(json.dumps(to_json(cmd, rc, out, err), ensure_ascii=False, indent=2))
    else:
        sys.stdout.write(out)
        sys.stderr.write(err)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
