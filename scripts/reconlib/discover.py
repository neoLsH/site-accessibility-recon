"""discover — 从已留存 HTML 里发现同站候选路径。

只做解析与过滤：不发请求、不判 robots、不持有 frontier。调用方拿到链接后
仍要逐条走 robots、节流与配额——发现不等于许可。

过滤规则：
    - 只收 http / https，且 netloc 在白名单内（默认就是页面自身 host）；
    - 去掉 fragment；同一 URL 只保留第一次出现；
    - 静态资源后缀（图片 / 字体 / 样式 / 脚本 / 音视频）默认跳过：
      它们不是“页面路径”，量内容区只会得到噪声。
"""

from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlsplit

ASSET_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp",
    ".css", ".js", ".mjs", ".map", ".woff", ".woff2", ".ttf", ".otf",
    ".mp4", ".webm", ".mov", ".mp3", ".wav", ".m3u8",
)


class _Anchors(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        for key, value in attrs:
            if key.lower() == "href" and value:
                self.hrefs.append(value.strip())

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)


def anchors(html):
    """按出现顺序返回原始 href。"""
    if isinstance(html, bytes):
        html = html.decode("utf-8", "replace")
    parser = _Anchors()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        pass
    return parser.hrefs


def _host(url):
    return (urlsplit(url).netloc or "").lower()


def _asset(path):
    return path.lower().endswith(ASSET_SUFFIXES)


def links(html, base_url, hosts=None, max_links=50, skip_assets=True):
    """把页面里的 <a href> 解析成同站绝对 URL。

    hosts 为空时只允许 base_url 的 host。返回值去 fragment、去重、保序，
    最多 max_links 条；不做 robots 判定。
    """
    if max_links < 1:
        return []
    allowed = {str(h).lower() for h in (hosts or [_host(base_url)]) if h}
    out, seen = [], set()
    for href in anchors(html):
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:",
                                         "data:", "blob:")):
            continue
        try:
            absolute = urldefrag(urljoin(base_url, href))[0]
        except ValueError:
            continue
        parts = urlsplit(absolute)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            continue
        if parts.netloc.lower() not in allowed:
            continue
        if skip_assets and _asset(parts.path):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        out.append(absolute)
        if len(out) >= max_links:
            break
    return out
