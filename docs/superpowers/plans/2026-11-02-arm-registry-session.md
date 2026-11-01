# 策略臂注册表、外部内核与上下文选举 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把两个硬编码客户端臂扩成可注册的内核集合，让所有发请求的能力点共用 `ArmSession`，再把选举升级为上下文与成本感知的 MAB，两版分别发布 v1.14.0 / v1.15.0。

**Architecture:** 新增 `reconlib/arms.py`（注册表）、`reconlib/render.py`（cmd/http 外部内核适配）、`reconlib/session.py`（robots → pacing → 选臂 → 取样 → 归类 → 更新 → 行）。v1.14.0 先接 `fetch` / `crawl` / `canary`，行为默认可退回现版；v1.15.0 加 `context.py`、`ContextualBandit`、`quick.py`、`matrix.py`，并让 `bandit` / `advise` 读上下文。

**Tech Stack:** Python 3 stdlib（`subprocess`、`urllib`、`json`、`unittest`）、现有 `reconlib` 原子、bash 薄壳、GitHub Actions。

---

## 文件结构

| 文件 | 责任 |
|---|---|
| `scripts/reconlib/arms.py` | 臂描述、内置表、外部配置加载、能力过滤 |
| `scripts/reconlib/render.py` | cmd / http 两种外部内核传输 → observation |
| `scripts/reconlib/session.py` | 唯一网络编排层；不写文件、不打印，行交给调用方 |
| `scripts/reconlib/context.py` | `route_class` / `response_class` / `key` / `fallbacks` |
| `scripts/reconlib/policy.py` | 保留 `Bandit`；新增 `ContextualBandit` |
| `scripts/crawl.py` | 改为 session 编排，护栏常量与输出字段不变 |
| `scripts/fetch-page.py` | 固定臂序的阶梯，输出 `records[].rungs` 不变 |
| `scripts/canary.py` | 先选一条臂，整个 pass 序列固定；记录 `arm` |
| `scripts/quick.py` | 单请求快筛 + 跨 URL 选举；输出列与词表不变 |
| `scripts/matrix.py` | 默认穷举臂；`--stop-on-ok` 才提前停 |
| `scripts/quick-check.sh` / `client-matrix.sh` | 参数与退出码透传的薄壳 |
| `scripts/bandit.py` / `reconlib/simulate.py` | `--rates-json` 上下文推演 |
| `scripts/reconlib/advice.py` | 证据快照加 `contexts` |
| `references/arms.md` | 注册表 schema、传输、能力、配置位置、安全边界 |
| `references/strategy.md` | 上下文、奖励、成本、回退链、各能力点语义 |

---

## Task 1: 臂注册表

**Files:**
- Create: `scripts/reconlib/arms.py`
- Test: `tests/test_arms.py`

- [ ] **Step 1: 写失败测试**

```python
import json
import os
import tempfile
import unittest

import support
from reconlib import arms


def write_cfg(text):
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


class RegistryTest(unittest.TestCase):
    def test_builtin_native_and_unconfigured_browser(self):
        reg = arms.load(write_cfg('{"arms": []}'))
        self.assertTrue(reg.get("native").available())
        browser = reg.get("browser:chromium")
        self.assertFalse(browser.available())
        self.assertIn("arms.json", browser.unavailable_reason())

    def test_external_override_makes_browser_available(self):
        cfg = write_cfg(json.dumps({"arms": [{
            "name": "browser:chromium", "transport": "cmd",
            "command": ["render", "{url}"], "capabilities": ["tls", "js"]}]}))
        reg = arms.load(cfg)
        arm = reg.get("browser:chromium")
        self.assertTrue(arm.available())
        self.assertEqual(arm.command, ["render", "{url}"])
        self.assertEqual(set(arm.capabilities), {"tls", "js"})

    def test_http_endpoint_with_userinfo_is_rejected(self):
        cfg = write_cfg(json.dumps({"arms": [{
            "name": "browser:webkit", "transport": "http",
            "endpoint": "http://user:pass@127.0.0.1:9/render",
            "capabilities": ["js"]}]}))
        with self.assertRaises(ValueError):
            arms.load(cfg)

    def test_cmd_requires_url_placeholder(self):
        cfg = write_cfg(json.dumps({"arms": [{
            "name": "browser:firefox", "transport": "cmd",
            "command": ["render"], "capabilities": ["js"]}]}))
        with self.assertRaises(ValueError):
            arms.load(cfg)

    def test_duplicate_external_name_is_rejected(self):
        cfg = write_cfg(json.dumps({"arms": [
            {"name": "browser:chromium", "transport": "cmd",
             "command": ["r", "{url}"], "capabilities": ["js"]},
            {"name": "browser:chromium", "transport": "cmd",
             "command": ["r", "{url}"], "capabilities": ["js"]}]}))
        with self.assertRaises(ValueError):
            arms.load(cfg)

    def test_bare_cffi_expands_to_chrome(self):
        reg = arms.load(write_cfg('{"arms": []}'))
        self.assertEqual(reg.get("cffi").name, "cffi:chrome")

    def test_parse_list_rejects_bare_browser_and_unknown(self):
        reg = arms.load(write_cfg('{"arms": []}'))
        with self.assertRaises(ValueError):
            arms.parse_list("browser", reg)
        with self.assertRaises(ValueError):
            arms.parse_list("magic", reg)
        self.assertEqual(arms.parse_list("native,cffi", reg),
                         ["native", "cffi:chrome"])

    def test_missing_explicit_config_is_error(self):
        with self.assertRaises(ValueError):
            arms.load("/no/such/arms.json")

    def test_require_filters_capabilities(self):
        reg = arms.load(write_cfg('{"arms": []}'))
        ok, skipped = reg.filter(["native", "browser:chromium"], require=["js"])
        self.assertEqual(ok, [])
        self.assertEqual(len(skipped), 2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试，确认失败**

```bash
cd site-accessibility-recon
python3 -m unittest tests.test_arms -v
```
Expected: FAIL（`ModuleNotFoundError: reconlib.arms`）。

- [ ] **Step 3: 实现 `scripts/reconlib/arms.py`**

```python
"""arms — 客户端臂注册表：名称 → 传输 / 能力 / 外部目标。

臂是“客户端协议栈”的描述，不是绕过手段。内置只声明两类可执行臂：
    native        curl 原生 TLS/H2
    cffi:<profile> curl_cffi 仿真浏览器协议栈（需安装 curl_cffi）

browser:<engine> 只是外部内核的适配器名称；没有仓库外配置时可见但不可用，
选中时记为 skipped，不发请求、不更新后验。仓库不内置任何浏览器内核。
"""

import importlib.util
import json
import os
import re
from urllib.parse import urlsplit

CAPABILITIES = ("tls", "h2", "js", "cookies")
TRANSPORTS = ("native", "cffi", "cmd", "http")
DEFAULT_CONFIG = os.path.expanduser("~/.config/site-recon/arms.json")
NAME_RE = re.compile(r"^[a-z][a-z0-9:_-]*$")


def _has_cffi():
    try:
        return importlib.util.find_spec("curl_cffi") is not None
    except (ImportError, ValueError):
        return False


class Arm:
    __slots__ = ("name", "transport", "engine", "profile", "capabilities",
                 "command", "endpoint")

    def __init__(self, name, transport, engine="", profile="", capabilities=(),
                 command=None, endpoint=None):
        self.name = name
        self.transport = transport
        self.engine = engine
        self.profile = profile
        self.capabilities = tuple(capabilities)
        self.command = list(command) if command else None
        self.endpoint = endpoint

    def available(self):
        return not self.unavailable_reason()

    def unavailable_reason(self):
        if self.transport == "native":
            return ""
        if self.transport == "cffi":
            return "" if _has_cffi() else "未安装 curl_cffi"
        if self.transport == "cmd":
            if self.command:
                return ""
            return "未配置外部内核：请在 --arms-file / ~/.config/site-recon/arms.json 提供 command"
        if self.transport == "http":
            if self.endpoint:
                return ""
            return "未配置外部内核：请在 --arms-file / ~/.config/site-recon/arms.json 提供 endpoint"
        return f"未知传输: {self.transport}"

    def as_dict(self):
        return {
            "name": self.name,
            "transport": self.transport,
            "engine": self.engine,
            "profile": self.profile,
            "capabilities": list(self.capabilities),
            "available": self.available(),
            "reason": self.unavailable_reason(),
        }


def _builtin():
    out = [
        Arm("native", "native", capabilities=("tls", "h2")),
        Arm("cffi:chrome", "cffi", profile="chrome",
            capabilities=("tls", "h2", "cookies")),
    ]
    for engine in ("chromium", "webkit", "firefox"):
        out.append(Arm(f"browser:{engine}", "cmd", engine=engine,
                       capabilities=("tls", "h2", "js", "cookies")))
    return out


class Registry:
    def __init__(self, entries=None):
        self._arms = {}
        for arm in _builtin():
            self._arms[arm.name] = arm
        for arm in entries or []:
            self.add(arm)

    def add(self, arm):
        if not NAME_RE.fullmatch(arm.name or ""):
            raise ValueError(f"非法臂名称: {arm.name!r}")
        if arm.transport not in TRANSPORTS:
            raise ValueError(f"臂 {arm.name}: 未知传输 {arm.transport!r}")
        caps = set(arm.capabilities)
        if not caps or not caps <= set(CAPABILITIES):
            raise ValueError(f"臂 {arm.name}: capabilities 必须是 "
                             f"{'/'.join(CAPABILITIES)} 的非空子集")
        if arm.transport == "cmd":
            if not arm.command or not all(isinstance(x, str) and x for x in arm.command):
                raise ValueError(f"臂 {arm.name}: command 必须是非空字符串数组")
            if not any("{url}" in x for x in arm.command):
                raise ValueError(f"臂 {arm.name}: command 至少有一个 {url} 占位符")
        if arm.transport == "http":
            parts = urlsplit(arm.endpoint or "")
            if parts.scheme not in ("http", "https") or not parts.netloc:
                raise ValueError(f"臂 {arm.name}: endpoint 必须是 http(s) 地址")
            if parts.username or parts.password:
                raise ValueError(f"臂 {arm.name}: endpoint 不能带 userinfo")
        self._arms[arm.name] = arm

    def get(self, name):
        name = normalize(name)
        if name.startswith("cffi:"):
            if not name[5:]:
                raise ValueError("cffi 臂缺少 profile")
            return self._arms.get(name) or Arm(
                name, "cffi", profile=name[5:],
                capabilities=("tls", "h2", "cookies"))
        if name not in self._arms:
            raise ValueError(f"未知客户端臂: {name!r}")
        return self._arms[name]

    def names(self):
        return sorted(self._arms)

    def filter(self, names, require=()):
        ok, skipped, seen = [], [], set()
        for name in names:
            arm = self.get(name)
            if arm.name in seen:
                continue
            seen.add(arm.name)
            reason = arm.unavailable_reason()
            missing = sorted(set(require) - set(arm.capabilities))
            if reason:
                skipped.append((arm.name, reason))
            elif missing:
                skipped.append((arm.name, f"缺少能力: {','.join(missing)}"))
            else:
                ok.append(arm.name)
        return ok, skipped


