"""robots — robots.txt 合规判定。

返回 allow / deny / unknown。unknown 不当作 allow：取不到 robots 时由调用方
警告并自行决定，至少要让“合规状态未知”可见。
"""

import re
import subprocess
from urllib.robotparser import RobotFileParser

from . import UA_DEFAULT


def decision(url, timeout=20, ua=UA_DEFAULT):
    m = re.match(r"^(https?://[^/]+)", url)
    if not m:
        return "unknown"
    origin = m.group(1)
    path = url[len(origin):] or "/"
    cmd = ["curl", "-sS", "-L", "--max-time", str(timeout), "-A", ua,
           "-w", "\n%{http_code}", f"{origin}/robots.txt"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True)
    except OSError:
        return "unknown"
    if out.returncode != 0:
        return "unknown"
    text, _, code = out.stdout.rpartition("\n")
    if code.startswith(("401", "403")):
        return "deny"
    if not code.startswith("2"):
        return "allow" if code.startswith("4") else "unknown"
    parser = RobotFileParser()
    parser.parse(text.splitlines())
    return "allow" if parser.can_fetch(ua, path) else "deny"
