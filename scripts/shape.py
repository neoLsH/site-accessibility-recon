#!/usr/bin/env python3
"""shape.py — 单响应页面形态分类：先认形态，再谈分层。

同一个 200 在静态页、CSR 空壳、降级软拦截里含义完全不同。本脚本只发 1 个请求
（或读 1 份留存 HTML），按可观测信号给出形态与下一步建议。

置信度固定为 heuristic：形态结论必须用真实浏览器基线复核。本脚本不判断
“是否被反爬”，它只回答“这份响应长什么样”。

用法:
    python3 scripts/shape.py https://example.com/page
    python3 scripts/shape.py --html recon/raw/p1.html
    python3 scripts/shape.py https://example.com/page --save-html recon/raw/p1.html --json
    python3 scripts/shape.py --html p1.html --explain        # 看命中了哪条判据
    python3 scripts/shape.py --html p1.html --signals my.json  # 换一份判据表

判据表默认读 assets/shape-signals.json；--signals 可整体替换，用于站点特有文案。

退出码: 0 = 拿到响应并完成分类；1 = 未拿到响应（unreachable）；2 = 用法错误
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from html.parser import HTMLParser
from urllib.parse import urlsplit

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36')

PAIRED = {'script', 'style', 'noscript', 'template', 'svg', 'title'}
DEFAULT_SIGNALS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'assets', 'shape-signals.json')
SIGNAL_KEYS = ('challenge', 'login_path', 'login_phrase', 'mounts', 'csr_globals')

NEXT = {
    'static-html': '直接跑 matrix 验证传输栈；完整率要跟真实浏览器基线比（降级 200 只有基线能判）',
    'csr-shell': '静态 HTML 是壳：先 surface 看采集面，再 headless 渲染后量内容区；别把整页长度当完整率',
    'challenge': '按 waf-classes 归类，先修上层（传输/出口/凭证），不要直接上求解',
    'login-wall': '需授权或凭证过期：走 S7 合法凭证或 S0 换数据源，不要反复换 IP',
    'error-page': '先看 L1 链路与状态码语义；5xx 属噪声，不计入反爬分母',
    'api-response': '这是 API 响应：直接采，别套 HTML 选择器；注意鉴权与限频',
    'thin-page': '低内容且无 CSR 迹象：可能是软拦截或业务空态，用真实浏览器基线对照',
    'unreachable': 'L1：先修 DNS/代理/TLS 连通，再谈指纹',
}


class _Scan(HTMLParser):
    """收集可见文本长度、脚本数、挂载点与登录表单等信号。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.open = {}
        self.text = []
        self.title = []
        self.scripts = 0
        self.ids = set()
        self.password = False
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        d = {k.lower(): (v or '') for k, v in attrs}
        if tag in PAIRED:
            self.open[tag] = self.open.get(tag, 0) + 1
        if tag == 'script':
            self.scripts += 1
        if tag == 'title':
            self._in_title = True
        if tag == 'input' and d.get('type', '').lower() == 'password':
            self.password = True
        if d.get('id'):
            self.ids.add(d['id'])

    def handle_startendtag(self, tag, attrs):
        d = {k.lower(): (v or '') for k, v in attrs}
        if tag.lower() == 'input' and d.get('type', '').lower() == 'password':
            self.password = True
        if d.get('id'):
            self.ids.add(d['id'])

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in PAIRED and self.open.get(tag):
            self.open[tag] -= 1
        if tag == 'title':
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title.append(data)
        elif not any(self.open.get(t) for t in PAIRED):
            self.text.append(data)


def text_len(scan):
    return len(re.sub(r'\s+', '', ''.join(scan.text)))


def scan_html(html):
    scan = _Scan()
    try:
        scan.feed(html)
        scan.close()
    except Exception:
        pass
    return scan


