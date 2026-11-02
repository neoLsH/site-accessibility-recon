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
 11. 案例结构：references/cases/ 每篇含现象 / 可迁移规律 / 置信度
 12. 版本记录：CHANGELOG.md 含当前版本条目
 13. 执行计划可运行，且档案模板 lint 无 ERROR
 14. 概率门脚本的节流护栏（pass 上限与默认间隔）
 15. 形态判据表：assets/shape-signals.json 可解析，且被 shape.py 默认加载
 16. 双指标结算：stats 对示例证据行可运行，口径符合定义
 17. 原子层契约：reconlib 可导入，度量与归类字段稳定，fetch-page 复用
 18. 行为测试：tests/ 有可编译的用例，且已接入 CI
 19. 批量采集护栏：crawl 的预算 / 间隔 / 熔断常量受限，试验行不能混入证据行
 20. 离线推演：bandit 不发请求、同 seed 可复现，示例试验行口径正确
 21. 臂注册表：外部臂按配置可用、未配置 browser 不可用、render 不经 shell
 22. 策略上下文：上下文键 / 回退链稳定，quick/matrix 薄壳转调 Python，bandit 支持分组推演
 23. 策略记忆：分层先验 / 遗忘 / 诊断 / 档案往返，四个采集入口与 bandit 全部接入
 24. 探索规划：explore 只读、预算封顶、同输入可复现，且 recon 入口已接入
 25. 安全探索：连续 blocked 暂停单臂、全暂停返回 halt、fixed 不受影响，CLI 已接入
 26. 会话状态：按臂/按 host 隔离、0600、关闭即清，采集入口默认开启而 canary 不持有
 27. 限频联合决策：连续限频进入 host 冷却、冷却期不发请求、限频样本不进选臂统计
 28. 加 --tests 时实跑 tests/ 全量（CI 另起一步；本地发布前建议也跑）

退出码：0 通过，1 有问题。
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = "site-accessibility-recon"

REQUIRED = [
    "SKILL.md",
    "AGENTS.md",
    "README.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
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
    "references/anti-patterns.md",
    "references/review-scorecard.md",
    "references/execution.md",
    "references/site-shapes.md",
    "references/cli.md",
    "references/walkthrough.md",
    "references/atoms.md",
    "references/arms.md",
    "references/strategy.md",
    "references/sessions.md",
    "references/crawl.md",
    "references/bandit.md",
    "references/agent.md",
    "references/forms.md",
    "assets/site-profile.template.yaml",
    "assets/report-template.md",
    "assets/shape-signals.json",
    "assets/example/profile.yaml",
    "assets/example/evidence.jsonl",
    "assets/example/evidence-before.jsonl",
    "assets/example/evidence-after.jsonl",
    "assets/example/trials.jsonl",
    "assets/example/site/index.html",
    "assets/example/site/article/index.html",
    "assets/example/site/article/1.html",
    "assets/example/site/article/2.html",
    "assets/workspace/.gitignore",
    "assets/workspace/README.md",
    "assets/workspace/raw/.gitkeep",
    "scripts/preflight.sh",
    "scripts/quick.py",
    "scripts/quick-check.sh",
    "scripts/matrix.py",
    "scripts/client-matrix.sh",
    "scripts/fingerprint-probe.js",
    "scripts/fp-echo.py",
    "scripts/fetch-page.py",
    "scripts/crawl.py",
    "scripts/bandit.py",
    "scripts/explore.py",
    "scripts/advise.py",
    "scripts/reconlib/__init__.py",
    "scripts/reconlib/measure.py",
    "scripts/reconlib/classify.py",
    "scripts/reconlib/robots.py",
    "scripts/reconlib/fetch.py",
    "scripts/reconlib/pacing.py",
    "scripts/reconlib/evidence.py",
    "scripts/reconlib/context.py",
    "scripts/reconlib/policy.py",
    "scripts/reconlib/policy_store.py",
    "scripts/reconlib/explore.py",
    "scripts/reconlib/simulate.py",
    "scripts/reconlib/advice.py",
    "scripts/reconlib/arms.py",
    "scripts/reconlib/render.py",
    "scripts/reconlib/session.py",
    "scripts/reconlib/cookies.py",
    "scripts/js-surface.py",
    "scripts/shape.py",
    "scripts/canary.py",
    "scripts/stats.py",
    "scripts/plan.py",
    "scripts/recon.py",
    "scripts/report.py",
    "scripts/self-check.py",
    "tests/support.py",
    "tests/test_atoms.py",
    "tests/test_fetch.py",
    "tests/test_pacing.py",
    "tests/test_evidence.py",
    "tests/test_policy.py",
    "tests/test_crawl.py",
    "tests/test_simulate.py",
    "tests/test_bandit.py",
    "tests/test_advice.py",
    "tests/test_advise.py",
    "tests/test_arms.py",
    "tests/test_render.py",
    "tests/test_session.py",
    "tests/test_context.py",
    "tests/test_policy_context.py",
    "tests/test_policy_prior.py",
    "tests/test_policy_store.py",
    "tests/test_policy_safety.py",
    "tests/test_cookies.py",
    "tests/test_rate_limit.py",
    "tests/test_quick.py",
    "tests/test_matrix.py",
    "tests/test_explore.py",
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
    skip = {"__pycache__"}
    out = []
    for base, dirs, files in os.walk(os.path.join(ROOT, "scripts")):
        dirs[:] = [d for d in dirs if d not in skip]
        for f in files:
            if f.endswith((".sh", ".js", ".py")):
                out.append(os.path.relpath(os.path.join(base, f), ROOT))
    return sorted(out)


def test_files():
    d = os.path.join(ROOT, "tests")
    if not os.path.isdir(d):
        return []
    return [os.path.join("tests", f) for f in sorted(os.listdir(d)) if f.endswith(".py")]


def text_files():
    """会随仓库出去、且可能夹带敏感标识的文件：Markdown、脚本、配置、fixture。"""
    skip = {"recon", ".git", "node_modules", "__pycache__"}
    exts = (".md", ".py", ".sh", ".js", ".json", ".jsonl", ".yaml", ".yml", ".html")
    out = []
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in skip]
        for f in files:
            if f.endswith(exts):
                out.append(os.path.relpath(os.path.join(base, f), ROOT))
    return sorted(out)


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


