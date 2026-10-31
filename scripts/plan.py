#!/usr/bin/env python3
"""plan.py — 档案 lint + 执行计划：把「卡在哪扇门」变成「下一步做什么」。

诊断的终点不是一篇报告，而是执行侧能照做的计划。本脚本读 profile.yaml：
  1. lint：结构、证据绑定、null/0、疑似站点常量、过期时间；
  2. 推导：按最早失败的闸门映射到 S0–S7，给出逐步动作、验证判据与停止条件。

它不联网、不改档案，也不替你做决定：`plan.level` 与推导结果不一致时只告警。

用法:
    python3 scripts/plan.py --profile recon/forum/profile.yaml
    python3 scripts/plan.py recon/forum/profile.yaml --json

退出码: 0 = 无 ERROR（WARN 允许）；1 = 有 ERROR；2 = 用法错误
"""

import argparse
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from report import load_profile  # noqa: E402

VALID_CONF = {'observed', 'mechanism', 'efficacy'}
VALID_STATUS = {'pass', 'fail', 'assumed'}
VALID_ORTH = {'yes', 'no', 'unknown'}
VALID_DIM = {'egress', 'client_stack', 'request_semantics', 'runtime', 'credential', 'account'}
LAYER = re.compile(r'^L[1-7](\.5)?$')
LEVEL = re.compile(r'^S[0-7]$')
DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
SECRETISH = re.compile(r'\b[0-9a-f]{32,}\b', re.I)
COOKIE_PAIR = re.compile(r'^[A-Za-z0-9_.\-]{2,}=')

RUNG = {
    'L1': ('—', '先修链路（DNS/代理/TLS 连通），不进对策阶梯'),
    'L2': ('S2', '换客户端栈：匹配目标放行的 TLS/H2 指纹'),
    'L3': ('S1', '修请求语义：Header 自洽、导航改 XHR、凭证通道'),
    'L4': ('S3', '运行时卫生：先删自己的 CDP 痕迹，再谈 stealth'),
    'L4.5': ('S6', '行为拟人：仅在已有行为模型证据时使用；脆弱且收益递减'),
    'L5': ('S7', '合法凭证：先试 S0 换数据源/短链，再走授权'),
    'L6': ('按维度', '服务端策略：egress→S4 / runtime→S3 / client_stack→S2'),
    'L7': ('—', '补样本：多 pass、区间与噪声分离，先别改对策'),
}
DIM_RUNG = {'egress': 'S4', 'runtime': 'S3', 'client_stack': 'S2',
            'request_semantics': 'S1', 'credential': 'S7'}


def layer_key(layer):
    m = re.match(r'L(\d)(?:\.(\d))?', str(layer or ''))
    return (int(m.group(1)), int(m.group(2) or 0)) if m else (99, 99)


