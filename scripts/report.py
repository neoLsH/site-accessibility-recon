#!/usr/bin/env python3
"""report.py — 从站点档案与证据表生成 Markdown 报告。

用法:
    python3 scripts/report.py --profile sites/example.com/profile.yaml
    python3 scripts/report.py --profile sites/example.com/profile.yaml \
        --evidence sites/example.com/evidence.csv --out sites/example.com/report.md

说明:
    - 只渲染档案里已有的结论；缺字段写「未记录」，不编造数字。
    - YAML 首选 PyYAML；未安装时使用内置的受限解析（只覆盖模板用到的语法），
      遇到不支持的写法会报错并提示安装 pyyaml，而不是静默给出错误结果。
"""

import argparse
import csv
import os
import re
import sys


class YamlSubsetError(Exception):
    pass


def _strip_comment(line):
    out = []
    quote = None
    for i, ch in enumerate(line):
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ('"', "'"):
            quote = ch
            out.append(ch)
            continue
        if ch == '#' and (i == 0 or line[i - 1] in ' \t'):
            break
        out.append(ch)
    return ''.join(out).rstrip()


def _unquote(s):
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s


def _scalar(s):
    s = s.strip()
    if s == '':
        return None
    if s[0] in ('"', "'") and s[-1:] == s[0] and len(s) >= 2:
        return s[1:-1]
    low = s.lower()
    # 只认 YAML 的 null 字面量；none / no 是普通字符串（proxy: none 会用到）
    if low in ('null', '~'):
        return None
    if low == 'true':
        return True
    if low == 'false':
        return False
    if s == '[]':
        return []
    if s == '{}':
        return {}
    if re.fullmatch(r'-?\d+', s):
        return int(s)
    if re.fullmatch(r'-?\d+\.\d+', s):
        return float(s)
    if s.startswith('[') and s.endswith(']'):
        inner = s[1:-1].strip()
        return [_scalar(x) for x in _split_flow(inner)] if inner else []
    if s.startswith('{') and s.endswith('}'):
        inner = s[1:-1].strip()
        out = {}
        if inner:
            for part in _split_flow(inner):
                k, _, v = part.partition(':')
                out[_unquote(k)] = _scalar(v)
        return out
    if s in ('|', '>', '|-', '>-'):
        raise YamlSubsetError('不支持块标量（| / >），请改用普通字符串')
    return s