def check_cases():
    """案例投稿门禁：结构可机检的部分（章节齐全、编号规范、置信度已标）。"""
    case_dir = os.path.join(ROOT, "references", "cases")
    if not os.path.isdir(case_dir):
        return
    need = ["## 现象", "## 可迁移规律", "## 置信度"]
    levels = re.compile(r"observed|mechanism|efficacy|assumed|观察|机制|效果|高|中|低")
    bad = 0
    for name in sorted(os.listdir(case_dir)):
        if not name.endswith(".md"):
            continue
        p = f"references/cases/{name}"
        if not re.match(r"^\d{2}-[a-z0-9-]+\.md$", name):
            fail(f"{p}: 文件名应为 NN-slug.md")
            bad += 1
            continue
        body = read(p)
        absent = [h for h in need if h not in body]
        if absent:
            fail(f"{p}: 缺少章节 {' / '.join(h.lstrip('# ') for h in absent)}")
            bad += 1
            continue
        if len(re.findall(r"^## ", body, re.M)) < 5:
            fail(f"{p}: 中间分析段太少（## 级标题 < 5），六段结构不完整")
            bad += 1
            continue
        tail = body.split("## 置信度", 1)[1]
        if not levels.search(tail):
            fail(f"{p}: 置信度段没有标级别（observed / mechanism / efficacy / assumed）")
            bad += 1
    if bad == 0:
        ok("案例结构：章节齐全、编号规范、置信度已标")


def check_changelog():
    m = re.search(r"^\s+version:\s*(\S+)\s*$", read("SKILL.md"), re.M)
    if not m:
        return
    v = m.group(1)
    if re.search(rf"^##\s*\[{re.escape(v)}\]", read("CHANGELOG.md"), re.M):
        ok(f"CHANGELOG 含 v{v} 条目")
    else:
        fail(f"CHANGELOG.md 没有 v{v} 的条目")


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
    targets = [p for p in text_files() if p != "scripts/self-check.py"]
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
    for p in script_files() + test_files():
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


def check_plan_smoke():
    """档案模板是 lint 的基线：它可以有提示，但不能有 ERROR；走查用的示例档案同理。"""
    for path, label in (("assets/site-profile.template.yaml", "模板"),
                        ("assets/example/profile.yaml", "示例档案")):
        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts/recon.py"), "plan",
             os.path.join(ROOT, path), "--json"],
            capture_output=True, text=True)
        if r.returncode != 0:
            fail(f"plan 入口异常（{label}）: {(r.stderr or r.stdout).strip()[:200]}")
            continue
        try:
            body = json.loads(r.stdout)
        except ValueError:
            fail(f"plan --json 不是合法 JSON（{label}）")
            continue
        lint = body.get("lint") or {}
        errors = lint.get("errors") or []
        if errors:
            fail(f"plan 对{label}报告 {len(errors)} 个 ERROR: {errors[0].get('path')}")
        else:
            ok(f"plan 可运行，{label} lint 无 ERROR（{len(lint.get('warnings') or [])} 条提示）")


def check_canary_guard():
    """节流是红线：pass 上限与默认间隔必须写在脚本里，不靠口头约定。"""
    body = read("scripts/canary.py")
    cap = re.search(r"MAX_PASSES\s*=\s*(\d+)", body)
    delay = re.search(r"^DELAY\s*=\s*(\d+(?:\.\d+)?)", body, re.M)
    if not cap or int(cap.group(1)) > 10:
        fail("canary 未限制最大 pass 数（应 <=10）")
    elif not delay or float(delay.group(1)) < 2:
        fail("canary 默认间隔过小（应 >=2s）")
    else:
        ok(f"canary 节流护栏：passes<={cap.group(1)}，delay>={delay.group(1)}s")


