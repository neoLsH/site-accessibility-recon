#!/usr/bin/env python3
"""self-check.py — skill 结构与内容的一致性校验，可用于本地或 CI。

检查项：
  1. 必备文件与目录
  2. SKILL.md frontmatter（分隔符、name 规范、description 单行且 <=1024）
  3. SKILL.md 行数 <= 150
  4. 未决占位符（TODO / TBD / FIXME）
  5. Markdown 相对链接可解析
  6. 脱敏红线（站点名 / 厂商名 / 非标准状态码 / Cookie 名，外加本地词表里的内网标识）
  7. 脚本语法（scripts/ 下全部 .sh / .js / .py）
  8. 工作区骨架（recon/）：内容默认被忽略、骨架可入库
  9. 内网词表：默认从仓库外读取（$RECON_DENY / ~/.config/site-recon/）；若出现在仓库内必须被忽略
 10. recon 入口可运行（--version 自检）

退出码：0 通过，1 有问题。
"""

import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = "site-accessibility-recon"

REQUIRED = [
    "SKILL.md",
    "AGENTS.md",
    "README.md",
    "references/triage-and-gates.md",
    "references/layers.md",
    "references/vectors.md",
    "references/evidence-matrix.md",
    "references/metrics.md",
    "references/countermeasure-ladder.md",
    "references/site-profile.md",
    "references/toolbox.md",
    "references/fingerprint-methods.md",
    "references/waf-classes.md",
    "references/detection-surface.md",
    "assets/site-profile.template.yaml",
    "assets/report-template.md",
    "assets/workspace/.gitignore",
    "assets/workspace/README.md",
    "assets/workspace/raw/.gitkeep",
    "scripts/preflight.sh",
    "scripts/quick-check.sh",
    "scripts/client-matrix.sh",
    "scripts/fingerprint-probe.js",
    "scripts/fp-echo.py",
    "scripts/fetch-page.py",
    "scripts/js-surface.py",
    "scripts/recon.py",
    "scripts/report.py",
    "scripts/self-check.py",
]

# 脱敏红线：命中即失败。这里只放公开可提的站点 / 厂商。
# 内网平台名、内网域名、内部产品代号一律不进本文件——否则规则本身就把内部标识写进了公开仓库；
# 它们放在本地词表 LOCAL_DENY 里（已被 .gitignore 忽略）。
FORBIDDEN = [
    (r"ctrip|csdn|zhihu|tripadvisor|cloudflare|datadome|"
     r"bilibili|xiaohongshu|kuaishou", "具体站点/厂商名"),
    (r"知乎|携程|小红书|快手|圆桌|专栏", "具体站点名"),
    (r"\b(412|432|521)\b", "会指认厂商的非标准状态码"),
    (r"(?i)\bcookie\s*[:=]\s*[a-z_]{3,}", "具体 Cookie 名"),
]

# 内网词表默认放仓库外：仓库里出现它本身就是风险（add -f、打包、整目录拷贝都会带走）。
LOCAL_DENY = "denylist.local"
DENY_HOME = os.path.expanduser("~/.config/site-recon/denylist.local")

failures = []
oks = []


def ok(msg):
    oks.append(msg)


def fail(msg):
    failures.append(msg)


def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return f.read()


def markdown_files():
    # recon/ 是运行时工作区，内容不入库、可能带真实站点标识，不参与校验；
    # .git 与依赖目录同理。
    skip = {"recon", ".git", "node_modules", "__pycache__"}
    out = []
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in skip]
        for f in files:
            if f.endswith(".md"):
                out.append(os.path.relpath(os.path.join(base, f), ROOT))
    return sorted(out)


def script_files():
    out = []
    for f in sorted(os.listdir(os.path.join(ROOT, "scripts"))):
        if f.endswith((".sh", ".js", ".py")):
            out.append(os.path.join("scripts", f))
    return out


def check_structure():
    for p in REQUIRED:
        if os.path.exists(os.path.join(ROOT, p)):
            ok(f"存在 {p}")
        else:
            fail(f"缺失 {p}")
    for d in ("scripts", "references", "references/cases", "assets", "assets/workspace"):
        if os.path.isdir(os.path.join(ROOT, d)):
            ok(f"目录 {d}/")
        else:
            fail(f"缺失目录 {d}/")