def lint(profile):
    errors, warns = [], []

    def err(path, message):
        errors.append({'path': path, 'message': message})

    def warn(path, message):
        warns.append({'path': path, 'message': message})

    for key in ('host', 'last_verified', 'confidence', 'triage', 'routes',
                'fetcher', 'network', 'gates', 'metrics', 'plan'):
        if key not in profile:
            err(key, '缺少必填段')

    if not str(profile.get('host') or '').strip():
        err('host', '缺少 host')

    conf = profile.get('confidence')
    if conf not in VALID_CONF:
        err('confidence', f'应为 {"/".join(sorted(VALID_CONF))}，实际 {conf!r}')

    verified = str(profile.get('last_verified') or '')
    if not DATE.match(verified):
        err('last_verified', '应为 YYYY-MM-DD')
    else:
        try:
            age = (datetime.date.today() - datetime.date.fromisoformat(verified)).days
            if age < 0:
                warn('last_verified', '日期在未来')
            elif age > 30:
                warn('last_verified', f'已 {age} 天未复验（>30 天视为待复验）')
        except ValueError:
            err('last_verified', '不是合法日期')

    if not str(profile.get('conclusion') or '').strip():
        warn('conclusion', '结论为空，报告摘要会缺失')

    triage = profile.get('triage')
    if not isinstance(triage, dict):
        err('triage', '应为映射（A/B/C/noise）')
    elif all(triage.get(k) is None for k in ('A', 'B', 'C', 'noise')):
        warn('triage', '三态分诊未记录，无法判断样本是否被噪声污染')

    routes = profile.get('routes')
    if not isinstance(routes, list) or not routes:
        err('routes', '至少要有 1 条路由')
    else:
        for i, r in enumerate(routes):
            if not isinstance(r, dict):
                err(f'routes[{i}]', '不是映射')
                continue
            pat = str(r.get('pattern') or '')
            if not pat:
                err(f'routes[{i}].pattern', '缺少 pattern')
            else:
                try:
                    re.compile(pat)
                except re.error as e:
                    err(f'routes[{i}].pattern', f'不是合法正则: {e}')
            if not str(r.get('content_selector') or '').strip():
                warn(f'routes[{i}].content_selector', '缺失，内容完整率不可测')
            mcl = r.get('min_content_len')
            if not isinstance(mcl, int) or isinstance(mcl, bool) or mcl <= 0:
                warn(f'routes[{i}].min_content_len', '应为正整数（用真实浏览器基线取分布）')

    fetcher = profile.get('fetcher')
    if not isinstance(fetcher, dict):
        err('fetcher', '应为映射')
    elif fetcher.get('mode') not in ('http-document', 'browser'):
        warn('fetcher.mode', '应为 http-document | browser')

    network = profile.get('network')
    if not isinstance(network, dict):
        err('network', '应为映射')
    else:
        if network.get('proxy') not in ('none', 'required', 'sticky-per-ip'):
            warn('network.proxy', '应为 none | required | sticky-per-ip')
        if not str(network.get('rate_limit') or '').strip():
            warn('network.rate_limit', '未写节流要求，执行侧会按默认并发打')

    gates = profile.get('gates')
    if not isinstance(gates, list) or not gates:
        err('gates', '至少要有 1 扇门')
    else:
        for i, g in enumerate(gates):
            if not isinstance(g, dict):
                err(f'gates[{i}]', '不是映射')
                continue
            layer = str(g.get('layer') or '')
            if not LAYER.match(layer):
                err(f'gates[{i}].layer', f'应为 L1..L7（可含 L4.5），实际 {layer!r}')
            status = str(g.get('status') or '')
            if status not in VALID_STATUS:
                err(f'gates[{i}].status', f'应为 {"/".join(sorted(VALID_STATUS))}')
            if status in ('pass', 'fail') and not str(g.get('evidence') or '').strip():
                err(f'gates[{i}].evidence', f'status={status} 必须给证据')
            orth = g.get('orthogonal')
            if isinstance(orth, bool):
                orth = 'yes' if orth else 'no'  # YAML 会把未加引号的 yes/no 读成布尔
            if orth not in VALID_ORTH:
                warn(f'gates[{i}].orthogonal', '应为 yes | no | unknown')
            if not str(g.get('name') or '').strip():
                warn(f'gates[{i}].name', '未命名，报告与回归无法引用这扇门')
            dim = str(g.get('dimension') or '')
            if dim and dim not in VALID_DIM:
                warn(f'gates[{i}].dimension', f'未知维度 {dim!r}')

    metrics = profile.get('metrics')
    if not isinstance(metrics, dict):
        err('metrics', '应为映射（P1 未测时子项写 null）')
    else:
        for key in ('baseline', 'after'):
            block = metrics.get(key)
            if block is None:
                continue
            if not isinstance(block, dict):
                err(f'metrics.{key}', '应为映射或 null')
                continue
            for pref in ('accessibility', 'completeness'):
                rate = block.get(f'{pref}_rate')
                sample = block.get(f'{pref}_sample')
                if rate is not None:
                    if (isinstance(rate, bool) or not isinstance(rate, (int, float))
                            or not 0 <= rate <= 1):
                        err(f'metrics.{key}.{pref}_rate', '应为 0..1 的小数')
                    elif sample is None:
                        warn(f'metrics.{key}.{pref}_sample', '有比率没有样本量，无法判断置信度')
                if sample is not None and (isinstance(sample, bool)
                                           or not isinstance(sample, int) or sample < 0):
                    err(f'metrics.{key}.{pref}_sample', '应为非负整数')
            bs, cs = block.get('accessibility_sample'), block.get('completeness_sample')
            if isinstance(bs, int) and isinstance(cs, int) and cs > bs:
                err(f'metrics.{key}.completeness_sample', '完整率分母不能大于可访问性样本量')
        if metrics.get('baseline') is None:
            warn('metrics.baseline', '未测改前基线，改动后的提升不可归因')

    plan = profile.get('plan')
    if not isinstance(plan, dict):
        err('plan', '应为映射')
    else:
        level = str(plan.get('level') or '')
        if level and not LEVEL.match(level):
            err('plan.level', f'应为 S0..S7，实际 {level!r}')
        if not level:
            warn('plan.level', '尚未选对策等级')
        blast = str(plan.get('blast_radius') or '')
        if blast and blast not in ('站点级', '全局'):
            err('plan.blast_radius', '应为 站点级 | 全局')

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f'{path}.{k}' if path else str(k))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f'{path}[{i}]')
        elif isinstance(node, str):
            if SECRETISH.search(node):
                warn(path, '疑似站点常量/hash，不要写进档案或仓库')
            if re.search(r'(?:^|\.)cookies?(?:\[|$)', path) and COOKIE_PAIR.match(node.strip()):
                err(path, '疑似 Cookie 名/值；只写用途与生命周期')

    walk(profile, '')
    return errors, warns