def check_shape_signals():
    """判据外置后，判据表本身也要被校验：能解析、能编译、且确实被默认加载。"""
    try:
        cfg = json.loads(read("assets/shape-signals.json"))
    except (OSError, ValueError) as e:
        fail(f"assets/shape-signals.json 不可用: {e}")
        return
    if not isinstance(cfg, dict):
        fail("shape-signals.json 顶层应为对象")
        return
    keys = ("challenge", "login_path", "login_phrase", "mounts", "csr_globals")
    bad = []
    for k in keys:
        vals = cfg.get(k)
        if not isinstance(vals, list) or not vals:
            bad.append(f"{k} 应为非空数组")
            continue
        for v in vals:
            if not isinstance(v, str) or not v.strip():
                bad.append(f"{k} 含空项或非字符串")
                break
            try:
                re.compile(v)
            except re.error as e:
                bad.append(f"{k} 正则非法 {v!r}: {e}")
                break
    if bad:
        fail("shape-signals.json 结构异常: " + "; ".join(bad))
        return
    if "shape-signals.json" not in read("scripts/shape.py"):
        fail("shape.py 未默认加载 assets/shape-signals.json")
        return
    ok(f"形态判据表可用（{sum(len(cfg[k]) for k in keys)} 条规则），shape.py 默认加载")


def check_stats_smoke():
    """指标口径最容易手算错：用固定示例行锁住定义。"""
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts/recon.py"), "stats",
         os.path.join(ROOT, "assets/example/evidence.jsonl"), "--json"],
        capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"stats 入口异常: {(r.stderr or r.stdout).strip()[:200]}")
        return
    try:
        body = json.loads(r.stdout)
    except ValueError:
        fail("stats --json 不是合法 JSON")
        return
    overall = body.get("overall") or {}
    access = overall.get("access") or {}
    complete = overall.get("complete") or {}
    got = (overall.get("n"), access.get("num"), access.get("den"),
           complete.get("num"), complete.get("den"))
    want = (8, 6, 7, 4, 6)
    if got != want:
        fail(f"stats 示例口径异常: (n, access_num, access_den, complete_num, complete_den)={got}，应为 {want}")
        return

    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts/recon.py"), "stats",
         os.path.join(ROOT, "assets/example/evidence-before.jsonl"),
         "--compare", os.path.join(ROOT, "assets/example/evidence-after.jsonl"),
         "--json"],
        capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"stats --compare 异常 rc={r.returncode}: {(r.stderr or r.stdout).strip()[:200]}")
        return
    try:
        cmp_body = json.loads(r.stdout)
    except ValueError:
        fail("stats --compare --json 不是合法 JSON")
        return
    if cmp_body.get("mode") != "compare":
        fail(f"stats --compare 未进入对比模式: mode={cmp_body.get('mode')!r}")
        return
    cmp = cmp_body.get("compare") or {}
    overall = cmp.get("overall") or {}
    access_status = (overall.get("access") or {}).get("status")
    complete_status = (overall.get("complete") or {}).get("status")
    routes = {g.get("route"): g for g in cmp.get("routes") or []}
    small = (routes.get("/article/4") or {}).get("access", {}).get("status")
    if (access_status, complete_status, small) != ("inconclusive", "improved", "insufficient") \
            or cmp.get("regressed") is not False:
        fail(f"stats --compare 判定异常: overall.access={access_status!r}, "
             f"overall.complete={complete_status!r}, /article/4.access={small!r}, "
             f"regressed={cmp.get('regressed')!r}")
        return
    ok("stats 可运行，示例口径 6/7 可访问、4/6 完整（噪声单列）")
    ok("stats --compare 可运行，整体仅完整率改善、小样本判不足、示例无回归")


def check_atoms_contract():
    """原子层是 crawl / bandit 的公共底座：导入、度量与归类契约必须稳定。"""
    code = """
import sys
sys.path.insert(0, "scripts")
import stats
import reconlib.arms, reconlib.render, reconlib.session
from reconlib import measure
from reconlib.classify import classify

body = b'<div id="content">abc</div><script>var x=1</script>'
assert measure.content_len(body, "#content") == 3, "内容区度量偏移"
assert measure.is_html("text/html") and not measure.is_html("application/json")

obs = {"status": 200, "ctype": "text/html", "headers": "", "body": body, "error": None}
ok = classify(obs, "#content", 1)
keys = {"outcome", "waf_no", "waf_class", "evidence", "attributable", "rate_limited"}
assert set(ok) == keys, sorted(ok)
assert ok["outcome"] == "ok" and ok["attributable"]

limited = classify({"status": 429, "ctype": "text/html", "headers": "Retry-After: 1",
                    "body": b"", "error": None}, "", 200)
assert limited["outcome"] == "blocked" and limited["rate_limited"]
assert not limited["attributable"]
assert ok["outcome"] in stats.VALID and limited["outcome"] in stats.VALID
"""
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, cwd=ROOT)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()[-3:]
        fail("原子层契约异常: " + " | ".join(tail))
        return
    if "from reconlib" not in read("scripts/fetch-page.py"):
        fail("fetch-page.py 未复用 reconlib 原子")
        return
    ok("原子层契约稳定（度量 / 归类 / outcome 词表），fetch-page 已复用")


def check_arms_guard():
    """臂注册表与外部内核是新的请求入口：未配置不能冒充可用，外部命令不能过 shell。"""
    render = read("scripts/reconlib/render.py")
    session = read("scripts/reconlib/session.py")
    if "shell=True" in render:
        fail("render 用了 shell=True（外部命令必须按 argv 传递）")
        return
    if "robots.decision" not in session or "fetch_observation" not in session:
        fail("session 未先做 robots 再取样")
        return
    for p in ("scripts/crawl.py", "scripts/fetch-page.py"):
        if "ArmSession" not in read(p):
            fail(f"{p} 未接入 ArmSession")
            return
    if "choose_arm" not in read("scripts/canary.py"):
        fail("canary 未固定单臂")
        return
    code = """
import os, sys, tempfile
sys.path.insert(0, "scripts")
from reconlib import arms
fd, path = tempfile.mkstemp(suffix=".json")
os.close(fd)
open(path, "w").write('{"arms": []}')
r = arms.load(path)
assert r.get("native").available()
assert not r.get("browser:chromium").available()
try:
    r.get("browser")
except ValueError:
    pass
else:
    raise AssertionError("裸 browser 未被拒绝")
os.unlink(path)
"""
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, cwd=ROOT)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()[-3:]
        fail("臂注册表契约异常: " + " | ".join(tail))
        return
    ok("臂注册表：外部臂按配置可用、未配置 browser 不可用、render 不经 shell")


