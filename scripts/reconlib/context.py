"""context — 首响应即可得到的上下文键：host + 路由类 + 响应类。

不用站点常量，只看 URL 路径形状与本次结局。响应类只粗分七类，够用来问
“这类页面该用哪种内核”，不做完整八形态判定。
"""

import re
from urllib.parse import urlsplit

ARTICLE = re.compile(
    r"/(?:article|post|story|news|detail|item|content|blog|wiki|thread|topic)/", re.I)
LIST = re.compile(
    r"/(?:list|search|tag|category|archive|index|feed|topics|articles|s|page)(?:/|$)|/$",
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