def route_example(profile):
    routes = profile.get('routes') if isinstance(profile.get('routes'), list) else []
    route = next((r for r in routes if isinstance(r, dict) and r.get('pattern')), None)
    host = str(profile.get('host') or 'example.com')
    if not route:
        return f'https://{host}/', ''
    pat = str(route.get('pattern')).strip()
    path = re.sub(r'[\^$]', '', pat)
    if re.search(r'[()\[\]\\|?*+]', path):
        path = '<实际路径>'
    if not path.startswith('/'):
        path = '/' + path
    selector = str(route.get('content_selector') or '').strip()
    return f'https://{host}{path}', selector


def derive(profile):
    gates = [g for g in (profile.get('gates') or []) if isinstance(g, dict)]
    failed = [g for g in gates if g.get('status') == 'fail']
    assumed = [g for g in gates if g.get('status') == 'assumed']
    pool = failed or assumed
    if not pool:
        return {'mode': 'none', 'rung': '—',
                'why': '没有失败/待证闸门：先补证据矩阵，别急着改对策'}
    gate = min(pool, key=lambda g: layer_key(g.get('layer')))
    layer = str(gate.get('layer') or '')
    rung, why = RUNG.get(layer, ('?', '层未知，回看 layers.md'))
    if layer == 'L6':
        rung = DIM_RUNG.get(str(gate.get('dimension') or ''), '按维度')
    return {
        'mode': 'fail' if failed else 'assume',
        'name': str(gate.get('name') or '未命名'),
        'layer': layer,
        'dimension': str(gate.get('dimension') or ''),
        'status': str(gate.get('status') or ''),
        'evidence': str(gate.get('evidence') or ''),
        'rung': rung,
        'why': why,
    }


def steps_for(profile, blocking, profile_path):
    url, selector = route_example(profile)
    sel = f' --selector "{selector}"' if selector else ''
    matrix = f'recon matrix {url}{sel} --passes 3'
    canary = f'recon canary {url}{sel} --passes 5'
    rung = blocking.get('rung', '—')

    single = {
        'S1': f'recon fetch {url}{sel}',
        'S2': f'recon matrix {url} --clients curl,cffi{sel}',
        'S3': 'scripts/fingerprint-probe.js（页面上下文）+ recon matrix 对照',
        'S4': f'recon canary {url}{sel} --passes 5',
        'S5': '整体对齐 UA/平台/GPU/语言/时区后重跑矩阵（需评审影响面）',
        'S6': '仅在已有行为模型证据时启用；先证明不是前几层的问题',
        'S7': '走合法凭证或 S0 换数据源；不提供绕过命令',
        '按维度': '按 gates[].dimension 选上表对应的 S1–S4 命令',
        '—': '先按上一层修链路/补样本，再决定是否进入对策',
    }.get(rung, '按 countermeasure-ladder.md 选最低成本的一级')

    if blocking.get('mode') == 'none':
        third = {'n': 3, 'action': '补证据：给假设门做单变量实验', 'command': matrix,
                 'verify': '把 assumed 变成 pass/fail 并绑定证据，再按最早失败层选对策'}
    else:
        third = {'n': 3, 'action': f'单变量（{rung}）：{blocking.get("why", "")}',
                 'command': single,
                 'verify': blocking.get('evidence') or '同一批路由、只改这一维，前后对比'}
    return [
        {'n': 1, 'action': '复现对照', 'command': matrix,
         'verify': '对照组拿到内容区基线；对照组也失败就停在 L1，不进入对策'},
        {'n': 2, 'action': '概率门定性', 'command': canary,
         'verify': 'flip_count=0 才算确定性；>0 按 probability-gate 报区间'},
        third,
        {'n': 4, 'action': '回归', 'command': matrix,
         'verify': '所有 status=pass 的门保持 pass；任一翻 fail 即回滚'},
        {'n': 5, 'action': '回写档案', 'command': f'recon report {profile_path}',
         'verify': 'gates 与 metrics.after 和证据表一致，再 bump last_verified'},
    ]