class _Redirects(urllib.request.HTTPRedirectHandler):
    count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _Redirects.count += 1
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, timeout, ua):
    _Redirects.count = 0
    req = urllib.request.Request(url, headers={
        'User-Agent': ua,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    })
    opener = urllib.request.build_opener(_Redirects)
    try:
        with opener.open(req, timeout=timeout) as r:
            return (r.status, r.geturl(), r.headers.get('Content-Type', ''),
                    _Redirects.count, r.read())
    except urllib.error.HTTPError as e:
        return (e.code, e.geturl() or url, e.headers.get('Content-Type', ''),
                _Redirects.count, e.read() or b'')
    except Exception as e:
        return (0, url, '', _Redirects.count, b'__ERR__' + str(e).encode())


def load_signals(path):
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except OSError as e:
        raise ValueError(f'读不到判据文件 {path}: {e}')
    except ValueError as e:
        raise ValueError(f'判据文件不是合法 JSON {path}: {e}')
    if not isinstance(data, dict):
        raise ValueError(f'判据文件顶层应为对象 {path}')
    missing = [k for k in SIGNAL_KEYS if not isinstance(data.get(k), list) or not data[k]]
    if missing:
        raise ValueError(f'判据文件缺少非空数组: {", ".join(missing)}')
    bad = [k for k in SIGNAL_KEYS if not all(isinstance(x, str) and x for x in data[k])]
    if bad:
        raise ValueError(f'判据条目必须是非空字符串: {", ".join(bad)}')
    cfg = {'mounts': {m.lower() for m in data['mounts']}}
    for key in ('challenge', 'login_path', 'login_phrase', 'csr_globals'):
        try:
            cfg[key] = re.compile('|'.join(f'(?:{p})' for p in data[key]),
                                  re.I if key != 'csr_globals' else 0)
        except re.error as e:
            raise ValueError(f'{key} 正则非法: {e}')
    return cfg


def matched(pattern, text, limit=5):
    out = []
    for m in pattern.finditer(text):
        s = m.group(0)
        if s not in out:
            out.append(s)
        if len(out) >= limit:
            break
    return out


def classify(status, final_url, ctype, redirects, body, min_content, cfg):
    raw = body.decode('utf-8', 'replace')
    scan = scan_html(raw)
    tlen = text_len(scan)
    head = raw[:8000]
    low_final = final_url.lower()
    path_q = urlsplit(low_final).path + '?' + urlsplit(low_final).query

    hits = {
        'challenge': matched(cfg['challenge'], head),
        'login_phrase': matched(cfg['login_phrase'], head),
        'login_path': matched(cfg['login_path'], path_q),
        'mounts': sorted(scan.ids & cfg['mounts']),
        'csr_globals': matched(cfg['csr_globals'], head),
    }
    facts = {
        'http': status,
        'bytes': len(body),
        'text_len': tlen,
        'redirects': redirects,
        'scripts': scan.scripts,
        'password_field': scan.password,
    }
    signals = {
        'challenge': bool(hits['challenge']),
        'login': (scan.password or (redirects > 0 and bool(hits['login_path']))
                  or bool(hits['login_phrase'])),
        'password_field': scan.password,
        'mount_point': bool(hits['mounts']),
        'csr_global': bool(hits['csr_globals']),
        'scripts': scan.scripts,
    }

    def explain(decisions):
        return {'matched': hits, 'facts': facts, 'decisions': decisions}

    if status == 0:
        return 'unreachable', {}, explain([{'gate': 'network', 'hit': False}])

    looks_json = raw.lstrip()[:1] in ('{', '[')
    if (ctype and 'html' not in ctype.lower()) or (looks_json and not raw.lstrip().startswith('<')):
        return 'api-response', signals, explain([{'gate': 'api-response', 'hit': True}])

    challenge = bool(hits['challenge']) and (
        not (200 <= status < 300) or tlen < min_content or len(body) < 20000)
    login_wall = (status == 401
                  or scan.password
                  or (redirects > 0 and bool(hits['login_path']) and tlen < 1200)
                  or (bool(hits['login_phrase']) and tlen < max(min_content * 2, 400)))
    csr = (tlen < min_content
           and (bool(hits['mounts']) or bool(hits['csr_globals']))
           and scan.scripts > 0)
    decisions = [
        {'gate': 'challenge', 'hit': challenge and status != 401},
        {'gate': 'login-wall', 'hit': login_wall},
        {'gate': 'error-page', 'hit': status >= 400},
        {'gate': 'csr-shell', 'hit': csr},
        {'gate': 'static-html', 'hit': tlen >= min_content},
    ]

    if challenge and status != 401:
        shape = 'challenge'
    elif login_wall:
        shape = 'login-wall'
    elif status >= 400:
        shape = 'error-page'
    elif csr:
        shape = 'csr-shell'
    elif tlen >= min_content:
        shape = 'static-html'
    else:
        shape = 'thin-page'
    return shape, signals, explain(decisions)