def check_strategy_context():
    """上下文与成本是选臂口径：键 / 回退链必须稳定，入口薄壳不能有第二份实现。"""
    if "quick.py" not in read("scripts/quick-check.sh"):
        fail("quick-check.sh 未转调 quick.py")
        return
    if "matrix.py" not in read("scripts/client-matrix.sh"):
        fail("client-matrix.sh 未转调 matrix.py")
        return
    code = """
import sys
sys.path.insert(0, "scripts")
from reconlib import context, evidence, policy
assert context.route_class("https://h/article/1") == "article"
assert context.route_class("https://h/api/list") == "api"
assert context.route_class("https://h/list/2") == "list"
assert context.response_class({"outcome": "degraded"}) == "shell"
assert context.fallbacks("h|article|challenge") == [
    "h|article|challenge", "h|article|*", "h|*|*", "*"]
b = policy.ContextualBandit(["native", "cffi:chrome"], seed=1, cost_weight=0)
b.update("h", "article|challenge", "native", "blocked", cost_ms=10)
b.update("h", "article|challenge", "cffi:chrome", "ok", cost_ms=10)
assert b.snapshot("h", "article|challenge")["best_arm"] == "cffi:chrome"
row = evidence.trial_row("https://h/article/1", "native",
                         {"outcome": "ok", "attributable": True},
                         context="h|article|content", cost_ms=42)
assert row["context"] == "h|article|content" and row["cost_ms"] == 42
"""
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, cwd=ROOT)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()[-3:]
        fail("上下文 / 成本契约异常: " + " | ".join(tail))
        return

    payload = {"contexts": [
        {"context": "h|article|challenge", "pages": 20,
         "rates": {"native": 0.3, "cffi:chrome": 0.8}},
        {"context": "h|list|content", "pages": 20,
         "rates": {"native": 0.9, "cffi:chrome": 0.6}},
    ]}
    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                      encoding="utf-8")
    try:
        json.dump(payload, tmp)
        tmp.close()
        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts/bandit.py"),
             "--rates-json", tmp.name, "--pages", "20", "--seed", "7", "--json"],
            capture_output=True, text=True)
        try:
            body = json.loads(r.stdout) if r.returncode == 0 else {}
        except ValueError:
            body = {}
        if body.get("source") != "rates-json" or len(body.get("contexts") or {}) != 2:
            fail("bandit --rates-json 未按上下文分组推演")
            return
    finally:
        os.unlink(tmp.name)
    ok("策略上下文：键与回退链稳定、quick/matrix 薄壳转调 Python、bandit 支持分组推演")


def check_policy_memory():
    """跨轮复用是策略层的能力边界：先验 / 遗忘 / 诊断 / 档案往返必须同时成立。"""
    code = """
import os, sys, tempfile
sys.path.insert(0, "scripts")
from reconlib import policy, policy_store

a = policy.ContextualBandit(["native", "cffi:chrome"], seed=3)
a.update("h", "article|challenge", "native", "blocked")
a.update("h", "article|challenge", "cffi:chrome", "ok")
assert a.snapshot("h", "article|challenge")["best_arm"] == "cffi:chrome"

cold = policy.ContextualBandit(["native", "cffi:chrome"], seed=3)
row = cold.snapshot("h", "article|content")["arms"][0]
assert row["pulls"] == 0 and row["n_eff"] > 0, "邻近上下文先验未生效"

f = policy.ContextualBandit(["native"], half_life=10, seed=1)
f.update("h", "article|content", "native", "blocked")
for _ in range(10):
    f.update("h", "article|content", "native", "ok")
assert f.snapshot("h", "article|content")["arms"][0]["mean"] > 0.6, "遗忘未生效"

snap = a.snapshot("h", "article|challenge")
assert 0.0 <= snap["confidence"] <= 1.0
assert snap["decision"] in ("exploit", "explore")
assert {row["arm"] for row in snap["arms"]} == {"native", "cffi:chrome"}
assert all(0.0 <= row["p_best"] <= 1.0 for row in snap["arms"])

fd, path = tempfile.mkstemp(suffix=".json")
os.close(fd)
try:
    policy_store.dump(a, path, meta={"command": "self-check"})
    c = policy.ContextualBandit(["native", "cffi:chrome"], seed=3)
    info = policy_store.restore(c, path)
    assert info["contexts"] == 1, info
    assert c.snapshot("h", "article|challenge") == snap, "档案往返后后验不一致"
    body = policy_store.load(path)
    assert body["schema"] == policy_store.SCHEMA
    assert policy_store.context_rates(body)
finally:
    os.unlink(path)
"""
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, cwd=ROOT)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()[-3:]
        fail("策略记忆契约异常: " + " | ".join(tail))
        return

    flags = ("--prior-strength", "--half-life", "--policy-in", "--policy-out")
    for p in ("scripts/crawl.py", "scripts/fetch-page.py",
              "scripts/quick.py", "scripts/matrix.py"):
        lost = [f for f in flags if f not in read(p)]
        if lost:
            fail(f"{p} 缺少策略参数: {', '.join(lost)}")
            return
    if "--policy-in" not in read("scripts/bandit.py"):
        fail("bandit 未支持 --policy-in 推演")
        return
    ok("策略记忆：先验 / 遗忘 / 诊断 / 档案往返成立，四入口与 bandit 已接入")