def _split_flow(s):
    """按逗号切分流式列表/映射，忽略括号与引号内的逗号。"""
    parts, buf, depth, quote = [], [], 0, None
    for ch in s:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ('"', "'"):
            quote = ch
            buf.append(ch)
            continue
        if ch in '[{':
            depth += 1
        elif ch in ']}':
            depth -= 1
        if ch == ',' and depth == 0:
            parts.append(''.join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if ''.join(buf).strip():
        parts.append(''.join(buf).strip())
    return parts


def _parse_map(lines, i, ind):
    out = {}
    while i < len(lines) and lines[i][0] == ind:
        content = lines[i][1]
        if content.startswith('- '):
            break
        key, sep, rest = content.partition(':')
        if not sep:
            raise YamlSubsetError(f'第 {lines[i][2]} 行无法解析为 key: value')
        key = _unquote(key)
        rest = rest.strip()
        if rest:
            out[key] = _scalar(rest)
            i += 1
        elif i + 1 < len(lines) and lines[i + 1][0] > ind:
            out[key], i = _parse(lines, i + 1, lines[i + 1][0])
        elif i + 1 < len(lines) and lines[i + 1][0] == ind and lines[i + 1][1].startswith('- '):
            out[key], i = _parse_seq(lines, i + 1, ind)
        else:
            out[key] = None
            i += 1
    return out, i


def _parse_seq(lines, i, ind):
    out = []
    while i < len(lines) and lines[i][0] == ind and lines[i][1].startswith('- '):
        item = lines[i][1][2:].strip()
        if item == '':
            out.append(_parse(lines, i + 1, lines[i + 1][0])[0] if i + 1 < len(lines) else None)
            i += 1
        elif ':' in item and not item.startswith(('"', "'", '[', '{')):
            lines[i] = (ind + 2, item, lines[i][2])
            val, i = _parse_map(lines, i, ind + 2)
            out.append(val)
        else:
            out.append(_scalar(item))
            i += 1
    return out, i


def _parse(lines, i, ind):
    if i >= len(lines):
        return None, i
    if lines[i][1].startswith('- '):
        return _parse_seq(lines, i, ind)
    return _parse_map(lines, i, ind)


def parse_yaml_subset(text):
    lines = []
    for no, raw in enumerate(text.splitlines(), 1):
        if '\t' in raw[: len(raw) - len(raw.lstrip())]:
            raise YamlSubsetError(f'第 {no} 行用了 Tab 缩进，请改为空格')
        line = _strip_comment(raw)
        if not line.strip() or line.strip() == '---':
            continue
        indent = len(line) - len(line.lstrip(' '))
        lines.append((indent, line.strip(), no))
    if not lines:
        return {}
    value, _ = _parse(lines, 0, lines[0][0])
    return value if value is not None else {}


def load_profile(path):
    with open(path, encoding='utf-8') as f:
        text = f.read()
    try:
        import yaml  # type: ignore
    except ImportError:
        try:
            return parse_yaml_subset(text)
        except YamlSubsetError as e:
            sys.exit(
                f'{path}: 内置 YAML 受限解析失败（{e}）。'
                '请安装 pyyaml（python3 -m pip install --user pyyaml）后重试。'
            )
    return yaml.safe_load(text) or {}


def cell(value):
    if value is None or value == '':
        return '未记录'
    return str(value).replace('|', '\\|').replace('\n', ' ')


def inline(value):
    return ' '.join(str(value).split())


def rate(value):
    if isinstance(value, bool) or value is None:
        return '未记录'
    if isinstance(value, (int, float)):
        return f'{value * 100:.1f}%'
    return str(value)


def section_header(p):
    routes = p.get('routes') or []
    patterns = ', '.join(r.get('pattern', '?') for r in routes if isinstance(r, dict)) or '未记录'
    m = p.get('metrics') or {}
    base = (m.get('baseline') or {}) if isinstance(m, dict) else {}
    sample = base.get('accessibility_sample') if isinstance(base, dict) else None
    passes = base.get('pass_count') if isinstance(base, dict) else None
    scope = f'{patterns}，样本 {sample if sample is not None else "未记录"}，pass {passes if passes is not None else "未记录"}'
    return [
        f'# {p.get("host", "未记录")} 可访问性排查报告',
        '',
        f'- 日期：{p.get("last_verified", "未记录")}',
        f'- 范围：{scope}',
        f'- 结论置信度：{p.get("confidence", "未记录")}',
        '',
    ]


def section_summary(p):
    conclusion = p.get('conclusion')
    gates = p.get('gates') or []
    failed = [g.get('name') for g in gates if isinstance(g, dict) and g.get('status') == 'fail' and g.get('name')]
    assumed = [g.get('name') for g in gates if isinstance(g, dict) and g.get('status') == 'assumed' and g.get('name')]
    plan = p.get('plan') or {}
    out = ['## 1. 结论摘要', '']
    if conclusion:
        out.append(f'- 结论：{inline(conclusion)}')
    out.append(f'- 卡在哪扇门：{cell("、".join(failed)) if failed else "无已证失败的门"}')
    if assumed:
        out.append(f'- 仍为假设：{"、".join(assumed)}')
    level = plan.get('level') if isinstance(plan, dict) else None
    reason = plan.get('reason') if isinstance(plan, dict) else None
    out.append(f'- 最低成本方案：{cell(level)}{"（" + inline(reason) + "）" if reason else ""}')
    out.append('')
    return out


def section_triage(p):
    tri = p.get('triage') or {}
    names = [('A', '链路失败'), ('B', '反爬拦截'), ('C', '内容业务状态'), ('noise', '基础设施噪声')]
    out = ['## 2. 三态分诊', '', '| 态 | 样本数 | 判据 |', '|---|---|---|']
    if not tri:
        out.append('| 合计 | 未记录 | 未提供 triage 字段 |')
    else:
        for key, label in names:
            out.append(f'| {label} | {cell(tri.get(key))} | 见 evidence.csv 的 http / marker 列 |')
    out.append('')
    return out


def section_gates(p):
    gates = p.get('gates') or []
    out = ['## 3. 闸门图', '', '| 闸门 | 层 | 维度 | 正交性 | 状态 | 证据 |', '|---|---|---|---|---|---|']
    if not gates:
        out.append('| 未记录 | - | - | - | - | - |')
    for g in gates:
        if not isinstance(g, dict):
            continue
        out.append(
            '| {} | {} | {} | {} | {} | {} |'.format(
                cell(g.get('name')), cell(g.get('layer')), cell(g.get('dimension')),
                cell(g.get('orthogonal')), cell(g.get('status')), cell(g.get('evidence')),
            )
        )
    out.append('')
    return out


def section_evidence(rows):
    out = ['## 4. 证据矩阵', '',
           '| 客户端 | 身份 | 路由 | 出口 | http | bytes | final_url | 内容区长度 | 备注 |',
           '|---|---|---|---|---|---|---|---|---|']
    if not rows:
        out.append('| 未记录 | - | - | - | - | - | - | - | 未提供 evidence.csv |')
        return out + ['']
    for r in rows:
        out.append(
            '| {} | {} | {} | {} | {} | {} | {} | {} | {} |'.format(
                cell(r.get('client')), cell(r.get('identity')), cell(r.get('route')),
                cell(r.get('egress')), cell(r.get('http')), cell(r.get('bytes')),
                cell(r.get('final_url')), cell(r.get('content_region_len')),
                cell(r.get('marker') or r.get('note')),
            )
        )
    out.append('')
    return out


def section_metrics(p):
    m = p.get('metrics') or {}
    base = (m.get('baseline') or {}) if isinstance(m, dict) else {}
    after = (m.get('after') or {}) if isinstance(m, dict) else {}
    out = ['## 5. 双指标', '', '| 指标 | 改前 | 改后 | 样本 | 区间 |', '|---|---|---|---|---|']

    def line(label, prefix):
        key = f'{prefix}_rate'
        b = base.get(key) if isinstance(base, dict) else None
        a = after.get(key) if isinstance(after, dict) else None
        src = after if isinstance(after, dict) and after.get(key) is not None else base
        sample = src.get(f'{prefix}_sample') if isinstance(src, dict) else None
        rng = src.get(f'{prefix}_range') if isinstance(src, dict) else None
        return f'| {label} | {rate(b)} | {rate(a)} | {cell(sample)} | {cell(rng)} |'

    out.append(line('可访问性成功率', 'accessibility'))
    out.append(line('内容完整率', 'completeness'))
    audit = m.get('inflation_audit') if isinstance(m, dict) else None
    out.append('')
    out.append(f'- 虚高审计：{cell(audit)}')
    out.append('')
    return out


def section_plan(p):
    plan = p.get('plan') or {}
    out = ['## 6. 对策', '']
    if not isinstance(plan, dict) or not plan:
        out.append('- 选定级别与理由：未记录')
        out.append('- 代价：未记录')
        out.append('- 影响面：未记录')
    else:
        out.append(f'- 选定级别与理由：{cell(plan.get("level"))} {inline(cell(plan.get("reason")))}'.rstrip())
        out.append(f'- 代价：{cell(plan.get("cost"))}')
        out.append(f'- 影响面：{cell(plan.get("blast_radius"))}')
    out.append('')
    return out


def section_open(p):
    issues = p.get('known_issues') or []
    out = ['## 7. 未解问题', '']
    if not issues:
        out.append('- 未记录')
    for it in issues:
        out.append(f'- [ ] {inline(it)}')
    out.append('')
    return out


def read_evidence(path):
    if not path:
        return []
    if not os.path.exists(path):
        return []
    with open(path, encoding='utf-8', newline='') as f:
        return list(csv.DictReader(f))


def build_report(profile, rows):
    parts = []
    parts += section_header(profile)
    parts += section_summary(profile)
    parts += section_triage(profile)
    parts += section_gates(profile)
    parts += section_evidence(rows)
    parts += section_metrics(profile)
    parts += section_plan(profile)
    parts += section_open(profile)
    return '\n'.join(parts).rstrip() + '\n'


def main():
    ap = argparse.ArgumentParser(description='从站点档案与证据表生成 Markdown 报告')
    ap.add_argument('--profile', required=True, help='sites/<host>/profile.yaml')
    ap.add_argument('--evidence', help='sites/<host>/evidence.csv（可选）')
    ap.add_argument('--out', help='输出文件；缺省打印到 stdout')
    args = ap.parse_args()

    if not os.path.exists(args.profile):
        sys.exit(f'找不到档案文件: {args.profile}')

    profile = load_profile(args.profile)
    if not isinstance(profile, dict):
        sys.exit(f'{args.profile} 顶层不是映射，无法生成报告')

    evidence = args.evidence
    if not evidence:
        sibling = os.path.join(os.path.dirname(args.profile), 'evidence.csv')
        if os.path.exists(sibling):
            evidence = sibling

    report = build_report(profile, read_evidence(evidence))
    if args.out:
        with open(args.out, 'w', encoding='utf-8') as f:
            f.write(report)
        print(f'已生成 {args.out}')
    else:
        sys.stdout.write(report)


if __name__ == '__main__':
    main()
