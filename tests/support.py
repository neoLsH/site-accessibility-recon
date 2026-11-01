"""测试共用：子进程调用、临时 HTTP 服务、响应构造。"""

import contextlib
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECON = os.path.join(ROOT, "scripts", "recon.py")
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36")


def run(*args, timeout=120):
    return subprocess.run([sys.executable, RECON, *args],
                          capture_output=True, text=True, timeout=timeout)


def run_json(*args, timeout=120):
    r = run(*args, timeout=timeout)
    try:
        data = json.loads(r.stdout)
    except ValueError:
        raise AssertionError(
            f"输出不是 JSON（rc={r.returncode}）\nstdout={r.stdout[:600]}\nstderr={r.stderr[:600]}")
    return r, data


def fixture(name):
    return os.path.join(FIXTURES, name)


def temp_path(name):
    fd, path = tempfile.mkstemp(prefix="recon-test-", suffix=name)
    os.close(fd)
    return path


def write_temp(text, suffix=".json"):
    path = temp_path(suffix)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def response(handler, status, body, ctype="text/html; charset=utf-8", headers=()):
    if isinstance(body, str):
        body = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(body)))
    for key, value in headers:
        handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(body)


@contextlib.contextmanager
def serve(routes):
    """routes: {path: callable(handler)}；返回 http://127.0.0.1:<port>。"""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            fn = routes.get(self.path)
            if fn is None:
                response(self, 404, "not found")
                return
            fn(self)

        def log_message(self, *args):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def long_html(topic="内容"):
    paragraph = (f"{topic}段落用于模拟真实正文。可访问性诊断必须量内容区，"
                 "不能量整页长度，否则导航与页脚会把空壳撑成有内容。")
    return ("<html><head><title>基线页</title></head><body><article id=\"content\">"
            + paragraph * 6 + "</article></body></html>")