def check_safe_exploration():
    """安全探索是策略层的护栏：连续 blocked 只暂停该臂，不扩大探测面。"""
    code = """
import sys
sys.path.insert(0, "scripts")
from reconlib import explore, policy

b = policy.ContextualBandit(["native", "cffi:chrome"], algorithm="ucb1",
                            min_pulls=1, prior_strength=0.0, block_budget=2)
for _ in range(2):
    b.update("h", "article|challenge", "native", "blocked")
snap = b.snapshot("h", "article|challenge")
row = next(r for r in snap["arms"] if r["arm"] == "native")
assert row["suspended"] and row["block_streak"] == 2, row
assert b.select("h", "article|challenge") == "cffi:chrome"
assert b.snapshot("h", "list|content")["suspended_arms"] == [], "暂停不应跨路由"

for _ in range(2):
    b.update("h", "article|challenge", "cffi:chrome", "blocked")
halt = b.snapshot("h", "article|challenge")
assert halt["decision"] == "halt" and halt["best_arm"] is None, halt
assert b.select("h", "article|challenge") is None
assert halt["suspended_arms"] == ["native", "cffi:chrome"], halt["suspended_arms"]

fixed = policy.ContextualBandit(["native", "cffi:chrome"], algorithm="fixed",
                                block_budget=2)
for _ in range(2):
    fixed.update("h", "article|challenge", "native", "blocked")
assert fixed.select("h", "article|challenge") == "native"
assert fixed.snapshot("h", "article|challenge")["suspended_arms"] == []

stat = b.export_state()["h|article|challenge"]["arms"]["native"]
assert len(stat) == policy.STAT_LEN, "暂停状态不应写进策略档案"

rows = [
    {"kind": "trial", "arm": "native", "trial_outcome": "blocked",
     "rate_limited": False, "context": "h|article|challenge"},
    {"kind": "trial", "arm": "native", "trial_outcome": "blocked",
     "rate_limited": False, "context": "h|article|challenge"},
    {"kind": "trial", "arm": "cffi:chrome", "trial_outcome": "ok",
     "rate_limited": False, "context": "h|article|challenge"},
]
bandit, pages, observed = explore.replay(rows, block_budget=2)
body = explore.plan(bandit, pages, observed, budget=3)
assert "native" not in {s["arm"] for s in body["steps"]}, body["steps"]
assert body["block_budget"] == 2, body["block_budget"]
"""
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, cwd=ROOT)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()[-3:]
        fail("安全探索契约异常: " + " | ".join(tail))
        return

    for p in ("scripts/crawl.py", "scripts/fetch-page.py", "scripts/quick.py",
              "scripts/matrix.py", "scripts/explore.py"):
        if "--block-budget" not in read(p):
            fail(f"{p} 缺少 --block-budget")
            return
    session = read("scripts/reconlib/session.py")
    for needle in ("block_budget=3", "all-arms-suspended"):
        if needle not in session:
            fail(f"session 缺少安全探索接入: {needle}")
            return
    ok("安全探索：连续 blocked 暂停单臂、全暂停返回 halt、fixed 不受影响，CLI 已接入")


def check_cookie_sessions():
    """会话状态只在临时目录：按 (臂, host) 隔离、0600、关闭即清，且不写进证据。"""
    code = """
import os
import stat
import sys
sys.path.insert(0, "scripts")
from reconlib import cookies

store = cookies.CookieStore()
try:
    a = store.curl_jar("native", "a.example")
    b = store.curl_jar("cffi:chrome", "a.example")
    c = store.curl_jar("native", "b.example")
    assert len({a, b, c}) == 3, "会话未按 (臂, host) 隔离"
    assert stat.S_IMODE(os.stat(a).st_mode) == 0o600, "会话文件权限不是 0600"
    assert os.path.commonpath([a, b, c, store.root]) == store.root, "会话文件应在临时目录"
    assert store.curl_jar("native", "a.example") == a, "同键应复用同一会话文件"
    root = store.root
finally:
    store.close()
assert not os.path.exists(root), "close 后临时目录仍在"

off = cookies.CookieStore(enabled=False)
assert off.curl_jar("native", "a.example") is None
assert off.cffi_session("cffi:chrome", "a.example") is None
assert off.root is None
off.close()
"""
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, cwd=ROOT)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()[-3:]
        fail("会话隔离契约异常: " + " | ".join(tail))
        return

    for p in ("scripts/crawl.py", "scripts/fetch-page.py", "scripts/quick.py"):
        if "--no-cookies" not in read(p):
            fail(f"{p} 缺少 --no-cookies")
            return
    if '"--cookies"' not in read("scripts/matrix.py"):
        fail("matrix 缺少显式 opt-in 的 --cookies")
        return
    if "cookies=" in read("scripts/canary.py"):
        fail("canary 应保持无会话（基线要可复现）")
        return

    session = read("scripts/reconlib/session.py")
    for needle in ("CookieStore(enabled=cookies)", "cookies=self.cookies",
                   "def close"):
        if needle not in session:
            fail(f"session 缺少会话接入: {needle}")
            return
    fetch_src = read("scripts/reconlib/fetch.py")
    for needle in ('"-b", cookie_file', "session=session"):
        if needle not in fetch_src:
            fail(f"fetch 缺少会话接入: {needle}")
            return
    ok("会话状态：按臂/按 host 隔离、0600、关闭即清；采集默认开启，matrix 显式 opt-in，canary 无会话")


