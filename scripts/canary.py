#!/usr/bin/env python3
"""canary.py — 概率门定性：同一客户端栈多 pass，报翻转率与内容区区间。

一次请求只能说明“这一发怎么样”。同一 URL 跨 pass 翻转，说明它是概率门；
这时报单点成功率会被系统性高估或低估。本脚本固定客户端栈、只动时间，
给出 min / median / max、翻转次数与噪声占比。

节流红线：默认间隔 3s，最少 2s；pass 上限 10。连续探测会触发按 IP 的
频率降权，把限频误判成“指纹被识别”。

用法:
    python3 scripts/canary.py https://example.com/page --selector '#content' --passes 5
    python3 scripts/canary.py https://example.com/page --json

退出码: 0 = 全部 pass 都稳定拿到内容；1 = 存在翻转/拦截/内容不足/噪声；2 = 用法错误
"""

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
from html.parser import HTMLParser

MAX_PASSES = 10
DELAY = 3
MIN_DELAY = 2
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36')

SKIP = {'script', 'style', 'noscript', 'template', 'svg'}
CHALLENGE = re.compile(
    r'captcha|verify\s+(?:that\s+)?you\s+are\s+(?:a\s+)?human|'
    r'checking\s+your\s+browser|unusual\s+traffic|'
    r'安全验证|人机验证|滑动验证|请求(?:过于|太)频繁|请稍候', re.I)

MEANING = {
    'deterministic-pass': '同一栈多 pass 都稳定拿到内容：可以进入对策验证',
    'deterministic-degraded': '稳定但内容不足：先查抽取与路由，不是概率门',
    'deterministic-blocked': '稳定拦截：按 waf-classes 归类，不是概率门',
    'deterministic-auth': '稳定要求登录/凭证：走 S7 或 S0，不是概率门',
    'deterministic-missing': '稳定 404/410：内容业务状态，换数据源',
    'probability-gate': '概率门：报区间、别报单点；对策要按翻转率验证',
    'unstable-infra': '只看到噪声：先修链路/出口，别下反爬结论',
}


class _SelText(HTMLParser):
    """第一个匹配元素内的可见字符数。"""

    def __init__(self, attr, val):
        super().__init__(convert_charrefs=True)
        self.attr, self.val = attr, val
        self.tag = None
        # attr 为 None 时整页计数：从一开始就在作用域内，且永不匹配结束标签
        self.depth = 1 if attr is None else 0
        self.skip = 0
        self.chars = 0
        self.done = False

    def _hit(self, tag, d):
        if self.attr == 'id':
            return d.get('id') == self.val
        if self.attr == 'class':
            return self.val in (d.get('class') or '').split()
        return tag == self.val

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        d = {k.lower(): (v or '') for k, v in attrs}
        if self.done:
            return
        if self.depth == 0:
            if self._hit(tag, d):
                self.tag = tag
                self.depth = 1
            return
        if tag == self.tag:
            self.depth += 1
        if tag in SKIP:
            self.skip += 1

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self.done or self.depth == 0:
            return
        if tag in SKIP and self.skip:
            self.skip -= 1
        if tag == self.tag:
            self.depth -= 1
            if self.depth == 0:
                self.done = True

    def handle_data(self, data):
        if self.depth and not self.skip:
            self.chars += len(re.sub(r'\s+', '', data))


def selector_text(html, selector):
    sel = selector.strip()
    if not sel:
        return None
    if sel.startswith('#'):
        attr, val = 'id', sel[1:]
    elif sel.startswith('.'):
        attr, val = 'class', sel[1:]
    elif re.fullmatch(r'[a-zA-Z][a-zA-Z0-9-]*', sel):
        attr, val = 'tag', sel
    else:
        return '?unsupported'
    scan = _SelText(attr, val)
    try:
        scan.feed(html)
        scan.close()
    except Exception:
        return '?'
    return scan.chars if scan.done else 'missing'


def fetch(url, timeout, ua):
    fd, path = tempfile.mkstemp(prefix='canary-', suffix='.html')
    os.close(fd)
    try:
        out = subprocess.run(
            ['curl', '-sS', '-L', '--max-time', str(timeout), '-A', ua, '-o', path,
             '-w', '%{http_code}\t%{size_download}\t%{url_effective}\t%{content_type}', url],
            capture_output=True, text=True)
        if out.returncode != 0:
            return '000', 0, url, '', b''
        code, size, final, ctype = (out.stdout.split('\t') + [''] * 4)[:4]
        with open(path, 'rb') as f:
            body = f.read()
        return code or '000', int(size or 0), final or url, ctype, body
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def outcome(code, content, min_content, markers):
    if code in ('000', '') or code.startswith('5'):
        return 'blocked' if (markers and code != '000') else 'noise'
    if code == '401':
        return 'auth'
    if code in ('404', '410'):
        return 'missing'
    if code.startswith('4'):
        return 'blocked'
    if code.startswith('2'):
        if isinstance(content, int):
            return 'ok' if content >= min_content else 'degraded'
        return 'degraded' if content == 'missing' else 'ok'
    return 'noise'


