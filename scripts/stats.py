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

改前/改后对比：改前的证据行放位置参数，改后的放 --compare。
差值区间用 Newcombe 95%（两个 Wilson 区间合成）；任一侧分母低于 --min-den
（默认 10）时只给区间、不给结论，标 insufficient。

用法:
    python3 scripts/stats.py evidence.jsonl
    python3 scripts/stats.py before.jsonl --compare after.jsonl
    python3 scripts/stats.py before.jsonl --compare after.jsonl --min-den 20 --json

退出码: 0 = 结算完成（对比时无回归）；1 = 对比发现回归；2 = 用法或数据格式错误
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
MIN_DEN = 10


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
            if isinstance(parsed, dict) and parsed.get('kind') == 'trial':
                raise ValueError(f'{path}: 这是试验行（kind: trial），不能当证据行')
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
            if isinstance(item, dict) and item.get('kind') == 'trial':
                raise ValueError(f'{path}:{lineno}: 试验行不能当证据行')
            rows.append(normalize(item, path, lineno))
    return rows


def wilson_bounds(num, den, z=1.96):
    if den <= 0:
        return (None, None)
    p = num / den
    d = 1 + z * z / den
    centre = p + z * z / (2 * den)
    half = z * math.sqrt(p * (1 - p) / den + z * z / (4 * den * den))
    return (max(0.0, (centre - half) / d), min(1.0, (centre + half) / d))


def wilson(num, den, z=1.96):
    lo, hi = wilson_bounds(num, den, z)
    return [None, None] if lo is None else [round(lo, 3), round(hi, 3)]


def newcombe(b_num, b_den, a_num, a_den, z=1.96):
    """两个独立比例之差的 Newcombe hybrid score 区间（法 10），返回未取整的上下界。"""
    p1, p2 = b_num / b_den, a_num / a_den
    l1, u1 = wilson_bounds(b_num, b_den, z)
    l2, u2 = wilson_bounds(a_num, a_den, z)
    d = p2 - p1
    lo = d - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    hi = d + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return (max(-1.0, lo), min(1.0, hi))


def round_pair(bounds):
    lo, hi = bounds
    # + 0.0 把 -0.0 归一成 0.0；判定仍用未取整的界
    return [None, None] if lo is None else [round(lo, 3) + 0.0, round(hi, 3) + 0.0]


def compare_metric(before, after, min_den):
    b_num, b_den = before['num'], before['den']
    a_num, a_den = after['num'], after['den']
    out = {
        'before': {'num': b_num, 'den': b_den, 'rate': before['rate']},
        'after': {'num': a_num, 'den': a_den, 'rate': after['rate']},
        'delta': None, 'ci95': [None, None], 'status': 'insufficient',
    }
    if b_den <= 0 or a_den <= 0:
        out['reason'] = '至少一侧分母为 0（全是噪声或没有可访问样本）'
        return out
    out['delta'] = round(a_num / a_den - b_num / b_den, 3)
    bounds = newcombe(b_num, b_den, a_num, a_den)
    out['ci95'] = round_pair(bounds)
    if b_den < min_den or a_den < min_den:
        out['reason'] = f'分母低于下限 {min_den}（改前 {b_den} / 改后 {a_den}）'
        return out
    # 判定用未取整的界：四舍五入到 0.000 不能当成跨 0
    lo, hi = bounds
    out['status'] = 'improved' if lo > 0 else 'regressed' if hi < 0 else 'inconclusive'
    return out


def is_regressed(row):
    return any(row[k]['status'] == 'regressed' for k in ('access', 'complete'))


