#!/usr/bin/env python3
"""stats.py — 双指标结算：把证据行聚合成按路由拆分的两个比率。

可访问性成功率 = (ok + degraded) / (总数 - 噪声)
内容完整率     = ok / (ok + degraded)

分母不同：完整率的分母是可访问成功的子集。噪声（5xx / 连接失败）单列，
不计入可访问性分母——把 5xx 算成“被反爬”是最常见的指标污染。

输入是 JSONL 行，或 canary --save 产出的 JSON（取其 rows）：
    {"route": "/article/1", "outcome": "ok", "content_len": 812}

outcome 取值：ok | degraded | blocked | auth | missing | noise
缺少 route 时从 final_url / url 的路径推断。

用法:
    python3 scripts/stats.py evidence.jsonl
    python3 scripts/stats.py evidence.jsonl canary-baseline.json --json

退出码: 0 = 结算完成；2 = 用法或数据格式错误
"""

import argparse
import json
import math
import sys
from urllib.parse import urlsplit

VALID = ('ok', 'degraded', 'blocked', 'auth', 'missing', 'noise')
DEFINITIONS = {
    'accessibility': '(ok + degraded) / (总数 - noise)',
    'completeness': 'ok / (ok + degraded)',
    'noise': '5xx 与连接失败单列，不进入可访问性分母',
}


def normalize(item, source, lineno=None):
    where = f'{source}:{lineno}' if lineno else source
    if not isinstance(item, dict):
        raise ValueError(f'{where}: 行不是对象')
    outcome = str(item.get('outcome') or '').strip().lower()
    if outcome not in VALID:
        raise ValueError(f'{where}: outcome 非法 {outcome!r}，应为 {"/".join(VALID)}')
    route = item.get('route')
    if not route:
        final = item.get('final_url') or item.get('url') or ''
        route = urlsplit(str(final)).path or '/'
    content = item.get('content_len', item.get('content'))
    if not isinstance(content, int) or isinstance(content, bool):
        content = None
    return {'route': str(route), 'outcome': outcome, 'content_len': content}


def read_rows(paths):
    rows = []
    for path in paths:
        with open(path, encoding='utf-8') as f:
            text = f.read()
        if not text.strip():
            continue
        parsed = None
        if text.lstrip().startswith(('{', '[')):
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
        if parsed is not None:
            if isinstance(parsed, dict) and isinstance(parsed.get('rows'), list):
                items = parsed['rows']
            elif isinstance(parsed, list):
                items = parsed
            elif isinstance(parsed, dict) and parsed.get('outcome'):
                items = [parsed]
            else:
                raise ValueError(f'{path}: JSON 里没有 rows 数组')
            for item in items:
                rows.append(normalize(item, path))
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            try:
                item = json.loads(line)
            except ValueError as e:
                raise ValueError(f'{path}:{lineno}: JSON 解析失败: {e}')
            rows.append(normalize(item, path, lineno))
    return rows


def wilson(num, den, z=1.96):
    if den <= 0:
        return [None, None]
    p = num / den
    d = 1 + z * z / den
    centre = p + z * z / (2 * den)
    half = z * math.sqrt(p * (1 - p) / den + z * z / (4 * den * den))
    return [round(max(0.0, (centre - half) / d), 3),
            round(min(1.0, (centre + half) / d), 3)]


def block(rows):
    n = len(rows)
    counts = {o: 0 for o in VALID}
    for r in rows:
        counts[r['outcome']] += 1
    noise = counts['noise']
    valid = n - noise
    ok, degraded = counts['ok'], counts['degraded']
    access_num = ok + degraded
    a_lo, a_hi = wilson(access_num, valid)
    c_lo, c_hi = wilson(ok, access_num)
    return {
        'n': n,
        'noise': noise,
        'missed': valid - access_num,
        'access': {
            'num': access_num, 'den': valid,
            'rate': round(access_num / valid, 3) if valid else None,
            'ci95': [a_lo, a_hi],
        },
        'complete': {
            'num': ok, 'den': access_num,
            'rate': round(ok / access_num, 3) if access_num else None,
            'ci95': [c_lo, c_hi],
        },
        'outcomes': counts,
    }


def summarize(rows):
    groups = {}
    for r in rows:
        groups.setdefault(r['route'], []).append(r)
    return {
        'rows': len(rows),
        'definitions': DEFINITIONS,
        'overall': block(rows),
        'groups': [{'route': k, **block(v)} for k, v in sorted(groups.items())],
    }


def fmt(value, digits=3):
    return 'n/a' if value is None else f'{value:.{digits}f}'


def ci(pair):
    lo, hi = pair
    return 'n/a' if lo is None else f'{lo:.3f}-{hi:.3f}'


def main():
    ap = argparse.ArgumentParser(description='双指标结算：按路由拆分可访问性成功率与内容完整率')
    ap.add_argument('paths', nargs='+', help='JSONL 证据行，或 canary --save 的 JSON')
    ap.add_argument('--json', action='store_true', help='输出单个 JSON 对象')
    args = ap.parse_args()

    try:
        rows = read_rows(args.paths)
    except (OSError, ValueError) as e:
        print(f'stats: {e}', file=sys.stderr)
        return 2

    result = summarize(rows)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print('route\tn\tnoise\tmissed\taccess_n\taccess_rate\taccess_ci\t'
          'complete_rate\tcomplete_ci\toutcomes')
    for g in result['groups'] + [{'route': 'ALL', **result['overall']}]:
        outcomes = ' '.join(f'{k}={v}' for k, v in g['outcomes'].items() if v)
        print(f"{g['route']}\t{g['n']}\t{g['noise']}\t{g['missed']}\t"
              f"{g['access']['num']}\t{fmt(g['access']['rate'])}\t{ci(g['access']['ci95'])}\t"
              f"{fmt(g['complete']['rate'])}\t{ci(g['complete']['ci95'])}\t{outcomes}")
    o = result['overall']
    print('\n== 口径 ==')
    print(f"可访问性成功率 = {DEFINITIONS['accessibility']}")
    print(f"内容完整率     = {DEFINITIONS['completeness']}")
    print(f"噪声 {o['noise']}/{o['n']} 已单列；置信区间为 Wilson 95%，样本越小越宽")
    return 0


if __name__ == '__main__':
    sys.exit(main())