def check_workspace_ignore():
    """工作区骨架：壳必须能入库，内容必须默认被忽略。"""
    shell = ["assets/workspace/.gitignore", "assets/workspace/README.md", "assets/workspace/raw/.gitkeep"]
    content = ["assets/workspace/raw/p1-curl.html", "assets/workspace/forum/profile.yaml"]
    missing = [p for p in shell if not os.path.exists(os.path.join(ROOT, p))]
    if missing:
        fail(f"工作区骨架不全: {', '.join(missing)}")
        return

    if not (shutil.which("git") and os.path.isdir(os.path.join(ROOT, ".git"))):
        rules = read("assets/workspace/.gitignore")
        need = ["*", "!.gitignore", "!README.md", "!raw/", "!raw/.gitkeep"]
        lost = [r for r in need if r not in rules.split("\n")]
        if lost:
            fail(f"工作区 .gitignore 缺少放行规则: {', '.join(lost)}")
        else:
            ok("工作区 .gitignore 含全部放行规则（无 git，跳过实际判定）")
        return

    def ignored(path):
        r = subprocess.run(["git", "-C", ROOT, "check-ignore", "--no-index", "-q", path],
                           capture_output=True)
        return r.returncode == 0

    bad = [p for p in content if not ignored(p)] + [p for p in shell if ignored(p)]
    if bad:
        fail(f"工作区忽略规则不对（应忽略内容、保留骨架）: {', '.join(bad)}")
    else:
        ok("工作区：内容被忽略、骨架可入库")


def check_frontmatter():
    text = read("SKILL.md")
    if not text.startswith("---\n"):
        fail("SKILL.md 未以 '---' 开头")
        return
    end = text.find("\n---", 4)
    if end == -1:
        fail("SKILL.md frontmatter 未闭合")
        return
    fm = text[4:end]

    m = re.search(r"^name:\s*(\S+)\s*$", fm, re.M)
    if not m:
        fail("frontmatter 缺少 name")
    else:
        name = m.group(1)
        if not re.fullmatch(r"[a-z0-9-]+", name):
            fail(f"name 不是 hyphen-case: {name}")
        if name != NAME:
            fail(f"name({name}) 与目录名({NAME}) 不一致")
        else:
            ok(f"name={name}")

    m = re.search(r"^description:\s*(.+)$", fm, re.M)
    if not m:
        fail("frontmatter 缺少 description")
        return
    desc = m.group(1).strip()
    if desc in ("|", ">", "|-", ">-"):
        fail("description 使用了块标量，应为单行")
    elif len(desc) > 1024:
        fail(f"description 超长: {len(desc)} > 1024")
    elif "\n" in desc:
        fail("description 不是单行")
    else:
        ok(f"description 单行，{len(desc)} 字符")


def check_limits():
    lines = read("SKILL.md").count("\n") + 1
    if lines <= 150:
        ok(f"SKILL.md {lines} 行 (<=150)")
    else:
        fail(f"SKILL.md {lines} 行 (>150)，细节应下沉到 references/")

    hits_total = 0
    for p in markdown_files():
        body = read(p)
        hits = re.findall(r"\b(TODO|TBD|FIXME)\b", body)
        if hits:
            fail(f"{p} 含未决占位符: {', '.join(sorted(set(hits)))}")
            hits_total += 1
    if hits_total == 0:
        ok("未决占位符 0 命中")


def check_disclosure():
    """渐进披露：references/ 下的文档必须都能从 SKILL.md 到达。"""
    skill = read("SKILL.md")
    missing = [
        p
        for p in sorted(os.listdir(os.path.join(ROOT, "references")))
        if p.endswith(".md") and p not in skill
    ]
    if missing:
        fail(f"SKILL.md 未索引: {', '.join(missing)}")
    else:
        ok("references/ 全部可从 SKILL.md 到达")
    if "references/cases/" in skill:
        ok("cases/ 目录已索引")
    else:
        fail("SKILL.md 未指向 cases 目录")


def check_version():
    m = re.search(r"^\s+version:\s*(\S+)\s*$", read("SKILL.md"), re.M)
    if not m:
        ok("未声明 version，跳过一致性检查")
        return
    v = m.group(1)
    if f"v{v}" in read("README.md"):
        ok(f"版本一致 v{v}")
    else:
        fail(f"README 未标注版本 v{v}")


def check_links():
    pattern = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
    bad = 0
    for p in markdown_files():
        base = os.path.dirname(p)
        for target in pattern.findall(read(p)):
            t = target.split("#", 1)[0].strip()
            if not t or t.startswith(("http://", "https://", "mailto:", "/")):
                continue
            resolved = os.path.normpath(os.path.join(ROOT, base, t))
            if not os.path.exists(resolved):
                fail(f"{p}: 链接失效 -> {target}")
                bad += 1
    if bad == 0:
        ok("Markdown 相对链接全部可解析")


