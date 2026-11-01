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
