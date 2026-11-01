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
            return ("未配置外部内核：请在 --arms-file / "
                    "~/.config/site-recon/arms.json 提供 command")
        if self.transport == "http":
            if self.endpoint:
                return ""
            return ("未配置外部内核：请在 --arms-file / "
                    "~/.config/site-recon/arms.json 提供 endpoint")
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
                raise ValueError(f"臂 {arm.name}: command 至少有一个 {{url}} 占位符")
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
        raise ValueError(f'臂配置格式应为 {{"arms": [...]}}: {path}')
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