def normalize(name):
    name = (name or "").strip()
    if name == "cffi":
        return "cffi:chrome"
    if name == "browser":
        raise ValueError("browser 臂要写成 browser:<engine>（如 browser:chromium）")
    return name


def parse_list(text, registry=None, impersonate="chrome"):
    reg = registry or load()
    out = []
    for raw in (text or "").split(","):
        raw = raw.strip()
        if not raw:
            continue
        name = f"cffi:{impersonate}" if raw == "cffi" else raw
        arm = reg.get(name)
        if arm.name not in out:
            out.append(arm.name)
    if not out:
        raise ValueError("至少需要一个客户端臂")
    return out


def parse_require(text):
    out = []
    for raw in (text or "").split(","):
        cap = raw.strip()
        if not cap:
            continue
        if cap not in CAPABILITIES:
            raise ValueError(f"未知能力: {cap!r}（可选 {'/'.join(CAPABILITIES)}）")
        if cap not in out:
            out.append(cap)
    return tuple(out)


def _entry(entry):
    name = entry.get("name")
    transport = entry.get("transport")
    if transport == "cffi":
        profile = entry.get("profile") or (name.split(":", 1)[1] if ":" in name else "chrome")
        engine = ""
    else:
        profile = ""
        engine = entry.get("engine") or (name.split(":", 1)[1] if ":" in name else "")
    return Arm(name, transport, engine=engine, profile=profile,
               capabilities=tuple(entry.get("capabilities") or ()),
               command=entry.get("command"), endpoint=entry.get("endpoint"))


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError as e:
        raise ValueError(f"读不到臂配置 {path}: {e}")
    except ValueError as e:
        raise ValueError(f"臂配置不是合法 JSON {path}: {e}")
    if not isinstance(data, dict) or not isinstance(data.get("arms"), list):
        raise ValueError(f"臂配置格式应为 {{\"arms\": [...]}}: {path}")
    seen = set()
    out = []
    for raw in data["arms"]:
        if not isinstance(raw, dict):
            raise ValueError(f"臂配置项必须是对象: {raw!r}")
        arm = _entry(raw)
        if arm.name in seen:
            raise ValueError(f"臂配置重复名称: {arm.name}")
        seen.add(arm.name)
        out.append(arm)
    return out


def load(path=None, env=None):
    env = os.environ if env is None else env
    source = path or env.get("RECON_ARMS_FILE")
    if source:
        if not os.path.exists(source):
            raise ValueError(f"找不到臂配置文件: {source}")
        return Registry(_read(source))
    if os.path.exists(DEFAULT_CONFIG):
        return Registry(_read(DEFAULT_CONFIG))
    return Registry()
```

- [ ] **Step 4: 跑测试，确认通过**

```bash
python3 -m unittest tests.test_arms -v
```
Expected: 9 tests PASS。

- [ ] **Step 5: 提交**

```bash
git add scripts/reconlib/arms.py tests/test_arms.py
git -c user.name="neoLsH" -c user.email="neoLsH@users.noreply.github.com" \
  commit -m "arms: 客户端臂注册表与外部配置"
```

---

## Task 2: 外部内核适配器

**Files:**
- Create: `scripts/reconlib/render.py`
- Test: `tests/test_render.py`

- [ ] **Step 1: 写失败测试**

```python
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import support
from reconlib import render
from reconlib.arms import Arm


def script(body):
    fd, path = tempfile.mkstemp(suffix=".py")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return path


def cmd_arm(path):
    return Arm("browser:chromium", "cmd", engine="chromium",
               capabilities=("tls", "js"),
               command=[sys.executable, path, "{url}", "{engine}", "{ua}", "{timeout}"])


class CmdTransportTest(unittest.TestCase):
    def test_success(self):
        path = script(
            "import json,sys\n"
            "print(json.dumps({'status': 200, 'body': '<h1>ok</h1>', "
            "'final_url': sys.argv[1], 'ctype': 'text/html'}))\n")
        obs = render.fetch("http://127.0.0.1:9/x", cmd_arm(path), timeout=5)
        self.assertEqual(obs["status"], 200)
        self.assertEqual(obs["body"], b"<h1>ok</h1>")
        self.assertEqual(obs["arm"], "browser:chromium")
        self.assertIsNone(obs["error"])

    def test_nonzero_exit_is_error(self):
        path = script("import sys\nsys.stderr.write('boom')\nsys.exit(3)\n")
        obs = render.fetch("http://127.0.0.1:9/x", cmd_arm(path), timeout=5)
        self.assertEqual(obs["status"], 0)
        self.assertIn("rc=3", obs["error"])

    def test_bad_json_is_error(self):
        path = script("print('not json')\n")
        obs = render.fetch("http://127.0.0.1:9/x", cmd_arm(path), timeout=5)
        self.assertIn("JSON", obs["error"])

    def test_timeout_is_error(self):
        path = script("import time\ntime.sleep(5)\n")
        obs = render.fetch("http://127.0.0.1:9/x", cmd_arm(path), timeout=1)
        self.assertIn("超时", obs["error"])

    def test_body_cap(self):
        arm = cmd_arm(script("pass\n"))
        obs = render._payload_observation(
            arm, {"status": 200, "body": "x" * (render.MAX_BODY + 1)}, 1)
        self.assertEqual(len(obs["body"]), render.MAX_BODY)
        self.assertTrue(obs["truncated"])

    def test_unconfigured_arm_is_skipped(self):
        arm = Arm("browser:webkit", "cmd", engine="webkit",
                  capabilities=("js",))
        obs = render.fetch("http://127.0.0.1:9/x", arm, timeout=5)
        self.assertTrue(obs["skipped"])
        self.assertIn("未配置", obs["error"])


class HttpTransportTest(unittest.TestCase):
    def serve(self, status, payload):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                json.loads(self.rfile.read(n))
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return f"http://127.0.0.1:{httpd.server_port}/render"

    def test_success(self):
        endpoint = self.serve(200, {"status": 200, "body": "ok"})
        arm = Arm("browser:webkit", "http", engine="webkit",
                  capabilities=("js",), endpoint=endpoint)
        obs = render.fetch("https://example.test/a", arm, timeout=5)
        self.assertEqual(obs["status"], 200)
        self.assertEqual(obs["body"], b"ok")

    def test_5xx_is_error(self):
        endpoint = self.serve(500, {"error": "boom"})
        arm = Arm("browser:webkit", "http", engine="webkit",
                  capabilities=("js",), endpoint=endpoint)
        obs = render.fetch("https://example.test/a", arm, timeout=5)
        self.assertIn("HTTP 500", obs["error"])


class ShellSafetyTest(unittest.TestCase):
    def test_render_does_not_use_shell(self):
        path = os.path.join(support.SCRIPTS, "reconlib", "render.py")
        with open(path, encoding="utf-8") as f:
            self.assertNotIn("shell=True", f.read())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试，确认失败**

```bash
python3 -m unittest tests.test_render -v
```
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 实现 `scripts/reconlib/render.py`**

```python
"""render — 外部内核适配：cmd / http → observation。

仓库不内置浏览器。外部渲染器只负责“把这页渲染出来”，不接收 Cookie、凭证、
代理或出口参数；robots 与 pacing 由 session 在调用前完成。

cmd 契约：argv 列表，不经 shell；stdout 是 JSON 对象，至少含 status 与 body
（二进制用 body_b64）。http 契约：POST JSON {url, engine, ua, timeout}，
响应同一 JSON 契约。任何协议错误都转成带 error 的 observation → classify 归 noise。
"""

import base64
import json
import subprocess
import time
from urllib import error as urlerror
from urllib import request

from . import UA_DEFAULT
from .fetch import _observation

MAX_BODY = 16 * 1024 * 1024
MAX_ENVELOPE = MAX_BODY * 2 + 1024 * 1024


def _elapsed(started):
    return int((time.monotonic() - started) * 1000)


def _text(value):
    if value is None:
        return ""
    if isinstance(value, dict):
        return "\n".join(f"{k}: {v}" for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return "\n".join(str(x) for x in value)
    return str(value)


def _payload_observation(arm, payload, elapsed_ms):
    name = getattr(arm, "name", str(arm))
    if not isinstance(payload, dict):
        return _observation(name, error="外部内核返回的不是 JSON 对象",
                            elapsed_ms=elapsed_ms)
    if payload.get("error"):
        return _observation(name, error=f"外部内核: {payload['error']}",
                            elapsed_ms=elapsed_ms)
    truncated = False
    if payload.get("body_b64"):
        try:
            body = base64.b64decode(payload["body_b64"], validate=True)
        except Exception as e:
            return _observation(name, error=f"body_b64 解码失败: {e}",
                                elapsed_ms=elapsed_ms)
    else:
        raw = payload.get("body")
        if raw is None:
            body = b""
        elif isinstance(raw, str):
            body = raw.encode("utf-8")
        elif isinstance(raw, (bytes, bytearray)):
            body = bytes(raw)
        else:
            return _observation(name, error="body 既不是字符串也不是 body_b64",
                                elapsed_ms=elapsed_ms)
    if len(body) > MAX_BODY:
        body = body[:MAX_BODY]
        truncated = True
    try:
        status = int(payload.get("status") or 0)
    except (TypeError, ValueError):
        status = 0
    obs = _observation(
        name, status=status, bytes=len(body),
        final_url=str(payload.get("final_url") or ""),
        ctype=str(payload.get("ctype") or ""),
        headers=_text(payload.get("headers")), body=body,
        elapsed_ms=int(payload.get("elapsed_ms") or elapsed_ms))
    if truncated:
        obs["truncated"] = True
    return obs


def _expand(part, url, arm, timeout, ua):
    return (part.replace("{url}", url)
                .replace("{engine}", arm.engine or "")
                .replace("{ua}", ua)
                .replace("{timeout}", str(timeout)))


def _run_cmd(url, arm, timeout, ua, started):
    argv = [_expand(part, url, arm, timeout, ua) for part in arm.command]
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return _observation(arm.name, error=f"外部内核超时（{timeout}s）",
                            elapsed_ms=_elapsed(started))
    except OSError as e:
        return _observation(arm.name, error=f"外部内核启动失败: {e}",
                            elapsed_ms=_elapsed(started))
    elapsed = _elapsed(started)
    if proc.returncode != 0:
        detail = (proc.stderr or b"").decode("utf-8", "replace").strip()[:200]
        msg = f"外部内核 rc={proc.returncode}" + (f": {detail}" if detail else "")
        return _observation(arm.name, error=msg, elapsed_ms=elapsed)
    try:
        payload = json.loads((proc.stdout or b"").decode("utf-8", "replace"))
    except ValueError:
        return _observation(arm.name, error="外部内核输出不是合法 JSON",
                            elapsed_ms=elapsed)
    obs = _payload_observation(arm, payload, elapsed)
    if not obs["final_url"]:
        obs["final_url"] = url
    return obs


def _run_http(url, arm, timeout, ua, started):
    data = json.dumps({"url": url, "engine": arm.engine or "",
                       "ua": ua, "timeout": timeout}).encode("utf-8")
    req = request.Request(arm.endpoint, data=data, method="POST",
                          headers={"Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(MAX_ENVELOPE + 1)
            http_status = resp.status
    except urlerror.HTTPError as e:
        return _observation(arm.name, status=e.code,
                            error=f"外部内核 HTTP {e.code}",
                            elapsed_ms=_elapsed(started))
    except (urlerror.URLError, TimeoutError, OSError) as e:
        return _observation(arm.name, error=f"外部内核连接失败: {e}",
                            elapsed_ms=_elapsed(started))
    elapsed = _elapsed(started)
    if len(raw) > MAX_ENVELOPE:
        return _observation(arm.name, error="外部内核响应过大（超过 16 MiB 内容上限）",
                            elapsed_ms=elapsed)
    try:
        payload = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return _observation(arm.name, error="外部内核响应不是合法 JSON",
                            elapsed_ms=elapsed)
    if isinstance(payload, dict) and "status" not in payload:
        payload["status"] = http_status
    obs = _payload_observation(arm, payload, elapsed)
    if not obs["final_url"]:
        obs["final_url"] = url
    return obs


def fetch(url, arm, timeout=20, ua=UA_DEFAULT):
    if getattr(arm, "transport", None) not in ("cmd", "http"):
        raise ValueError("render.fetch 需要 cmd / http 传输的臂描述")
    reason = arm.unavailable_reason()
    if reason:
        return _observation(arm.name, error=reason, skipped=True)
    started = time.monotonic()
    if arm.transport == "cmd":
        return _run_cmd(url, arm, timeout, ua, started)
    return _run_http(url, arm, timeout, ua, started)
```