def check_rate_limit_guard():
    """限频是 host 级联合决策：连续超预算就冷却整台 host，且不进选臂胜负。"""
    code = """
import sys
sys.path.insert(0, "scripts")
from reconlib import pacing, policy

class Clock:
    t = 0.0
    def now(self):
        return self.t
    def sleep(self, seconds):
        self.t += seconds

c = Clock()
p = pacing.Pacing(delay=0, rate_limit_budget=2, rate_limit_cooldown=30,
                  clock=c.now, sleep=c.sleep)
assert p.cooldown_left("h") == 0
p.observe("h", "blocked", rate_limited=True, retry_after=5)
assert p.cooldown_left("h") == 0, "未达预算不应冷却"
state = p.observe("h", "blocked", rate_limited=True, retry_after=5)
assert p.cooldown_left("h") == 30, p.cooldown_left("h")
assert state["rate_limit_streak"] == 2 and state["rate_limit_total"] == 2, state
c.t += 31
assert p.cooldown_left("h") == 0, "冷却应按时钟到期"

longer = pacing.Pacing(delay=0, rate_limit_budget=1, rate_limit_cooldown=10,
                       clock=c.now, sleep=c.sleep)
assert longer.observe("h", "blocked", rate_limited=True,
                      retry_after=45)["cooldown"] == 45

off = pacing.Pacing(delay=0, rate_limit_budget=0, rate_limit_cooldown=30,
                    max_consecutive_blocked=99, clock=c.now, sleep=c.sleep)
for _ in range(5):
    state = off.observe("h2", "blocked", rate_limited=True)
assert state["rate_limit_total"] == 5 and off.cooldown_left("h2") == 0

b = policy.ContextualBandit(["native"], algorithm="thompson", seed=1)
assert b.update_from_outcome("h", "article|challenge", "native", "blocked",
                             rate_limited=True) is False
assert b.snapshot("h")["arms"][0]["pulls"] == 0, "限频样本不应更新后验"
"""
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, cwd=ROOT)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()[-3:]
        fail("限频冷却契约异常: " + " | ".join(tail))
        return

    for p in ("scripts/crawl.py", "scripts/fetch-page.py", "scripts/quick.py",
              "scripts/matrix.py"):
        body = read(p)
        for flag in ("--rate-limit-budget", "--rate-limit-cooldown"):
            if flag not in body:
                fail(f"{p} 缺少 {flag}")
                return
    crawl = read("scripts/crawl.py")
    for needle in ("cooldown_pages", "rate_limited_pages"):
        if needle not in crawl:
            fail(f"crawl 汇总缺少 {needle}")
            return

    session = read("scripts/reconlib/session.py")
    if "rate-limit-cooldown" not in session or "cooldown_left" not in session:
        fail("session 未接入限频冷却")
        return
    cool_at = session.find("self.pace.cooldown_left(host)")
    robots_at = session.find("if not permission:")
    if cool_at < 0 or robots_at < 0 or cool_at > robots_at:
        fail("session 应在取 robots.txt 之前检查冷却（冷却期不发任何请求）")
        return
    pacing_src = read("scripts/reconlib/pacing.py")
    for needle in ("DEFAULT_RATE_LIMIT_BUDGET", "DEFAULT_RATE_LIMIT_COOLDOWN",
                   "def cooldown_left"):
        if needle not in pacing_src:
            fail(f"pacing 缺少限频接入: {needle}")
            return
    ok("限频联合决策：超预算冷却 host、冷却期零请求、限频不进选臂统计，四入口已接入")