def main():
    ap = argparse.ArgumentParser(description='单响应页面形态分类（启发式）')
    ap.add_argument('url', nargs='?', help='目标 URL')
    ap.add_argument('--html', help='直接读留存 HTML（离线复核）')
    ap.add_argument('--min-content', type=int, default=200, help='可见文本字符数下限，默认 200')
    ap.add_argument('--timeout', type=int, default=20, help='单请求超时秒数，默认 20')
    ap.add_argument('--ua', default=UA, help='User-Agent')
    ap.add_argument('--save-html', help='把响应体另存到该路径')
    ap.add_argument('--signals', help=f'判据表 JSON，默认 {DEFAULT_SIGNALS}')
    ap.add_argument('--explain', action='store_true', help='逐条打印命中的判据与门序')
    ap.add_argument('--json', action='store_true', help='输出单个 JSON 对象')
    args = ap.parse_args()

    if not args.url and not args.html:
        ap.error('需要 URL 或 --html')
    if args.url and args.html:
        ap.error('URL 与 --html 只能给一个')
    try:
        cfg = load_signals(args.signals or DEFAULT_SIGNALS)
    except ValueError as e:
        ap.error(str(e))

    if args.html:
        if not os.path.exists(args.html):
            ap.error(f'找不到文件: {args.html}')
        with open(args.html, 'rb') as f:
            body = f.read()
        status, final_url, ctype, redirects = 200, args.html, 'text/html', 0
    else:
        status, final_url, ctype, redirects, body = fetch(args.url, args.timeout, args.ua)
        if args.save_html and body and not body.startswith(b'__ERR__'):
            os.makedirs(os.path.dirname(os.path.abspath(args.save_html)), exist_ok=True)
            with open(args.save_html, 'wb') as f:
                f.write(body)

    shape, signals, explain = classify(
        status, final_url, ctype, redirects, body, args.min_content, cfg)
    scan = scan_html(body.decode('utf-8', 'replace')) if not body.startswith(b'__ERR__') else None
    result = {
        'shape': shape,
        'confidence': 'heuristic',
        'url': args.url,
        'http': status,
        'final_url': final_url,
        'redirects': redirects,
        'bytes': len(body),
        'content_type': ctype,
        'text_len': text_len(scan) if scan else 0,
        'scripts': scan.scripts if scan else 0,
        'signals': signals,
        'explain': explain,
        'next': NEXT[shape],
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"shape: {shape}  (heuristic)")
        print(f"http: {status}  redirects: {redirects}  bytes: {len(body)}  "
              f"text: {result['text_len']}  scripts: {result['scripts']}")
        print(f"final: {final_url}")
        if signals:
            flags = ' '.join(f"{k}={'yes' if v is True else 'no'}"
                             for k, v in signals.items() if isinstance(v, bool))
            print(f"signals: {flags}")
        if args.explain:
            hits = explain['matched']
            for key in ('challenge', 'login_phrase', 'login_path', 'mounts', 'csr_globals'):
                print(f"hit {key}: {', '.join(hits[key]) if hits[key] else '—'}")
            gates = '  '.join(f"{d['gate']}={'yes' if d['hit'] else 'no'}"
                              for d in explain['decisions'])
            print(f"gates: {gates}")
        print(f"next: {NEXT[shape]}")

    return 1 if shape == 'unreachable' else 0


if __name__ == '__main__':
    sys.exit(main())