def stop_conditions(profile, blocking):
    out = []
    metrics = profile.get('metrics') if isinstance(profile.get('metrics'), dict) else {}
    if metrics.get('baseline') is None:
        out.append('没有改前基线：先跑一组基线，否则改后的提升不可归因')
    gates = [g for g in (profile.get('gates') or []) if isinstance(g, dict)]
    if any(g.get('orthogonal') == 'unknown' for g in gates):
        out.append('有正交性未知的闸门：单变量实验可能看起来无效，先确认另一扇门是否仍堵')
    issues = ' '.join(str(x) for x in (profile.get('known_issues') or []))
    if re.search(r'概率|翻转|翻转率|nondetermin|flip', issues, re.I):
        out.append('已知概率门：改动后至少 3 pass，报 min/median/max 与翻转次数')
    if not any(str((r or {}).get('content_selector') or '').strip()
               for r in (profile.get('routes') or []) if isinstance(r, dict)):
        out.append('没有 content_selector：完整率不可测，先补选择器与基线')
    if blocking.get('mode') == 'none':
        out.append('没有已证失败的门：先补假设门的实验，不要凭空加对策')
    return out


def main():
    ap = argparse.ArgumentParser(description='档案 lint + 执行计划')
    ap.add_argument('--profile', required=True, help='recon/<代号>/profile.yaml')
    ap.add_argument('--json', action='store_true', help='输出单个 JSON 对象')
    args = ap.parse_args()

    if not os.path.exists(args.profile):
        ap.error(f'找不到档案文件: {args.profile}')

    profile = load_profile(args.profile)
    if not isinstance(profile, dict):
        ap.error(f'{args.profile} 顶层不是映射')

    errors, warns = lint(profile)
    blocking = derive(profile)
    plan = profile.get('plan') if isinstance(profile.get('plan'), dict) else {}
    declared = str(plan.get('level') or '')
    consistent = None
    if declared and blocking.get('rung', '').startswith('S'):
        consistent = declared == blocking['rung']
        if not consistent:
            warns.append({'path': 'plan.level',
                          'message': f'档案写 {declared}，但按最早失败层推导应先做 '
                                     f'{blocking["rung"]}（更高成本的一级要等低级失效）'})

    steps = steps_for(profile, blocking, args.profile)
    stops = stop_conditions(profile, blocking)
    result = {
        'profile': args.profile,
        'ok': not errors,
        'lint': {'errors': errors, 'warnings': warns},
        'blocking': blocking,
        'declared_plan': declared or None,
        'consistent': consistent,
        'steps': steps,
        'stop_conditions': stops,
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if errors else 0

    print(f'profile: {args.profile}')
    print('\n== Lint ==')
    for e in errors:
        print(f'  ERROR {e["path"]}: {e["message"]}')
    for w in warns:
        print(f'  WARN  {w["path"]}: {w["message"]}')
    if not errors and not warns:
        print('  无问题')

    print('\n== 执行计划 ==')
    if blocking.get('mode') == 'none':
        print(f'  阻塞闸门: 无（{blocking["why"]}）')
    else:
        print(f'  阻塞闸门: {blocking.get("name")} '
              f'({blocking.get("layer")}, {blocking.get("status")}) → {blocking.get("rung")}')
        print(f'  理由: {blocking.get("why")}')
    for s in steps:
        print(f'\n  {s["n"]}. {s["action"]}')
        if s['command']:
            print(f'     $ {s["command"]}')
        print(f'     验证: {s["verify"]}')

    if stops:
        print('\n== 停止条件 ==')
        for s in stops:
            print(f'  - {s}')

    print(f'\n结论: {"通过" if not errors else str(len(errors)) + " 个 ERROR 需先修"}'
          f'（{len(warns)} 条提示）')
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