def compare_summaries(before, after, min_den):
    b_routes = {g['route']: g for g in before['groups']}
    a_routes = {g['route']: g for g in after['groups']}
    shared = sorted(set(b_routes) & set(a_routes))
    routes = [{
        'route': r,
        'access': compare_metric(b_routes[r]['access'], a_routes[r]['access'], min_den),
        'complete': compare_metric(b_routes[r]['complete'], a_routes[r]['complete'], min_den),
    } for r in shared]
    overall = {
        'access': compare_metric(before['overall']['access'], after['overall']['access'], min_den),
        'complete': compare_metric(before['overall']['complete'], after['overall']['complete'], min_den),
    }
    regressed = [r['route'] for r in routes if is_regressed(r)]
    return {
        'min_den': min_den,
        'overall': overall,
        'routes': routes,
        'only_before': sorted(set(b_routes) - set(a_routes)),
        'only_after': sorted(set(a_routes) - set(b_routes)),
        'regressed_routes': regressed,
        'regressed': bool(regressed) or is_regressed(overall),
    }


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


def ratio(side):
    if side['rate'] is None:
        return f"{side['num']}/{side['den']} n/a"
    return f"{side['num']}/{side['den']} {side['rate']:.3f}"


def signed(value):
    return 'n/a' if value is None else f'{value:+.3f}'


def print_single(result):
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


def print_compare(result):
    cmp = result['compare']
    print('== 改前 -> 改后 ==')
    for key, label in (('access', '可访问性成功率'), ('complete', '内容完整率')):
        print(f'\n{label}')
        print('route\tbefore\tafter\tdelta\tci95\tstatus')
        rows = [('ALL', cmp['overall'])] + [(r['route'], r) for r in cmp['routes']]
        for name, row in rows:
            m = row[key]
            print(f"{name}\t{ratio(m['before'])}\t{ratio(m['after'])}\t"
                  f"{signed(m['delta'])}\t{ci(m['ci95'])}\t{m['status']}")
    notes = []
    for r in cmp['routes']:
        for key in ('access', 'complete'):
            m = r[key]
            if m['status'] == 'insufficient' and m.get('reason'):
                notes.append(f"{r['route']} {key}: {m['reason']}")
    if cmp['only_before'] or cmp['only_after']:
        notes.append('只在一侧出现的路由：改前 ' + (', '.join(cmp['only_before']) or '无')
                     + '；改后 ' + (', '.join(cmp['only_after']) or '无'))
    print('\n== 说明 ==')
    print(f"下限: 每侧分母 >= {cmp['min_den']} 才给结论；低于此值标 insufficient，区间照给")
    print('区间: Newcombe 95%（两个 Wilson 区间合成）；不含 0 才判 improved / regressed')
    for note in notes[:12]:
        print(f'注意: {note}')
    if len(notes) > 12:
        print(f'注意: 另有 {len(notes) - 12} 条（--json 可看全）')
    if cmp['regressed']:
        print('回归: ' + ', '.join(cmp['regressed_routes'] or ['ALL']))
    else:
        print('回归: 无')


def main():
    ap = argparse.ArgumentParser(description='双指标结算：按路由拆分可访问性成功率与内容完整率')
    ap.add_argument('paths', nargs='+', help='改前证据行（不给 --compare 时就是单批结算）')
    ap.add_argument('--compare', metavar='AFTER', help='改后证据行；按改前 -> 改后对比双指标')
    ap.add_argument('--min-den', type=int, default=MIN_DEN,
                    help=f'对比时每侧分母下限，默认 {MIN_DEN}；低于此值只给区间不给结论')
    ap.add_argument('--json', action='store_true', help='输出单个 JSON 对象')
    args = ap.parse_args()
    if args.min_den < 1:
        ap.error('--min-den 必须 >= 1')

    try:
        before = summarize(read_rows(args.paths))
        after = summarize(read_rows([args.compare])) if args.compare else None
    except (OSError, ValueError) as e:
        print(f'stats: {e}', file=sys.stderr)
        return 2

    if after is None:
        result = {'mode': 'single', **before}
    else:
        result = {
            'mode': 'compare',
            'rows': before['rows'] + after['rows'],
            'definitions': DEFINITIONS,
            'baseline': before,
            'current': after,
            'compare': compare_summaries(before, after, args.min_den),
        }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif after is None:
        print_single(result)
    else:
        print_compare(result)

    if after is not None and result['compare']['regressed']:
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