def deny_sources():
    """词表来源：$RECON_DENY > ~/.config/site-recon/ > 仓库内（最后手段，必须被忽略）。"""
    out = []
    env = os.environ.get("RECON_DENY")
    if env:
        out.append(env)
    if os.path.exists(DENY_HOME):
        out.append(DENY_HOME)
    local = os.path.join(ROOT, LOCAL_DENY)
    if os.path.exists(local):
        out.append(local)
    return out


def local_deny():
    """本地禁用词表：每行一个正则（大小写不敏感），# 开头为注释。"""
    rules = []
    for path in deny_sources():
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        rules.append((f"(?i){line}", f"内网标识（{os.path.basename(path)}）"))
        except OSError as e:
            fail(f"词表读取失败 {path}: {e}")
    return rules or None


def check_local_deny():
    if not deny_sources():
        ok(f"未提供内网词表（$RECON_DENY 或 {DENY_HOME}），跳过该项（CI 同样跳过）")
        return
    ok(f"内网词表已加载 {len(local_deny() or [])} 条")

    local = os.path.join(ROOT, LOCAL_DENY)
    if not os.path.exists(local):
        return
    if shutil.which("git") and os.path.isdir(os.path.join(ROOT, ".git")):
        r = subprocess.run(["git", "-C", ROOT, "check-ignore", "--no-index", "-q", LOCAL_DENY],
                           capture_output=True)
        if r.returncode != 0:
            fail(f"仓库里有 {LOCAL_DENY} 且未被忽略，会随提交出去；请移到 {DENY_HOME}")
            return
    ok(f"仓库内的 {LOCAL_DENY} 已被忽略（仍建议移到 {DENY_HOME}，免得打包/拷贝带走）")


def check_forbidden():
    bad = 0
    # 本文件的 FORBIDDEN 列表必然含这些词，跳过自身
    targets = [p for p in markdown_files() + script_files() + ["assets/site-profile.template.yaml"]
               if p != "scripts/self-check.py"]
    patterns = FORBIDDEN + (local_deny() or [])
    for p in targets:
        if not os.path.exists(os.path.join(ROOT, p)):
            continue
        body = read(p)
        for pattern, label in patterns:
            try:
                hits = list(re.finditer(pattern, body))
            except re.error as e:
                fail(f"{LOCAL_DENY} 里有非法正则 {pattern!r}: {e}")
                continue
            for m in hits:
                line = body[: m.start()].count("\n") + 1
                fail(f"{p}:{line} 命中{label}: {m.group(0)!r}")
                bad += 1
    if bad == 0:
        ok("脱敏红线 0 命中")


def check_scripts():
    for p in script_files():
        path = os.path.join(ROOT, p)
        if p.endswith(".sh"):
            if not shutil.which("bash"):
                continue
            r = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
            if r.returncode != 0:
                fail(f"{p} 语法错误: {r.stderr.strip()}")
                continue
            if not os.access(path, os.X_OK):
                fail(f"{p} 没有可执行权限（chmod +x）")
                continue
        elif p.endswith(".js"):
            if not shutil.which("node"):
                continue
            r = subprocess.run(["node", "--check", path], capture_output=True, text=True)
            if r.returncode != 0:
                fail(f"{p} 语法错误: {r.stderr.strip()}")
                continue
        else:
            if not shutil.which("python3"):
                continue
            r = subprocess.run([sys.executable, "-m", "py_compile", path],
                               capture_output=True, text=True)
            if r.returncode != 0:
                fail(f"{p} 语法错误: {r.stderr.strip()}")
                continue
        ok(f"{p} 语法通过")


def check_cli():
    """语法过了不代表能跑：入口至少得能自报版本。"""
    r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts/recon.py"), "--version"],
                       capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.startswith("recon "):
        ok(f"recon 入口可用（{r.stdout.strip()}）")
    else:
        fail(f"recon 入口异常: {(r.stderr or r.stdout).strip()[:200]}")


def main():
    check_structure()
    check_workspace_ignore()
    check_local_deny()
    check_frontmatter()
    check_limits()
    check_disclosure()
    check_version()
    check_links()
    check_forbidden()
    check_scripts()
    check_cli()

    print("== 通过 ==")
    for m in oks:
        print(f"  ok   {m}")
    if failures:
        print("\n== 失败 ==")
        for m in failures:
            print(f"  FAIL {m}")
        print(f"\n{len(failures)} 项失败")
        return 1
    print(f"\n全部通过（{len(oks)} 项）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
