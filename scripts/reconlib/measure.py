"""measure — 内容区度量。

量“选择器命中的容器”里的可见字符数，不量整页字节：导航、页脚、内联脚本
会把空壳撑成“有内容”，是最常见的指标虚高来源。

    content_len(body, selector) -> int | "missing" | "?unsupported" | "-"
    is_html(ctype)              -> bool
"""

import re
from html.parser import HTMLParser

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr"}

SELECTOR = re.compile(r"([A-Za-z][\w-]*)?(#[\w-]+)?(\.[\w-]+)*")


class RegionLen(HTMLParser):
    """量选择器命中的容器内可见字符数（近似）。"""

    def __init__(self, sel):
        super().__init__(convert_charrefs=True)
        self.sel = sel
        self.depth = 0
        self.active = False
        self.matched = False
        self.total = 0
        self.skip = 0

    def _match(self, tag, attrs):
        a = dict(attrs)
        if self.sel.startswith("#"):
            return a.get("id") == self.sel[1:]
        if self.sel.startswith("."):
            return self.sel[1:] in (a.get("class") or "").split()
        return tag == self.sel

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
            return
        if not self.active:
            if self._match(tag, attrs):
                self.active = self.matched = True
                self.depth = 1
        elif tag not in VOID:
            self.depth += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
            return
        if self.active and self.depth > 0:
            self.depth -= 1
            if self.depth == 0:
                self.active = False

    def handle_data(self, data):
        if self.active and self.skip == 0:
            self.total += len(data.strip())


def content_len(body, selector):
    if not selector:
        return "-"
    if not SELECTOR.fullmatch(selector):
        return "?unsupported"
    if isinstance(body, bytes):
        html = body.decode("utf-8", "replace")
    else:
        html = str(body or "")
    if not html:
        return "missing"
    parser = RegionLen(selector)
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        pass
    return parser.total if parser.matched else "missing"


def is_html(ctype):
    """空 content-type 按 HTML 处理：curl -w 偶尔给空，不代表不是页面。"""
    return (not ctype) or ("html" in ctype) or ("xml" in ctype)