def check_explore_planner():
    """探索规划是只读层：不发请求、预算封顶、同输入可复现。"""
    code = """
import sys
sys.path.insert(0, "scripts")
from reconlib import explore

rows = [
    {"kind": "trial", "arm": "native", "trial_outcome": "blocked",
     "rate_limited": False, "context": "h|article|challenge",
     "url": "https://h/a/1"},
    {"kind": "trial", "arm": "cffi:chrome", "trial_outcome": "ok",
     "rate_limited": False, "context": "h|article|challenge",
     "url": "https://h/a/1"},
    {"kind": "trial", "arm": "native", "trial_outcome": "noise",
     "rate_limited": False, "context": "h|article|challenge"},
]
bandit, pages, observed = explore.replay(rows, arms=["browser:chromium"])
assert observed["attempts"] == 2 and observed["skipped"] == 1, observed
body = explore.plan(bandit, pages, observed, budget=3, max_per_context=2)
assert body["schema"] == "recon-explore/1"
assert body["allocated"] <= 3
assert body["allocated"] == sum(s["requests"] for s in body["steps"])
assert "browser:chromium" in body["unseen_arms"], body["unseen_arms"]
assert body["stop"]
again = explore.plan(bandit, pages, observed, budget=3, max_per_context=2)
assert body == again, "同输入不可复现"
"""
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, cwd=ROOT)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()[-3:]
        fail("探索规划契约异常: " + " | ".join(tail))
        return

    cli = read("scripts/explore.py")
    if any(m in cli for m in ("fetch_one", "ArmSession", "urlopen", "robots.decision")):
        fail("explore 不应联网（出现了取内容 / 网络调用）")
        return
    if "explore" not in read("scripts/recon.py"):
        fail("recon 入口未接入 explore")
        return

    args = [sys.executable, os.path.join(ROOT, "scripts/recon.py"), "explore",
            "--trials", os.path.join(ROOT, "assets/example/trials.jsonl"),
            "--arms", "native,cffi:chrome,cffi:firefox", "--budget", "8",
            "--json"]
    first = subprocess.run(args, capture_output=True, text=True)
    second = subprocess.run(args, capture_output=True, text=True)
    if first.returncode != 0 or second.returncode != 0:
        fail(f"explore 示例异常: {(first.stderr or first.stdout).strip()[:200]}")
        return
    if first.stdout != second.stdout:
        fail("explore 同输入输出不一致")
        return
    try:
        got = json.loads(first.stdout)
    except ValueError:
        fail("explore --json 不是合法 JSON")
        return
    if got.get("schema") != "recon-explore/1" or got.get("ok") is not True:
        fail(f"explore 契约异常: schema={got.get('schema')!r} ok={got.get('ok')!r}")
        return
    if got.get("allocated", 99) > got.get("budget", 0):
        fail("explore 分配超过预算")
        return
    if sum(s.get("requests", 0) for s in got.get("steps") or []) != got.get("allocated"):
        fail("explore steps 与 allocated 不一致")
        return
    if "cffi:firefox" not in (got.get("unseen_arms") or []):
        fail("explore 未识别未试臂")
        return
    ok("探索规划：只读、预算封顶、未试臂可识别、同输入可复现")


def check_crawl_guard():
    """批量采集是唯一会连发请求的入口：预算 / 间隔 / 熔断必须写在代码里，不靠口头约定。"""
    body = read("scripts/crawl.py")
    caps = {
        "MAX_PAGES": (re.search(r"^MAX_PAGES\s*=\s*(\d+)", body, re.M), 50),
        "MAX_REQUESTS": (re.search(r"^MAX_REQUESTS\s*=\s*(\d+)", body, re.M), 150),
        "MAX_CONSECUTIVE_BLOCKED": (
            re.search(r"^MAX_CONSECUTIVE_BLOCKED\s*=\s*(\d+)", body, re.M), 3),
    }
    bad = [f"{k} 未收紧（上限 {cap}）" for k, (m, cap) in caps.items()
           if not m or int(m.group(1)) > cap]
    delay = re.search(r"^DEFAULT_DELAY\s*=\s*(\d+(?:\.\d+)?)", body, re.M)
    if not delay or float(delay.group(1)) < 5:
        bad.append("DEFAULT_DELAY 过小（应 >=5s）")
    lost = [m for m in ("session", "evidence", "policy") if m not in body]
    if lost:
        bad.append(f"crawl 未复用原子 / 编排层: {', '.join(lost)}")

    tmp = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    try:
        tmp.write(json.dumps({"kind": "trial", "url": "https://example.invalid/a",
                              "arm": "native", "attempt": 1,
                              "trial_outcome": "blocked"}) + "\n")
        tmp.close()
        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts/recon.py"), "stats", tmp.name],
            capture_output=True, text=True)
        if r.returncode == 0 or "试验行" not in (r.stderr + r.stdout):
            bad.append("stats 未拒绝试验行（试验日志会污染双指标）")
    finally:
        os.unlink(tmp.name)

    if bad:
        fail("crawl 护栏异常: " + "; ".join(bad))
    else:
        ok("crawl 护栏：pages<=50、requests<=150、delay>=5s、熔断<=3；试验行被 stats 拒绝")


def check_bandit_smoke():
    """推演必须离线、可复现，样本筛选口径要与 crawl 一致。"""
    code = """
import sys
sys.path.insert(0, "scripts")
from reconlib import simulate

rates = simulate.parse_rates("native=0.3,cffi:chrome=0.8")
a = simulate.simulate("thompson", rates, pages=40, seed=7)
b = simulate.simulate("thompson", rates, pages=40, seed=7)
assert a == b, "同 seed 不可复现"
assert a["requests"] <= 40 * 2, "请求数超出臂数上限"

got = simulate.rates_from_trials([
    {"kind": "trial", "arm": "native", "trial_outcome": "ok"},
    {"kind": "trial", "arm": "native", "trial_outcome": "noise"},
    {"kind": "trial", "arm": "native", "trial_outcome": "blocked", "rate_limited": True},
])
assert got == {"native": 1.0}, got
"""
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, cwd=ROOT)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()[-3:]
        fail("推演原子异常: " + " | ".join(tail))
        return
    body = read("scripts/bandit.py")
    if "fetch_one" in body or "robots" in body:
        fail("bandit 不应发请求（出现了 fetch_one / robots）")
        return
    if "simulate" not in body or "policy" not in body:
        fail("bandit 未复用 simulate / policy 原子")
        return

    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts/recon.py"), "bandit",
         "--trials", os.path.join(ROOT, "assets/example/trials.jsonl"),
         "--pages", "50", "--json"],
        capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"bandit 示例异常: {(r.stderr or r.stdout).strip()[:200]}")
        return
    try:
        got_body = json.loads(r.stdout)
    except ValueError:
        fail("bandit --json 不是合法 JSON")
        return
    rates = got_body.get("rates") or {}
    if (round(rates.get("native", -1), 3), round(rates.get("cffi:chrome", -1), 3)) != (0.4, 0.75):
        fail(f"bandit 示例胜率口径异常: {rates}")
        return
    ok("推演可复现且离线；示例试验行估出 native=0.40、cffi=0.75")