- [ ] **Step 4: 跑测试，确认通过**

```bash
python3 -m unittest tests.test_render -v
```
Expected: 8 tests PASS。

- [ ] **Step 5: 提交**

```bash
git add scripts/reconlib/render.py tests/test_render.py
git -c user.name="neoLsH" -c user.email="neoLsH@users.noreply.github.com" \
  commit -m "render: cmd/http 外部内核适配与 16 MiB 上限"
```

---

## Task 3: Session 编排

**Files:**
- Create: `scripts/reconlib/session.py`
- Test: `tests/test_session.py`

- [ ] **Step 1: 写失败测试**

```python
import os
import sys
import tempfile
import unittest

import support
from reconlib import arms, fetch
from reconlib.session import ArmSession, host_of


def empty_registry():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"arms": []}')
    return arms.load(path)


def render_arm(script_body, name="browser:chromium"):
    fd, path = tempfile.mkstemp(suffix=".py")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        f.write(script_body)
    cfg = tempfile.mkstemp(suffix=".json")[1]
    with open(cfg, "w", encoding="utf-8") as f:
        import json
        f.write(json.dumps({"arms": [{
            "name": name, "transport": "cmd",
            "command": [sys.executable, path, "{url}"],
            "capabilities": ["tls", "js"]}]}))
    return arms.load(cfg)


class SessionTest(unittest.TestCase):
    def test_ok_page_records_evidence_and_trial(self):
        routes = {"/ok": lambda h: support.response(h, 200, support.long_html())}
        with support.serve(routes) as base:
            s = ArmSession(["native"], registry=empty_registry(),
                           policy="fixed", delay=0, permission=True)
            page = s.probe(base + "/ok", selector="#content", min_content=200)
        self.assertEqual(page["evidence"]["outcome"], "ok")
        self.assertEqual(page["requests"], 1)
        self.assertEqual(page["stopped"], "ok")
        self.assertEqual(len(s.trials), 1)
        self.assertEqual(s.trials[0]["kind"], "trial")

    def test_robots_deny_makes_zero_requests(self):
        hits = {"n": 0}

        def page(h):
            hits["n"] += 1
            support.response(h, 200, support.long_html())

        routes = {"/robots.txt": lambda h: support.response(
                      h, 200, "User-agent: *\nDisallow: /private\n", ctype="text/plain"),
                  "/private/a": page}
        with support.serve(routes) as base:
            s = ArmSession(["native"], registry=empty_registry(), delay=0)
            got = s.probe(base + "/private/a")
        self.assertEqual(got["skipped"], "robots-deny")
        self.assertEqual(got["requests"], 0)
        self.assertEqual(hits["n"], 0)

    def test_unavailable_arm_is_skipped_not_counted(self):
        s = ArmSession(["browser:chromium"], registry=empty_registry(), delay=0,
                       permission=True)
        page = s.probe("http://127.0.0.1:9/x")
        self.assertEqual(page["requests"], 0)
        self.assertEqual(page["halt"], "no-arm")
        self.assertTrue(page["tries"][0]["skipped"])

    def test_budget_stops_before_request(self):
        routes = {"/ok": lambda h: support.response(h, 200, support.long_html())}
        with support.serve(routes) as base:
            s = ArmSession(["native"], registry=empty_registry(), delay=0,
                           permission=True)
            page = s.probe(base + "/ok", max_attempts=0)
        self.assertEqual(page["halt"], "budget")
        self.assertEqual(page["requests"], 0)

    def test_circuit_opens_and_blocks_next_page(self):
        routes = {"/wall": lambda h: support.response(h, 403, "Forbidden")}
        with support.serve(routes) as base:
            s = ArmSession(["native"], registry=empty_registry(), delay=0,
                           permission=True, max_consecutive_blocked=1)
            first = s.probe(base + "/wall")
            second = s.probe(base + "/wall")
        self.assertEqual(first["stopped"], "circuit-open")
        self.assertTrue(second["skipped"].startswith("circuit-open"))
        self.assertEqual(second["requests"], 0)

    def test_require_filters_without_request(self):
        s = ArmSession(["native"], registry=empty_registry(), delay=0,
                       permission=True)
        page = s.probe("http://127.0.0.1:9/x", require=("js",))
        self.assertEqual(page["halt"], "no-arm")
        self.assertIn("缺少能力", page["tries"][0]["reason"])

    def test_external_error_becomes_noise(self):
        reg = render_arm("import sys\nsys.stderr.write('boom')\nsys.exit(2)\n")
        s = ArmSession(["browser:chromium"], registry=reg, policy="fixed",
                       delay=0, permission=True)
        page = s.probe("http://127.0.0.1:9/x", selector="#content")
        self.assertEqual(page["evidence"]["outcome"], "noise")
        self.assertEqual(page["requests"], 1)
        self.assertEqual(page["stopped"], "noise")

    def test_stop_on_ok_false_runs_all_arms(self):
        ok_script = (
            "import json,sys\n"
            "print(json.dumps({'status': 200, 'body': "
            "'<article id=\"content\">' + '正文' * 120 + '</article>'}))\n")
        reg = render_arm(ok_script)
        # 第二个臂用同一配置复制一份名称
        import json
        cfg = tempfile.mkstemp(suffix=".json")[1]
        command = reg.get("browser:chromium").command
        with open(cfg, "w", encoding="utf-8") as f:
            f.write(json.dumps({"arms": [
                {"name": "browser:chromium", "transport": "cmd",
                 "command": command, "capabilities": ["tls"]},
                {"name": "browser:webkit", "transport": "cmd",
                 "command": command, "capabilities": ["tls"]}]}))
        reg = arms.load(cfg)
        s = ArmSession(["browser:chromium", "browser:webkit"], registry=reg,
                       policy="round_robin", delay=0, permission=True)
        page = s.probe("http://127.0.0.1:9/x", selector="#content",
                       max_attempts=2, stop_on_ok=False)
        self.assertEqual(len(page["tries"]), 2)
        self.assertEqual(page["requests"], 2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试，确认失败**

```bash
python3 -m unittest tests.test_session -v
```
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 实现 `scripts/reconlib/session.py`**

```python
"""session — 唯一网络编排层：robots → pacing → 选臂 → 取样 → 归类 → 更新 → 行。

不打印、不写文件。调用方传 save 回调决定是否落盘，拿返回的行决定落盘位置。
所有能力点（fetch / crawl / canary / quick / matrix）共用这一层，避免每个 CLI
再写一份“先 robots、再节流、再试臂”的逻辑。
"""

from urllib.parse import urlsplit

from . import UA_DEFAULT, evidence, fetch, measure, pacing, render, robots
from .classify import classify
from .policy import ALGORITHMS, Bandit

RANK = {"ok": 3, "degraded": 2, "blocked": 1}
NO_ARM = "没有可用的客户端臂"


def host_of(url):
    return urlsplit(url).netloc or url


def fetch_observation(url, arm, timeout=20, ua=UA_DEFAULT):
    if arm.transport in ("native", "cffi"):
        return fetch.fetch_one(url, arm.name, timeout=timeout, ua=ua)
    return render.fetch(url, arm, timeout=timeout, ua=ua)


def display_arm(arm):
    if arm.transport in ("native", "cffi"):
        return fetch.display_arm(arm.name)
    return arm.name