def summarize(rows, passes):
    content = [r for r in rows if r['outcome'] != 'noise']
    outcomes = [r['outcome'] for r in content]
    flips = sum(1 for a, b in zip(outcomes, outcomes[1:]) if a != b)
    nums = [r['content'] for r in content if isinstance(r['content'], int)]
    median = statistics.median(nums) if nums else None
    if isinstance(median, float) and median.is_integer():
        median = int(median)
    if not content:
        verdict = 'unstable-infra'
    elif len(set(outcomes)) == 1:
        verdict = 'deterministic-pass' if outcomes[0] == 'ok' else f'deterministic-{outcomes[0]}'
    else:
        verdict = 'probability-gate'
    return {
        'verdict': verdict,
        'flip_count': flips,
        'flip_rate': round(flips / max(1, len(outcomes) - 1), 3),
        'noise_passes': passes - len(content),
        'outcomes': {o: outcomes.count(o) for o in sorted(set(outcomes))},
        'content': {
            'min': min(nums) if nums else None,
            'median': median,
            'max': max(nums) if nums else None,
        },
    }


def main():
    ap = argparse.ArgumentParser(description='概率门定性：同栈多 pass 的翻转率')
    ap.add_argument('url', help='目标 URL')
    ap.add_argument('--selector', default='', help='内容区选择器（#id / .class / 标签名）')
    ap.add_argument('--min-content', type=int, default=200, help='内容区字符数下限，默认 200')
    ap.add_argument('--passes', type=int, default=5, help=f'重复次数，默认 5，上限 {MAX_PASSES}')
    ap.add_argument('--delay', type=float, default=DELAY, help=f'pass 间隔秒数，默认 {DELAY}')
    ap.add_argument('--timeout', type=int, default=20, help='单请求超时秒数，默认 20')
    ap.add_argument('--ua', default=UA, help='User-Agent')
    ap.add_argument('--json', action='store_true', help='输出单个 JSON 对象')
    args = ap.parse_args()

    if args.passes < 2 or args.passes > MAX_PASSES:
        ap.error(f'--passes 必须在 2..{MAX_PASSES} 之间；长跑请分段并留间隔')
    delay = max(args.delay, MIN_DELAY)
    if args.delay < MIN_DELAY:
        print(f'# 间隔已抬到 {MIN_DELAY}s：连打会触发按 IP 降权', file=sys.stderr)

    rows = []
    for i in range(args.passes):
        if i:
            time.sleep(delay)
        code, size, final, ctype, body = fetch(args.url, args.timeout, args.ua)
        html = body.decode('utf-8', 'replace')
        if args.selector:
            mlen = selector_text(html, args.selector)
        else:
            scan = _SelText(None, None)
            try:
                scan.feed(html)
                scan.close()
            except Exception:
                pass
            mlen = scan.chars
        oc = outcome(code, mlen, args.min_content, bool(CHALLENGE.search(html[:8000])))
        rows.append({
            'pass': i + 1, 'http': code, 'bytes': size, 'content': mlen,
            'final_url': final, 'content_type': ctype, 'outcome': oc,
        })

    stats = summarize(rows, args.passes)
    result = {'url': args.url, 'selector': args.selector, 'min_content': args.min_content,
              'passes': args.passes, 'delay': delay, 'rows': rows, **stats}

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print('pass\thttp\tbytes\tcontent\toutcome\tfinal_url')
        for r in rows:
            print(f"{r['pass']}\t{r['http']}\t{r['bytes']}\t{r['content']}\t"
                  f"{r['outcome']}\t{r['final_url']}")
        c = stats['content']
        print('\n== 结论 ==')
        print(f"verdict: {stats['verdict']}")
        print(f"flips: {stats['flip_count']}  rate: {stats['flip_rate']}  "
              f"noise: {stats['noise_passes']}/{args.passes}")
        print(f"content: min {c['min']} / median {c['median']} / max {c['max']}")
        print(f"含义: {MEANING[stats['verdict']]}")

    return 0 if stats['verdict'] == 'deterministic-pass' else 1


if __name__ == '__main__':
    sys.exit(main())