def check_advice_smoke():
    """建议层是 Agent / LLM 的交接点：只读、可校验、必须带停止条件。"""
    code = """
import sys
sys.path.insert(0, "scripts")
from reconlib import advice

body = advice.recommend([
    {"route": "/a", "outcome": "noise"},
    {"route": "/a", "outcome": "ok"},
    {"route": "/b", "outcome": "auth"},
])
assert body["next"]["action"] == "check-authorization", body["next"]
assert body["evidence"]["noise_excluded_n"] == 2, body["evidence"]
assert body["next"]["stop"], "建议必须带停止条件"
assert advice.recommend([])["next"]["action"] == "collect-more"

try:
    advice.recommend([{"kind": "trial", "trial_outcome": "ok"}])
except ValueError as e:
    assert "试验行" in str(e)
else:
    raise AssertionError("试验行未被拒绝")
"""
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, cwd=ROOT)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()[-3:]
        fail("建议原子异常: " + " | ".join(tail))
        return
    body = read("scripts/advise.py")
    if any(m in body for m in ("fetch_one", "robots", "subprocess", "write_jsonl")):
        fail("advise 不应联网 / 执行命令 / 写证据行")
        return
    if "advice" not in body or "evidence" not in body:
        fail("advise 未复用 advice / evidence 原子")
        return

    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts/recon.py"), "advise",
         os.path.join(ROOT, "assets/example/evidence.jsonl"), "--json"],
        capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"advise 示例异常: {(r.stderr or r.stdout).strip()[:200]}")
        return
    try:
        got = json.loads(r.stdout)
    except ValueError:
        fail("advise --json 不是合法 JSON")
        return
    ev = got.get("evidence") or {}
    if got.get("schema") != "advise/1" or got.get("ok") is not True:
        fail(f"advise 契约异常: schema={got.get('schema')!r} ok={got.get('ok')!r}")
        return
    if (ev.get("n"), ev.get("noise_excluded_n")) != (8, 7):
        fail(f"advise 计数口径异常: n={ev.get('n')} 可归因={ev.get('noise_excluded_n')}")
        return
    if (got.get("next") or {}).get("action") != "collect-more":
        fail(f"示例应判补样本，实际 {got.get('next')}")
        return

    tmp = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    try:
        tmp.write(json.dumps({"kind": "trial", "url": "https://example.invalid/a",
                              "arm": "native", "trial_outcome": "ok"}) + "\n")
        tmp.close()
        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts/recon.py"), "advise", tmp.name],
            capture_output=True, text=True)
        if r.returncode == 0 or "试验行" not in (r.stderr + r.stdout):
            fail("advise 未拒绝试验行")
            return
    finally:
        os.unlink(tmp.name)

    ok("建议层只读：示例判 collect-more、噪声剔出分母、试验行被拒、每条建议带停止条件")


def check_tests():
    mods = [os.path.basename(p) for p in test_files() if os.path.basename(p).startswith("test_")]
    if not mods:
        fail("tests/ 下没有 test_*.py")
        return
    if "unittest" not in read(".github/workflows/check.yml"):
        fail(".github/workflows/check.yml 未跑行为测试（缺 unittest 步骤）")
    else:
        ok(f"行为测试 {len(mods)} 个模块，已接入 CI")


def run_tests():
    try:
        r = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", os.path.join(ROOT, "tests")],
            capture_output=True, text=True, timeout=300, cwd=ROOT)
    except subprocess.TimeoutExpired:
        fail("行为测试超时（>300s）")
        return
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()[-12:]
        fail("行为测试失败:\n" + "\n".join(tail))
        return
    summary = [ln for ln in (r.stderr or "").splitlines() if ln.startswith("Ran ")]
    ok(f"行为测试通过（{summary[0] if summary else 'rc=0'}）")


def main():
    check_structure()
    check_workspace_ignore()
    check_local_deny()
    check_frontmatter()
    check_limits()
    check_disclosure()
    check_version()
    check_cases()
    check_changelog()
    check_links()
    check_forbidden()
    check_scripts()
    check_cli()
    check_plan_smoke()
    check_canary_guard()
    check_shape_signals()
    check_stats_smoke()
    check_atoms_contract()
    check_arms_guard()
    check_strategy_context()
    check_policy_memory()
    check_safe_exploration()
    check_cookie_sessions()
    check_rate_limit_guard()
    check_explore_planner()
    check_crawl_guard()
    check_bandit_smoke()
    check_advice_smoke()
    check_tests()
    if "--tests" in sys.argv[1:]:
        run_tests()

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