class ArmSession:
    def __init__(self, arm_names, registry=None, policy="thompson", seed=None,
                 min_pulls=1, delay=pacing.DEFAULT_DELAY, timeout=20,
                 ua=UA_DEFAULT, max_consecutive_blocked=3, save=None,
                 require=(), permission=False, clock=None, sleep=None):
        from . import arms as armreg
        self.registry = registry or armreg.load()
        self.arms = [self.registry.get(name) for name in arm_names]
        if not self.arms:
            raise ValueError("至少需要一个客户端臂")
        if policy not in ALGORITHMS:
            raise ValueError(f"未知策略 {policy!r}")
        self.bandit = Bandit([a.name for a in self.arms], algorithm=policy,
                             seed=seed, min_pulls=min_pulls)
        kw = {"delay": delay,
              "max_consecutive_blocked": max_consecutive_blocked}
        if clock is not None:
            kw["clock"] = clock
        if sleep is not None:
            kw["sleep"] = sleep
        self.pace = pacing.Pacing(**kw)
        self.timeout = timeout
        self.ua = ua
        self.save = save
        self.require = tuple(require)
        self.permission = permission
        self.trials = []

    def snapshot(self, host):
        return self.bandit.snapshot(host)

    def probe(self, url, selector="", min_content=200, max_attempts=None,
              require=None, permission=None, warn=None, stop_on_ok=True):
        require = tuple(self.require if require is None else require)
        permission = self.permission if permission is None else permission
        host = host_of(url)
        page = {"url": url, "host": host, "tries": [], "evidence": None,
                "stopped": None, "skipped": None, "halt": None,
                "requests": 0, "robots": "skipped" if permission else None,
                "last": None}
        if not permission:
            allowed = robots.decision(url, timeout=self.timeout, ua=self.ua)
            page["robots"] = allowed
            if allowed == "deny":
                page["skipped"] = "robots-deny"
                page["stopped"] = "robots-deny"
                return page
            if allowed == "unknown" and warn:
                warn(host)
        if self.pace.aborted(host):
            reason = self.pace.snapshot(host)["reason"]
            page["skipped"] = f"circuit-open: {reason}"
            page["stopped"] = "circuit-open"
            page["halt"] = "circuit-open"
            return page

        candidates, skipped = self.registry.filter([a.name for a in self.arms],
                                                   require)
        if not candidates:
            page["halt"] = "no-arm"
            page["skipped"] = "no-arm"
            page["stopped"] = "no-arm"
            for name, reason in skipped:
                page["tries"].append({"arm": name, "skipped": True,
                                      "outcome": "noise", "reason": reason})
            return page

        used = 0
        while candidates:
            if max_attempts is not None and used >= max_attempts:
                page["halt"] = "budget"
                page["stopped"] = "budget"
                break
            name = self.bandit.select(host, candidates)
            arm = self.registry.get(name)
            self.pace.wait(host)
            obs = fetch_observation(url, arm, timeout=self.timeout, ua=self.ua)
            html = measure.is_html(obs.get("ctype"))
            content = measure.content_len(obs.get("body") or b"",
                                          selector) if html else "-"
            verdict = classify(obs, selector, min_content, content=content)

            if obs.get("skipped"):
                page["tries"].append({
                    "arm": name, "skipped": True, "outcome": verdict["outcome"],
                    "reason": obs.get("error") or "skipped"})
                candidates.remove(name)
                continue

            used += 1
            page["last"] = dict(verdict)
            state = self.pace.observe(
                host, verdict["outcome"], verdict["rate_limited"],
                pacing.retry_after(obs.get("headers") or ""))
            self.bandit.update_from_outcome(host, name, verdict["outcome"],
                                            verdict["rate_limited"])
            saved = self.save(url, name, obs.get("body") or b"") if self.save else None
            trial = evidence.trial_row(
                url, name, verdict, attempt=len(page["tries"]) + 1,
                elapsed_ms=obs.get("elapsed_ms"))
            if saved:
                trial["saved"] = saved
            self.trials.append(trial)
            page["tries"].append({
                "arm": name, "status": obs.get("status", 0),
                "bytes": obs.get("bytes", 0), "content": content,
                "outcome": verdict["outcome"],
                "attributable": verdict["attributable"],
                "rate_limited": verdict["rate_limited"], "saved": saved,
                "elapsed_ms": obs.get("elapsed_ms", 0),
                "final": obs.get("final_url") or url,
            })
            obs["content"] = content
            row = evidence.evidence_row(obs, verdict, route=evidence.route_of(url))
            if page["evidence"] is None or RANK.get(row["outcome"], 0) > \
                    RANK.get(page["evidence"]["outcome"], 0):
                page["evidence"] = row

            if verdict["outcome"] == "ok":
                page["stopped"] = "ok"
                if stop_on_ok:
                    page["requests"] = used
                    return page
                candidates.remove(name)
                continue
            if not verdict["attributable"]:
                page["stopped"] = verdict["outcome"]
                page["requests"] = used
                return page
            candidates.remove(name)
            if state["aborted"]:
                page["stopped"] = "circuit-open"
                page["halt"] = "circuit-open"
                page["reason"] = state["reason"]
                page["requests"] = used
                return page

        page["requests"] = used
        if page["evidence"] is None and page["halt"] is None:
            page["stopped"] = "no-arm"
        return page
```

- [ ] **Step 4: 跑测试，确认通过**

```bash
python3 -m unittest tests.test_session -v
```
Expected: 8 tests PASS。

- [ ] **Step 5: 提交**

```bash
git add scripts/reconlib/session.py tests/test_session.py
git -c user.name="neoLsH" -c user.email="neoLsH@users.noreply.github.com" \
  commit -m "session: 统一 robots/pacing/选臂/归类/更新编排"
```

---

## Task 4: crawl 接入

**Files:**
- Modify: `scripts/crawl.py`
- Test: `tests/test_crawl.py`

- [ ] **Step 1: 加失败测试**

在 `tests/test_crawl.py` 追加：

```python
    def test_unconfigured_browser_arm_is_skipped(self):
        cfg = support.write_temp('{"arms": []}')
        try:
            r, data = support.run_json("crawl", "http://127.0.0.1:9/x",
                                       "--arms", "browser:chromium",
                                       "--arms-file", cfg, "--delay", "0",
                                       "--timeout", "5", "--json")
        finally:
            os.unlink(cfg)
        self.assertEqual(r.returncode, 1)
        page = data["pages"][0]
        self.assertEqual(page["skipped"], "no-arm")
        self.assertEqual(page["requests"], 0)
        self.assertIn("没有可用的客户端臂", data["summary"]["stopped"])
```

- [ ] **Step 2: 跑测试，确认失败**

```bash
python3 -m unittest tests.test_crawl.CrawlTest.test_unconfigured_browser_arm_is_skipped -v
```
Expected: FAIL（`--arms-file` 未识别）。

- [ ] **Step 3: 替换 `probe_page` 与 `crawl`**

删除 `probe_page`，把 `crawl` 改成：

```python
def crawl(urls, args):
    from reconlib import arms as armreg
    from reconlib.session import ArmSession

    registry = armreg.load(args.arms_file)
    arm_list = armreg.parse_list(args.arms, registry, args.impersonate)
    require = armreg.parse_require(args.require)
    save = (lambda url, arm, body: fetch.save_html(args.out_dir, url, arm, body)
            if args.out_dir else None)
    session = ArmSession(
        arm_list, registry=registry, policy=args.policy, seed=args.seed,
        min_pulls=args.min_pulls, delay=args.delay, timeout=args.timeout,
        ua=args.ua, max_consecutive_blocked=args.max_consecutive_blocked,
        save=save, require=require, permission=args.i_have_permission)

    pages, requests, stopped = [], 0, None
    warned = set()

    def warn(host):
        if host not in warned:
            warned.add(host)
            print(f"# 警告：{host} 的 robots.txt 取不到，合规状态未知",
                  file=sys.stderr)

    for url in urls:
        if len(pages) >= args.max_pages:
            stopped = f"达到 max-pages={args.max_pages}"
            break
        page = session.probe(
            url, selector=args.selector, min_content=args.min_content,
            max_attempts=args.max_requests - requests, require=require,
            permission=args.i_have_permission, warn=warn)
        requests += page["requests"]
        pages.append(page)
        if page["halt"] == "budget":
            stopped = stopped or f"达到 max-requests={args.max_requests}"
            break
        if page["halt"] == "no-arm":
            stopped = stopped or "没有可用的客户端臂"
            break
        if page["halt"] == "circuit-open":
            stopped = stopped or f"circuit-open: {page.get('reason', '连续 blocked')}"
            # 熔断只封这个 host：余下 URL 会在 probe 顶部被记为 skipped

    ev_rows = [p["evidence"] for p in pages if p["evidence"]]
    trials = session.trials

    if args.evidence_out:
        evidence.write_jsonl(args.evidence_out, ev_rows)
    if args.trial_out:
        evidence.write_jsonl(args.trial_out, trials)

    outcomes = {k: 0 for k in evidence.OUTCOMES}
    for row in ev_rows:
        outcomes[row["outcome"]] += 1
    hosts = sorted({host_of(u) for u in urls})
    summary = {
        "requested": len(urls),
        "pages": len(pages),
        "requests": requests,
        "skipped_robots": sum(1 for p in pages if p.get("skipped") == "robots-deny"),
        "circuit_open": sum(1 for p in pages
                            if str(p.get("skipped", "")).startswith("circuit-open")),
        "outcomes": outcomes,
        "stopped": stopped,
        "policy": args.policy,
        "arms": arm_list,
        "seed": args.seed,
        "hosts": [session.snapshot(h) for h in hosts],
    }
    return pages, summary, ev_rows, trials
```

同时：
- 顶部导入改为 `from reconlib import arms, evidence, fetch, pacing` 与 `from reconlib.policy import ALGORITHMS`；删除 `measure`、`Bandit` 导入。
- 参数区新增：

```python
    ap.add_argument("--arms-file", help="外部臂配置 JSON（默认 ~/.config/site-recon/arms.json）")
    ap.add_argument("--require", default="", help="硬性能力要求，如 js（逗号分隔）")
```

- `parse_arms` 删除，改用 `armreg.parse_list`。

- [ ] **Step 4: 跑 crawl 全量测试**

```bash
python3 -m unittest tests.test_crawl -v
```
Expected: 10 tests PASS。

- [ ] **Step 5: 提交**

```bash
git add scripts/crawl.py tests/test_crawl.py
git -c user.name="neoLsH" -c user.email="neoLsH@users.noreply.github.com" \
  commit -m "crawl: 改用 ArmSession 编排，护栏与输出字段不变"
```

---

## Task 5: fetch-page 接入

**Files:**
- Modify: `scripts/fetch-page.py`
- Test: `tests/test_fetch.py`

- [ ] **Step 1: 加失败测试**

在 `tests/test_fetch.py` 追加：

```python
    def test_unconfigured_browser_arm_is_reported(self):
        cfg = support.write_temp('{"arms": []}')
        try:
            r, data = support.run_json("fetch", "http://127.0.0.1:9/x",
                                       "--arms", "browser:chromium",
                                       "--arms-file", cfg, *OPTS, "--json")
        finally:
            os.unlink(cfg)
        self.assertEqual(r.returncode, 1)
        rec = data["records"][0]
        self.assertEqual(rec["verdict"], "未拿到内容")
        self.assertTrue(rec["rungs"][0]["skipped"])
```

- [ ] **Step 2: 跑测试，确认失败**

```bash
python3 -m unittest tests.test_fetch.FetchCliTest.test_unconfigured_browser_arm_is_reported -v
```
Expected: FAIL（`--arms` 未识别）。

- [ ] **Step 3: 重写 `resolve_arms` 与 `probe`**

```python
def resolve_arms(args, registry):
    from reconlib import arms as armreg
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
    save = (lambda u, a, body: fetch.save_html(args.out_dir, u, a, body)
            if args.out_dir else None)
    session = ArmSession(
        arm_list, registry=registry, policy=args.policy, delay=0.0,
        timeout=args.timeout, ua=args.ua, save=save,
        require=armreg_require(args.require),
        permission=args.i_have_permission)
    page = session.probe(
        url, selector=args.selector, min_content=args.min_content,
        max_attempts=len(arm_list), require=armreg_require(args.require),
        permission=args.i_have_permission,
        warn=lambda host: print(f"# 警告：{url} 的 robots.txt 取不到，合规状态未知",
                                file=sys.stderr))
    record["robots"] = page["robots"]

    for t in page["tries"]:
        rung = {
            "rung": display_arm(registry.get(t["arm"])),
            "status": t.get("status", "-") if not t.get("skipped") else "-",
            "bytes": t.get("bytes", "-") if not t.get("skipped") else "-",
            "content": t.get("content", "-") if not t.get("skipped") else "-",
            "final": t.get("final", url), "saved": t.get("saved"),
            "outcome": t["outcome"],
            "attributable": t.get("attributable", False),
            "rate_limited": t.get("rate_limited", False),
            "elapsed_ms": t.get("elapsed_ms", 0),
        }
        if t.get("skipped"):
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


def armreg_require(text):
    from reconlib import arms as armreg
    return armreg.parse_require(text)
```

`main` 里：
- 新增 `--arms`（与 `--rung` 互斥）、`--policy`（`ALGORITHMS`，默认 `fixed`）、`--arms-file`、`--require`。
- 构建 registry：

```python
    from reconlib import arms as armreg
    from reconlib.policy import ALGORITHMS
    ...
    try:
        registry = armreg.load(args.arms_file)
        arm_list = resolve_arms(args, registry)
    except ValueError as e:
        ap.error(str(e))
```

- 循环改为调用 `probe(url, args, registry, arm_list)`。

- [ ] **Step 4: 跑 fetch 全量测试**

```bash
python3 -m unittest tests.test_fetch -v
```
Expected: 9 tests PASS。

- [ ] **Step 5: 提交**

```bash
git add scripts/fetch-page.py tests/test_fetch.py
git -c user.name="neoLsH" -c user.email="neoLsH@users.noreply.github.com" \
  commit -m "fetch: 阶梯改用 ArmSession，新增 --arms/--policy"
```

---

## Task 6: canary 固定臂

**Files:**
- Modify: `scripts/canary.py`
- Test: `tests/test_canary.py`

- [ ] **Step 1: 加失败测试**

```python
    def test_arm_is_chosen_once_and_recorded(self):
        with support.serve({"/ok": lambda h: support.response(h, 200, support.long_html())}) as base:
            r, data = support.run_json(
                "canary", base + "/ok", *PASSES, "--arms", "native,cffi:chrome",
                "--policy", "round_robin", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(data["arm"], "native")

    def test_no_available_arm_is_usage_error(self):
        cfg = support.write_temp('{"arms": []}')
        try:
            r = support.run("canary", "http://127.0.0.1:9/x", *PASSES,
                            "--arms", "browser:chromium", "--arms-file", cfg)
        finally:
            os.unlink(cfg)
        self.assertEqual(r.returncode, 2)
        self.assertIn("没有可用的客户端臂", r.stderr)
```

- [ ] **Step 2: 跑测试，确认失败**

```bash
python3 -m unittest tests.test_canary.CanaryTest.test_arm_is_chosen_once_and_recorded -v
```
Expected: FAIL（`--arms` 未识别）。

- [ ] **Step 3: 改 canary**

顶部新增：

```python
from reconlib import arms as armreg
from reconlib.policy import ALGORITHMS, Bandit
from reconlib.session import fetch_observation, host_of
```

新增函数：

```python
def choose_arm(args):
    registry = armreg.load(args.arms_file)
    names = armreg.parse_list(args.arms, registry, args.impersonate)
    require = armreg.parse_require(args.require)
    available, _ = registry.filter(names, require)
    if not available:
        raise ValueError("没有可用的客户端臂（检查 --arms / --arms-file / --require）")
    bandit = Bandit(available, algorithm=args.policy, seed=args.seed, min_pulls=0)
    return registry, bandit.select(host_of(args.url), available)
```

`main` 里：
- 新增参数：

```python
    ap.add_argument("--arms", default="native",
                    help="逗号分隔的候选臂，默认 native；整个 pass 序列只选一条")
    ap.add_argument("--policy", choices=ALGORITHMS, default="fixed",
                    help="选臂策略，默认 fixed")
    ap.add_argument("--seed", type=int, help="thompson 选臂的随机种子")
    ap.add_argument("--arms-file", help="外部臂配置 JSON")
    ap.add_argument("--require", default="", help="硬性能力要求，如 js")
    ap.add_argument("--impersonate", default="chrome", help="裸 cffi 臂的仿真目标")
```

- 在加载基线之后、pass 循环之前：

```python
    try:
        registry, arm = choose_arm(args)
    except ValueError as e:
        ap.error(str(e))
```

- pass 循环内把 `fetch(args.url, args.timeout, args.ua)` 换成：

```python
        obs = fetch_observation(args.url, registry.get(arm),
                                timeout=args.timeout, ua=args.ua)
        code = str(obs.get("status") or "000")
        size = obs.get("bytes") or len(obs.get("body") or b"")
        final = obs.get("final_url") or args.url
        ctype = obs.get("ctype") or ""
        html = (obs.get("body") or b"").decode("utf-8", "replace")
```

删除旧的 `fetch()` 函数。
- `rows.append` 增加 `"arm": arm`。
- `result` 增加 `"arm": arm`。
- 文本输出增加 `print(f"arm: {arm}")`。

- [ ] **Step 4: 跑 canary 全量测试**

```bash
python3 -m unittest tests.test_canary -v
```
Expected: 9 tests PASS。

- [ ] **Step 5: 提交**

```bash
git add scripts/canary.py tests/test_canary.py
git -c user.name="neoLsH" -c user.email="neoLsH@users.noreply.github.com" \
  commit -m "canary: 先选一条臂并在整个 pass 序列固定"
```

---

## Task 7: v1.14.0 自检、文档与发布

**Files:**
- Create: `references/arms.md`
- Modify: `scripts/self-check.py`, `scripts/reconlib/__init__.py`, `references/atoms.md`, `references/cli.md`, `references/toolbox.md`, `references/crawl.md`, `SKILL.md`, `README.md`, `AGENTS.md`, `CHANGELOG.md`

- [ ] **Step 1: 写 `references/arms.md`（约 90 行）**

必须覆盖：
- 臂 = 协议栈描述，不是绕过手段。
- 内置表：`native`、`cffi:<profile>`、`browser:<engine>`（未配置不可用）。
- 外部配置位置与优先级：`--arms-file` > `RECON_ARMS_FILE` > `~/.config/site-recon/arms.json`。
- cmd / http 两种契约、四个占位符、16 MiB 上限、错误归 noise。
- 能力 `tls/h2/js/cookies` 与 `--require` 的硬过滤语义。
- 安全边界：不传 Cookie/凭证/代理/出口；端点禁止 userinfo；同名覆盖规则。

- [ ] **Step 2: 更新自检**

`REQUIRED` 追加：

```python
    "references/arms.md",
    "scripts/reconlib/arms.py",
    "scripts/reconlib/render.py",
    "scripts/reconlib/session.py",
    "tests/test_arms.py",
    "tests/test_render.py",
    "tests/test_session.py",
```

新增 `check_arms_guard()`：

```python
def check_arms_guard():
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
```

在 `main()` 中 `check_crawl_guard()` 前调用 `check_arms_guard()`。

`check_atoms_contract()` 的 code 字符串加入：

```python
import reconlib.arms, reconlib.render, reconlib.session
```

- [ ] **Step 3: 更新文档与版本**

- `scripts/reconlib/__init__.py`：模块说明加 `arms / render / session`。
- `references/atoms.md`：在“采样”原子下补 `arms`（臂描述）与 `render`（外部内核分支）；`session` 标注为编排层，不是原子。
- `references/cli.md`：`fetch` / `crawl` / `canary` 补 `--arms`、`--arms-file`、`--policy`、`--require`；加一段“外部内核”说明。
- `references/toolbox.md`：补 `curl_cffi` 与外部渲染器适配说明（仓库不内置）。
- `references/crawl.md`：把“多臂选举”改为“ArmSession 编排”，说明 skipped 不计请求。
- `SKILL.md`：版本 `1.14.0`；按需加载表加 `arms.md`；命令示例加 `--arms-file`。
- `README.md`：目录树加 `arms.md`、`reconlib/arms.py`、`render.py`、`session.py`；状态段改为 v1.14.0；已知限制加“外部内核质量取决于适配器”。
- `AGENTS.md`：命令区加 `--arms-file` 说明。
- `CHANGELOG.md`：新增 `[1.14.0] — 2026-11-02`，写清注册表、双传输、session、三个能力点接入、默认行为不变。

- [ ] **Step 4: 跑全量门禁**

```bash
python3 scripts/self-check.py --tests
```
Expected: 0 失败。

- [ ] **Step 5: 提交、打标签、推送**

```bash
git add -A
git -c user.name="neoLsH" -c user.email="neoLsH@users.noreply.github.com" \
  commit -m "v1.14.0: 臂注册表、外部内核适配与统一编排"
git tag v1.14.0
GIT_SSH_COMMAND='ssh -i ~/.ssh/id_rsa_new_1 -o IdentitiesOnly=yes' \
  git push origin main
GIT_SSH_COMMAND='ssh -i ~/.ssh/id_rsa_new_1 -o IdentitiesOnly=yes' \
  git push origin v1.14.0
```
Expected: push 成功；随后用 `gh run list` 确认 CI 绿。

---

## Task 8: 上下文原子

**Files:**
- Create: `scripts/reconlib/context.py`
- Test: `tests/test_context.py`

- [ ] **Step 1: 写失败测试**

```python
import unittest

from reconlib import context


class RouteClassTest(unittest.TestCase):
    def test_classes(self):
        self.assertEqual(context.route_class("https://a.test/article/1"), "article")
        self.assertEqual(context.route_class("https://a.test/news/2026/x"), "article")
        self.assertEqual(context.route_class("https://a.test/list"), "list")
        self.assertEqual(context.route_class("https://a.test/"), "list")
        self.assertEqual(context.route_class("https://a.test/api/v1/x"), "api")
        self.assertEqual(context.route_class("https://a.test/x.json"), "api")
        self.assertEqual(context.route_class("https://a.test/help"), "other")


class ResponseClassTest(unittest.TestCase):
    def test_classes(self):
        self.assertEqual(context.response_class({"outcome": "ok"}), "content")
        self.assertEqual(context.response_class({"outcome": "degraded"}), "shell")
        self.assertEqual(context.response_class({"outcome": "blocked", "waf_no": 4}),
                         "challenge")
        self.assertEqual(context.response_class({"outcome": "auth"}), "login")
        self.assertEqual(context.response_class({"outcome": "missing"}), "missing")
        self.assertEqual(context.response_class({"outcome": "noise"}), "noise")
        self.assertEqual(context.response_class(None), "unknown")


class KeyTest(unittest.TestCase):
    def test_key_and_fallbacks(self):
        key = context.key("a.test", "article", "content")
        self.assertEqual(key, "a.test|article|content")
        self.assertEqual(context.fallbacks(key),
                         ["a.test|article|content", "a.test|article|*",
                          "a.test|*|*", "*"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 实现**

```python
"""context — 首响应即可得到的上下文键：host + 路由类 + 响应类。

不用站点常量，只看 URL 路径形状与本次结局。响应类只粗分七类，够用来问
“这类页面该用哪种内核”，不做完整八形态判定。
"""

import re
from urllib.parse import urlsplit

ARTICLE = re.compile(
    r"/(?:article|post|story|news|detail|item|content|blog|wiki|thread|topic)/", re.I)
LIST = re.compile(
    r"/(?:list|search|tag|category|archive|index|feed|topics|articles|s|page)/|/$",
    re.I)
API = re.compile(r"/(?:api|graphql|rest|v\d+)/|\.json$|/ajax/", re.I)


def route_class(url):
    path = urlsplit(str(url or "")).path or "/"
    if API.search(path):
        return "api"
    if ARTICLE.search(path):
        return "article"
    if LIST.search(path):
        return "list"
    return "other"


def response_class(verdict):
    outcome = (verdict or {}).get("outcome")
    if outcome == "ok":
        return "content"
    if outcome == "degraded":
        return "shell"
    if outcome == "blocked":
        return "challenge"
    if outcome == "auth":
        return "login"
    if outcome == "missing":
        return "missing"
    if outcome == "noise":
        return "noise"
    return "unknown"


def key(host, route, response):
    return f"{host}|{route}|{response}"


def fallbacks(key_text):
    host, route, response = key_text.split("|", 2)
    return [f"{host}|{route}|{response}", f"{host}|{route}|*",
            f"{host}|*|*", "*"]


def split(key_text):
    return key_text.split("|", 2)
```

- [ ] **Step 3: 跑测试并提交**

```bash
python3 -m unittest tests.test_context -v
git add scripts/reconlib/context.py tests/test_context.py
git -c user.name="neoLsH" -c user.email="neoLsH@users.noreply.github.com" \
  commit -m "context: 路由类、响应类与回退链"
```

---

## Task 9: ContextualBandit

**Files:**
- Modify: `scripts/reconlib/policy.py`
- Test: `tests/test_policy_context.py`

- [ ] **Step 1: 写失败测试**

```python
import unittest

from reconlib.policy import ContextualBandit

ARMS = ("native", "cffi:chrome")


class RewardTest(unittest.TestCase):
    def test_degraded_is_half_reward(self):
        b = ContextualBandit(ARMS, algorithm="fixed", min_pulls=0)
        b.update("h", "article|content", "native", "degraded", cost_ms=100)
        arm = b.snapshot("h", "article|content")["arms"][0]
        self.assertEqual(arm["pulls"], 1)
        self.assertEqual(arm["successes"], 0.5)
        self.assertEqual(arm["failures"], 0.5)


class CostTest(unittest.TestCase):
    def test_cost_penalty_prefers_fast_arm(self):
        b = ContextualBandit(ARMS, algorithm="ucb1", min_pulls=0, cost_weight=1.0)
        for _ in range(4):
            b.update("h", "article|content", "native", "ok", cost_ms=50)
            b.update("h", "article|content", "cffi:chrome", "ok", cost_ms=5000)
        self.assertEqual(b.select("h", "article|content", ARMS), "native")

    def test_cost_weight_zero_disables_penalty(self):
        b = ContextualBandit(ARMS, algorithm="fixed", min_pulls=0, cost_weight=0)
        b.update("h", "article|content", "cffi:chrome", "ok", cost_ms=5000)
        self.assertEqual(b.select("h", "article|content", ARMS), "native")


class FallbackTest(unittest.TestCase):
    def test_unknown_falls_back_to_route_level(self):
        b = ContextualBandit(ARMS, algorithm="ucb1", min_pulls=1)
        for _ in range(2):
            b.update("h", "article|content", "cffi:chrome", "ok", cost_ms=10)
            b.update("h", "article|content", "native", "blocked", cost_ms=10)
        self.assertEqual(b.select("h", "article|unknown", ARMS), "cffi:chrome")
        self.assertEqual(b.last_level("h", "article|unknown"), "h|article|*")

    def test_global_fallback_when_no_context_samples(self):
        b = ContextualBandit(ARMS, algorithm="ucb1", min_pulls=0)
        b.update("h", "list|content", "native", "ok", cost_ms=10)
        self.assertEqual(b.select("h", "article|unknown", ARMS), "native")

    def test_same_seed_same_sequence(self):
        def run():
            b = ContextualBandit(ARMS, algorithm="thompson", seed=5, min_pulls=0)
            b.update("h", "article|content", "native", "ok", cost_ms=10)
            b.update("h", "article|content", "cffi:chrome", "blocked", cost_ms=10)
            return [b.select("h", "article|unknown", ARMS) for _ in range(5)]

        self.assertEqual(run(), run())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 实现 `ContextualBandit`**

在 `policy.py` 追加：

```python
from . import context as ctxmod

REWARD = {"ok": 1.0, "degraded": 0.5, "blocked": 0.0}


class ContextualBandit:
    """按 (host, route, response) 维护后验；样本不足时沿回退链聚合。"""

    def __init__(self, arms, algorithm="thompson", seed=None, min_pulls=1,
                 ucb_c=1.414, cost_weight=0.1, cost_cap_ms=5000):
        self.arms = tuple(dict.fromkeys(arms))
        if not self.arms:
            raise ValueError("至少需要一个客户端臂")
        if algorithm not in ALGORITHMS:
            raise ValueError(f"未知策略 {algorithm!r}")
        self.algorithm = algorithm
        self.min_pulls = max(0, int(min_pulls))
        self.ucb_c = float(ucb_c)
        self.cost_weight = max(0.0, float(cost_weight))
        self.cost_cap_ms = max(1.0, float(cost_cap_ms))
        self._rng = random.Random(seed)
        self._state = {}
        self._level = {}

    def _state_of(self, key_text):
        return self._state.setdefault(key_text, {
            "arms": {a: [0, 0.0, 0.0, 0.0, 0] for a in self.arms},
            "rr": 0,
        })

    def _keys_for(self, full):
        host, route, response = ctxmod.split(full)
        keys = []
        for k in self._state:
            h, r, resp = ctxmod.split(k)
            if h != host:
                continue
            if route != "*" and r != route and r != "*":
                continue
            if response != "*" and resp != response and resp != "*":
                continue
            keys.append(k)
        return keys

    def _aggregate(self, full, candidates):
        stats = {a: [0, 0.0, 0.0, 0.0, 0] for a in candidates}
        for k in self._keys_for(full):
            for arm, v in self._state[k]["arms"].items():
                if arm not in stats:
                    continue
                for i in range(5):
                    stats[arm][i] += v[i]
        return stats

    def _penalty(self, stat):
        if not self.cost_weight or not stat[4]:
            return 0.0
        return self.cost_weight * min(1.0, stat[3] / stat[4] / self.cost_cap_ms)

    def _level_stats(self, host, context, candidates):
        full = f"{host}|{context}"
        levels = ctxmod.fallbacks(full)
        for level in levels[:-1]:
            stats = self._aggregate(level, candidates)
            if all(stats[a][0] >= self.min_pulls for a in candidates):
                return level, stats
        return levels[-1], self._aggregate(levels[-1], candidates)

    def select(self, host, context, candidates=None):
        arms = [a for a in (candidates or self.arms) if a in self.arms]
        if not arms:
            arms = list(self.arms)
        full = f"{host}|{context}"
        if self.algorithm == "fixed":
            self._level[full] = full
            return arms[0]
        if self.algorithm == "round_robin":
            state = self._state_of(full)
            arm = arms[state["rr"] % len(arms)]
            state["rr"] = (state["rr"] + 1) % len(arms)
            self._level[full] = full
            return arm

        level, stats = self._level_stats(host, context, arms)
        self._level[full] = level
        warm = [a for a in arms if stats[a][0] < self.min_pulls]
        if warm:
            return min(warm, key=lambda a: (stats[a][0], arms.index(a)))
        if self.algorithm == "ucb1":
            total = sum(stats[a][0] for a in arms)
            return max(arms, key=lambda a: self._ucb(stats[a], total))
        return max(arms, key=lambda a: self._thompson(stats[a]))

    def _thompson(self, stat):
        pulls, successes, failures, cost_sum, cost_n = stat
        return self._rng.betavariate(1 + successes, 1 + failures) - self._penalty(stat)

    def _ucb(self, stat, total):
        pulls, successes, _, _, _ = stat
        if pulls == 0:
            return float("inf")
        return (successes / pulls + self.ucb_c * math.sqrt(math.log(max(1, total)) / pulls)
                - self._penalty(stat))

    def update(self, host, context, arm, outcome, cost_ms=0, rate_limited=False):
        if arm not in self.arms:
            raise ValueError(f"未知客户端臂 {arm!r}")
        if rate_limited or outcome not in REWARD:
            return False
        reward = REWARD[outcome]
        stat = self._state_of(f"{host}|{context}")["arms"][arm]
        stat[0] += 1
        stat[1] += reward
        stat[2] += 1.0 - reward
        stat[3] += min(self.cost_cap_ms, max(0.0, float(cost_ms or 0)))
        stat[4] += 1
        return True

    def update_from_outcome(self, host, context, arm, outcome, elapsed_ms=0,
                            rate_limited=False):
        return self.update(host, context, arm, outcome, elapsed_ms, rate_limited)

    def last_level(self, host, context):
        return self._level.get(f"{host}|{context}")

    def _rows(self, stats):
        rows = [{
            "arm": arm,
            "pulls": int(v[0]),
            "successes": round(v[1], 3),
            "failures": round(v[2], 3),
            "mean": round(v[1] / v[0], 3) if v[0] else None,
            "cost_ms": round(v[3] / v[4]) if v[4] else None,
        } for arm, v in stats.items()]
        pulled = [r for r in rows if r["pulls"]]
        best = max(pulled, key=lambda r: (r["mean"], r["pulls"]))["arm"] if pulled else None
        return rows, best

    def snapshot(self, host, context=None):
        if context is not None:
            full = f"{host}|{context}"
            stats = self._aggregate(full, list(self.arms))
            rows, best = self._rows(stats)
            return {"host": host, "context": context,
                    "algorithm": self.algorithm,
                    "level": self._level.get(full), "best_arm": best,
                    "arms": rows}
        stats = self._aggregate(f"{host}|*|*", list(self.arms))
        rows, best = self._rows(stats)
        return {"host": host, "algorithm": self.algorithm,
                "best_arm": best, "arms": rows,
                "contexts": sum(1 for k in self._state
                                if ctxmod.split(k)[0] == host)}
```

`policy.py` 顶部补 `import math`（已有）与 `from . import context as ctxmod`。

- [ ] **Step 3: 跑测试并提交**

```bash
python3 -m unittest tests.test_policy tests.test_policy_context -v
git add scripts/reconlib/policy.py tests/test_policy_context.py
git -c user.name="neoLsH" -c user.email="neoLsH@users.noreply.github.com" \
  commit -m "policy: 上下文成本感知 Bandit（小数奖励 / 成本惩罚 / 回退链）"
```

---

## Task 10: session 接入上下文与成本

**Files:**
- Modify: `scripts/reconlib/session.py`, `scripts/reconlib/evidence.py`
- Test: `tests/test_session.py`, `tests/test_evidence.py`

- [ ] **Step 1: 加失败测试**

`tests/test_session.py` 追加：

```python
    def test_context_and_cost_are_recorded(self):
        routes = {"/ok": lambda h: support.response(h, 200, support.long_html())}
        with support.serve(routes) as base:
            s = ArmSession(["native"], registry=empty_registry(),
                           policy="fixed", delay=0, permission=True)
            page = s.probe(base + "/article/1", selector="#content",
                           min_content=200)
        self.assertEqual(page["context"], "article|content")
        self.assertEqual(s.trials[0]["context"], "article|content")
        self.assertIn("cost_ms", s.trials[0])
        self.assertEqual(page["evidence"]["context"], "article|content")
```

`tests/test_evidence.py` 追加：

```python
    def test_context_is_optional(self):
        row = evidence_row(obs(), cls(), context="a.test|article|content")
        self.assertEqual(row["context"], "a.test|article|content")
        self.assertNotIn("context", evidence_row(obs(), cls()))
        trial = trial_row("https://example.test/a", "native", cls(),
                          context="a.test|article|content", cost_ms=42)
        self.assertEqual(trial["context"], "a.test|article|content")
        self.assertEqual(trial["cost_ms"], 42)
```

- [ ] **Step 2: 改 evidence**

```python
def evidence_row(observation, classification, route=None, context=None):
    ...
    row = {...}
    if context:
        row["context"] = context
    return row


def trial_row(url, arm, classification, attempt=1, elapsed_ms=None,
              route=None, context=None, cost_ms=None):
    ...
    if context:
        row["context"] = context
    if cost_ms is not None:
        row["cost_ms"] = int(cost_ms)
    return row
```

- [ ] **Step 3: 改 session**

- 构造函数参数加 `cost_weight=0.1`，把 `Bandit` 换成 `ContextualBandit`：

```python
from . import context as ctxmod
from .policy import ALGORITHMS, ContextualBandit
...
        self.bandit = ContextualBandit(
            [a.name for a in self.arms], algorithm=policy, seed=seed,
            min_pulls=min_pulls, cost_weight=cost_weight)
```

- `probe` 增加 `route` 与上下文：

```python
        route = ctxmod.route_class(url)
        ctx = f"{route}|unknown"
        page["context"] = ctx
        page["contexts"] = []
```

- 选臂改为 `self.bandit.select(host, ctx, candidates)`。
- 取样后：

```python
            resp = ctxmod.response_class(verdict)
            ctx = f"{route}|{resp}"
            if ctx not in page["contexts"]:
                page["contexts"].append(ctx)
            page["context"] = ctx
            elapsed = obs.get("elapsed_ms") or 0
            state = self.pace.observe(...)
            self.bandit.update_from_outcome(host, ctx, name,
                                            verdict["outcome"],
                                            elapsed_ms=elapsed,
                                            rate_limited=verdict["rate_limited"])
            trial = evidence.trial_row(..., elapsed_ms=elapsed,
                                       context=f"{host}|{ctx}", cost_ms=elapsed)
            row = evidence.evidence_row(obs, verdict, route=evidence.route_of(url),
                                        context=f"{host}|{ctx}")
```

- `page["evidence"]["context"]` 由 evidence_row 写入。
- `page["last"]` 保持 classification。

- [ ] **Step 4: 跑测试并提交**

```bash
python3 -m unittest tests.test_session tests.test_evidence tests.test_crawl -v
git add scripts/reconlib/session.py scripts/reconlib/evidence.py \
  tests/test_session.py tests/test_evidence.py
git -c user.name="neoLsH" -c user.email="neoLsH@users.noreply.github.com" \
  commit -m "session: 上下文更新与成本记录"
```

---

## Task 11: quick.py 与薄壳

**Files:**
- Create: `scripts/quick.py`
- Modify: `scripts/quick-check.sh`, `scripts/recon.py`, `scripts/self-check.py`
- Test: `tests/test_quick.py`

- [ ] **Step 1: 写失败测试**

```python
import unittest

import support

OPTS = ("--selector", "#content", "--delay", "0", "--timeout", "10")


class QuickTest(unittest.TestCase):
    def test_ok_page_is_feasible(self):
        routes = {"/ok": lambda h: support.response(h, 200, support.long_html())}
        with support.serve(routes) as base:
            r, data = support.run_json("quick", base + "/ok", *OPTS, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        row = data["rows"][0]
        self.assertEqual(row["verdict"], "可行·HTTP")
        self.assertEqual(row["http"], "200")
        self.assertGreaterEqual(int(row["content"]), 200)

    def test_csr_shell_is_browser_feasible(self):
        body = ('<html><body><div id="root">加载中</div><script>'
                + "x" * 1200 + "</script></body></html>")
        with support.serve({"/app": lambda h: support.response(h, 200, body)}) as base:
            r, data = support.run_json("quick", base + "/app", *OPTS, "--json")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(data["rows"][0]["verdict"], "可行·浏览器")

    def test_challenge_is_suspected_block(self):
        routes = {"/wall": lambda h: support.response(
            h, 403, "<html><body>请完成安全验证</body></html>")}
        with support.serve(routes) as base:
            r, data = support.run_json("quick", base + "/wall", *OPTS, "--json")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(data["rows"][0]["verdict"], "疑似拦截")

    def test_robots_deny(self):
        hits = {"n": 0}

        def page(h):
            hits["n"] += 1
            support.response(h, 200, support.long_html())

        routes = {"/robots.txt": lambda h: support.response(
                      h, 200, "User-agent: *\nDisallow: /private\n", ctype="text/plain"),
                  "/private/a": page}
        with support.serve(routes) as base:
            r, data = support.run_json("quick", base + "/private/a", *OPTS, "--json")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(data["rows"][0]["verdict"], "合规禁止")
        self.assertEqual(hits["n"], 0)

    def test_one_request_per_url(self):
        hits = {"n": 0}

        def page(h):
            hits["n"] += 1
            support.response(h, 200, support.long_html())

        with support.serve({"/ok": page}) as base:
            support.run_json("quick", base + "/ok", *OPTS, "--json")
        self.assertEqual(hits["n"], 1)

    def test_unconfigured_arm_falls_back(self):
        cfg = support.write_temp('{"arms": []}')
        routes = {"/ok": lambda h: support.response(h, 200, support.long_html())}
        try:
            with support.serve(routes) as base:
                r, data = support.run_json(
                    "quick", base + "/ok", *OPTS, "--arms", "browser:chromium,native",
                    "--arms-file", cfg, "--json")
        finally:
            support.os.unlink(cfg)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(data["rows"][0]["verdict"], "可行·HTTP")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 实现 `scripts/quick.py`**

关键分类函数（完整移植 shell 词表）：

```python
MARKERS = re.compile(
    r"captcha|challenge|verify you are human|are you a robot|unusual traffic|"
    r"access denied|access restricted|request blocked|人机验证|安全验证|访问验证|"
    r"滑动验证|环境异常|异常访问", re.I)
LOGIN_PATH = re.compile(r"/(login|signin|sign-in|auth|passport|sso)([/?#]|$)", re.I)
LOGIN_FORM = re.compile(r"type=[\"']?password", re.I)
SCRIPT = re.compile(r"<script", re.I)


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
```

主循环：

```python
    registry = armreg.load(args.arms_file)
    arm_list = armreg.parse_list(args.arms, registry, args.impersonate)
    require = armreg.parse_require(args.require)
    session = ArmSession(
        arm_list, registry=registry, policy=args.policy, seed=args.seed,
        delay=args.delay, timeout=args.timeout, ua=args.ua,
        permission=args.skip_robots, require=require)
    rows, bad = [], 0
    for i, url in enumerate(urls):
        page = session.probe(
            url, selector=args.selector, min_content=args.min_content,
            max_attempts=1, require=require,
            permission=args.skip_robots, keep_body=True)
        if page["skipped"] == "robots-deny":
            rows.append((("合规禁止", "-", "-", "-", "-", url,
                          "robots.txt 不允许抓取")))
        else:
            obs = page.get("observation") or {}
            content = "-" if not obs else measure.content_len(
                obs.get("body") or b"", args.selector)
            verdict, reason = verdict_of(url, obs, content, args.selector,
                                         args.min_content)
            rows.append((verdict, str(obs.get("status") or "-"),
                         str(obs.get("bytes") or "-"), str(content),
                         obs.get("final_url") or url, url, reason))
        if not rows[-1][0].startswith("可行"):
            bad += 1
    print("verdict\thttp\tbytes\tcontent\tfinal_url\turl\treason")
    for row in rows:
        print("\t".join(str(x) for x in row))
    ...
    return 0 if bad == 0 else 1
```

`--json` 由 `recon.py` 解析表，不在 quick 内实现。

- [ ] **Step 3: session 加 `keep_body`**

`probe` 增加参数 `keep_body=False`；非 skipped 尝试后：

```python
            if keep_body:
                page["observation"] = obs
```

- [ ] **Step 4: 薄壳与入口**

`quick-check.sh` 全文改为：

```bash
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/quick.py" "$@"
```

`recon.py` 的 `TOOLS["quick"]` 改为 `("python", "quick.py", "可行性快筛：每 URL 1 请求 + robots，先分掉死路")`。

`self-check.py` 的 `REQUIRED` 加 `scripts/quick.py`、`tests/test_quick.py`；`check_tests` 无需改。

- [ ] **Step 5: 跑测试并提交**

```bash
python3 -m unittest tests.test_quick -v
git add scripts/quick.py scripts/quick-check.sh scripts/recon.py \
  scripts/reconlib/session.py scripts/self-check.py tests/test_quick.py
git -c user.name="neoLsH" -c user.email="neoLsH@users.noreply.github.com" \
  commit -m "quick: Python 实现 + 跨 URL 选臂，shell 变薄壳"
```

---

## Task 12: matrix.py 与薄壳

**Files:**
- Create: `scripts/matrix.py`
- Modify: `scripts/client-matrix.sh`, `scripts/recon.py`, `scripts/self-check.py`
- Test: `tests/test_matrix.py`

- [ ] **Step 1: 写失败测试**

```python
import unittest

import support

OPTS = ("--selector", "#content", "--delay", "0", "--timeout", "10")


class MatrixTest(unittest.TestCase):
    def test_exhaustive_by_default(self):
        routes = {"/ok": lambda h: support.response(h, 200, support.long_html())}
        with support.serve(routes) as base:
            r, data = support.run_json("matrix", base + "/ok", *OPTS, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(data["rows"]), 2)
        self.assertEqual({row["client"] for row in data["rows"]},
                         {"native", "cffi:chrome"})

    def test_stop_on_ok_runs_one_arm(self):
        routes = {"/ok": lambda h: support.response(h, 200, support.long_html())}
        with support.serve(routes) as base:
            r, data = support.run_json("matrix", base + "/ok", *OPTS,
                                       "--stop-on-ok", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(data["rows"]), 1)

    def test_html_offline(self):
        path = support.write_temp(support.long_html(), suffix=".html")
        try:
            r = support.run("matrix", "--html", path, "--selector", "#content")
        finally:
            support.os.unlink(path)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("content\t", r.stdout)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 实现 `scripts/matrix.py`**

核心：

```python
def run_matrix(url, args, registry, arm_list):
    require = armreg.parse_require(args.require)
    session = ArmSession(
        arm_list, registry=registry, policy=args.policy, seed=args.seed,
        delay=args.delay, timeout=args.timeout, ua=args.ua,
        permission=args.i_have_permission, require=require,
        save=(lambda u, a, body: fetch.save_html(
            args.save_html, u, f"p{args.pass_no}-{a}", body)) if args.save_html else None)
    page = session.probe(
        url, selector=args.selector, min_content=args.min_content,
        max_attempts=len(arm_list), require=require,
        permission=args.i_have_permission, stop_on_ok=args.stop_on_ok)
    rows = []
    for t in page["tries"]:
        rows.append({
            "client": t["arm"],
            "identity": registry.get(t["arm"]).engine or registry.get(t["arm"]).profile or "-",
            "http": t.get("status", "-") if not t.get("skipped") else "SKIP",
            "bytes": t.get("bytes", "-") if not t.get("skipped") else "-",
            "content": t.get("content", "-") if not t.get("skipped") else "-",
            "time_s": f"{(t.get('elapsed_ms') or 0) / 1000:.3f}",
            "final_url": t.get("final", url),
        })
    return rows, page
```

TAB 表头：

```python
HEADER = ("client", "identity", "http", "bytes", "content", "time_s", "final_url")
```

无 `--selector` 时去掉 `content` 列。`--html` 离线模式用 `measure.content_len` 输出 `content\tN`。

`--passes`：外层循环，每轮 `args.pass_no` 用于保存文件名；轮间由 session pacing 控制。

`--clients` 兼容：
- `curl` → `native`
- `cffi` → `cffi:chrome,cffi:safari`
- `node` / `py` → 报用法错误，提示改用 `--arms` 或外部内核。
- 未给 `--clients` 时默认 `--arms native,cffi:chrome`。

退出码：有任一行 `http` 为 2xx 且 `content` 为数字/非空 → 0；否则 1；用法错误 2。

- [ ] **Step 3: 薄壳与入口**

`client-matrix.sh` 全文改为：

```bash
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/matrix.py" "$@"
```

`recon.py` 的 `TOOLS["matrix"]` 改为 `("python", "matrix.py", ...)`。
`self-check.py` 的 `REQUIRED` 加 `scripts/matrix.py`、`tests/test_matrix.py`。

- [ ] **Step 4: 跑测试并提交**

```bash
python3 -m unittest tests.test_matrix -v
git add scripts/matrix.py scripts/client-matrix.sh scripts/recon.py \
  scripts/self-check.py tests/test_matrix.py
git -c user.name="neoLsH" -c user.email="neoLsH@users.noreply.github.com" \
  commit -m "matrix: Python 实现，默认穷举、--stop-on-ok 才提前停"
```

---

## Task 13: bandit 上下文推演与 advise 上下文发现

**Files:**
- Modify: `scripts/bandit.py`, `scripts/reconlib/simulate.py`, `scripts/reconlib/advice.py`
- Test: `tests/test_bandit.py`, `tests/test_advice.py`

- [ ] **Step 1: 加失败测试**

`tests/test_bandit.py` 追加：

```python
    def test_rates_json_context_groups(self):
        path = support.write_temp(json.dumps({
            "contexts": [
                {"context": "article|unknown", "pages": 40,
                 "rates": {"native": 0.4, "cffi:chrome": 0.75}},
                {"context": "list|unknown", "pages": 40,
                 "rates": {"native": 0.9, "cffi:chrome": 0.8}},
            ]}))
        try:
            r, data = support.run_json("bandit", "--rates-json", path,
                                       "--policies", "thompson",
                                       "--pages", "80", "--json")
        finally:
            support.os.unlink(path)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("contexts", data)
        self.assertEqual(len(data["contexts"]), 2)
        self.assertEqual(data["rows"][0]["policy"], "thompson")
```

`tests/test_advice.py` 追加：

```python
    def test_contexts_are_summarized(self):
        body = advice.recommend([
            {"route": "/a", "outcome": "blocked", "arm": "native",
             "context": "h|article|challenge"},
            {"route": "/a", "outcome": "ok", "arm": "cffi:chrome",
             "context": "h|article|content"},
        ])
        self.assertEqual(body["evidence"]["contexts"],
                         {"h|article|challenge": 1, "h|article|content": 1})
```

- [ ] **Step 2: 实现 `simulate.simulate_contexts`**

```python
def simulate_contexts(policy, groups, pages=None, seed=None, min_pulls=1,
                      cost_weight=0.0):
    from .policy import ContextualBandit

    if not groups:
        raise ValueError("至少需要一个上下文组")
    total_pages = pages or sum(g.get("pages", 0) for g in groups) or 1
    rng = random.Random(seed)
    bandit = ContextualBandit(
        sorted({a for g in groups for a in g["rates"]}),
        algorithm=policy, seed=seed, min_pulls=min_pulls,
        cost_weight=cost_weight)
    requests = successes = 0
    pulls = {}
    context_rows = {}
    for group in groups:
        ctx = group["context"]
        rates = group["rates"]
        n = group.get("pages") or max(1, total_pages // len(groups))
        gpulls = {a: 0 for a in rates}
        gwins = {a: 0 for a in rates}
        for _ in range(n):
            candidates = list(rates)
            while candidates:
                arm = bandit.select("sim", ctx, candidates)
                requests += 1
                pulls[arm] = pulls.get(arm, 0) + 1
                gpulls[arm] += 1
                ok = rng.random() < rates[arm]
                bandit.update("sim", ctx, arm, "ok" if ok else "blocked",
                              cost_ms=0)
                if ok:
                    successes += 1
                    gwins[arm] += 1
                    break
                candidates.remove(arm)
        context_rows[ctx] = {"pages": n, "pulls": gpulls, "wins": gwins}
    return {
        "policy": policy,
        "pages": total_pages,
        "requests": requests,
        "successes": successes,
        "access_rate": successes / total_pages if total_pages else 0,
        "requests_per_success": (requests / successes) if successes else None,
        "pulls": pulls,
        "contexts": context_rows,
    }
```

`bandit.py`：
- 新增 `--rates-json`，与 `--arms` / `--trials` 三选一。
- `load_contexts(path)` 校验：顶层 `contexts` 数组，每项 `context` 非空、`rates` 非空且 0–1。
- 有 `--rates-json` 时：`rows = [simulate_contexts(name, groups, pages=args.pages, ...)]`，`body["contexts"] = rows[0]["contexts"]`，`body["source"]="rates-json"`，`body["rates"]={}`。
- 表格输出在有 `contexts` 时加一段 `context` 行。

- [ ] **Step 3: 改 advice**

`_contexts(rows)`：

```python
def _contexts(rows):
    out = {}
    for row in rows:
        ctx = row.get("context")
        if ctx:
            out[ctx] = out.get(ctx, 0) + 1
    return out
```

`evidence["contexts"] = _contexts(rows)`。
在 `blocked` 分支加上下文差异 finding：

```python
    ctx_ok = {r.get("context") for r in rows if r.get("outcome") == "ok"}
    ctx_blocked = {r.get("context") for r in rows if r.get("outcome") == "blocked"}
    if ctx_ok and ctx_blocked and ctx_ok != ctx_blocked:
        findings.append({"code": "context-split",
                         "count": len(ctx_ok | ctx_blocked),
                         "message": "同一站点在不同上下文里结局不同；换臂前先按上下文分组"})
```

`switch-arm` 的 `why` 在有 `context-split` 时追加“先按上下文分组”。

- [ ] **Step 4: 跑测试并提交**

```bash
python3 -m unittest tests.test_bandit tests.test_advice tests.test_advise -v
git add scripts/bandit.py scripts/reconlib/simulate.py scripts/reconlib/advice.py \
  tests/test_bandit.py tests/test_advice.py
git -c user.name="neoLsH" -c user.email="neoLsH@users.noreply.github.com" \
  commit -m "bandit/advise: 上下文分组推演与差异发现"
```

---

## Task 14: v1.15.0 文档、门禁与发布

**Files:**
- Create: `references/strategy.md`
- Modify: `references/arms.md`, `references/atoms.md`, `references/cli.md`, `references/toolbox.md`, `references/crawl.md`, `references/bandit.md`, `references/forms.md`, `references/agent.md`, `SKILL.md`, `README.md`, `AGENTS.md`, `CHANGELOG.md`, `scripts/self-check.py`

- [ ] **Step 1: 写 `references/strategy.md`（约 100 行）**

必须覆盖：
- 上下文键 `host|route|response`，classes 与回退链。
- 奖励 `ok=1 / degraded=0.5 / blocked=0`，哪些结局不更新。
- 成本按 5000ms 归一化，`--cost-weight` 默认 0.1，0 关闭。
- 各能力点语义表：fetch/crawl 页内选举；canary 固定单臂；quick 每 URL 1 请求、跨 URL 学习；matrix 默认穷举；bandit 离线。
- 读法：同时看样本数、成本与回退层；上下文误判会带来选臂偏差。

- [ ] **Step 2: 更新文档**

- `references/arms.md`：加“上下文与成本”一节，指向 strategy.md。
- `references/atoms.md`：九个原子表补 `context`；`policy` 行补 `ContextualBandit`。
- `references/cli.md`：`quick` / `matrix` 改为 Python 入口；`bandit --rates-json`；`--cost-weight`；上下文列说明。
- `references/toolbox.md`：加外部内核适配器与 `--arms-file`。
- `references/crawl.md`：补上下文与成本字段。
- `references/bandit.md`：补 `--rates-json` 分组推演与限制。
- `references/forms.md` / `references/agent.md`：能力点列表加 quick/matrix 的臂语义。
- `SKILL.md`：版本 `1.15.0`；按需加载表加 `strategy.md`；命令示例更新。
- `README.md`：状态段改 v1.15.0；命令表 quick/matrix 说明；目录树加 strategy.md、quick.py、matrix.py；已知限制更新。
- `AGENTS.md`：命令区补 `--arms-file`、`--rates-json`。
- `CHANGELOG.md`：新增 `[1.15.0] — 2026-11-02`。

- [ ] **Step 3: 自检补检查**

`REQUIRED` 加 `references/strategy.md`；新增：

```python
def check_strategy_context():
    body = read("scripts/reconlib/session.py")
    pol = read("scripts/reconlib/policy.py")
    ev = read("scripts/reconlib/evidence.py")
    missing = []
    if "context_class" in body or "response_class" not in body:
        missing.append("session 未接入 response_class")
    if "ContextualBandit" not in pol:
        missing.append("policy 缺少 ContextualBandit")
    if "cost_weight" not in pol:
        missing.append("policy 缺少成本权重")
    if "context" not in ev or "cost_ms" not in ev:
        missing.append("evidence 行缺少 context / cost_ms")
    if missing:
        fail("策略上下文契约异常: " + "; ".join(missing))
        return
    ok("上下文策略：session 用 response_class、policy 有成本权重、行带 context/cost_ms")
```

在 `main()` 中调用。

- [ ] **Step 4: 跑全量门禁**

```bash
python3 scripts/self-check.py --tests
```
Expected: 0 失败。

- [ ] **Step 5: 提交、打标签、推送**

```bash
git add -A
git -c user.name="neoLsH" -c user.email="neoLsH@users.noreply.github.com" \
  commit -m "v1.15.0: 上下文成本感知与全能力点接入"
git tag v1.15.0
GIT_SSH_COMMAND='ssh -i ~/.ssh/id_rsa_new_1 -o IdentitiesOnly=yes' \
  git push origin main
GIT_SSH_COMMAND='ssh -i ~/.ssh/id_rsa_new_1 -o IdentitiesOnly=yes' \
  git push origin v1.15.0
```
Expected: push 成功；`gh run list` 两步 CI 均绿。
