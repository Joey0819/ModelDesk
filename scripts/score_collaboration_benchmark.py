#!/usr/bin/env python3
"""Score saved paired ModelDesk receipts and downloads offline; never execute model code."""

import argparse
import ast
import hashlib
import json
import re
import statistics
import uuid
from pathlib import Path

from markdown_it import MarkdownIt

from benchmark_collaboration import DEFAULT_OUTPUT, FIXTURE, build_request, select_final

FILLER = re.compile(
    r'值得一提的是|综上所述|总而言之|总的来说|需要指出的是|显而易见|不难看出|尤为重要|'
    r'进一步赋能|有效助力|作为一个(?:AI|人工智能)|以下是(?:该|我|为您|根据)|希望(?:这|以上).*(?:帮助|有所)|'
    r'\bin conclusion\b|\bit is worth noting\b|\bneedless to say\b|\bhopefully this helps\b', re.I)
MARKDOWN = MarkdownIt('commonmark', {'html': False}).enable(['table', 'strikethrough'])


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    return json.loads(path.read_bytes())


def exported_body(html):
    # Compare only actual Markdown content, excluding the title and status added by the exporter.
    match = re.search(r'<main><h1>.*?</h1><p class="meta">.*?</p>(.*)</main>', html, re.S)
    if not match:
        return None
    return re.sub(r'<aside class="notice(?: partial)?">.*?</aside>', '', match[1], flags=re.S).strip()


def table_cells(tokens):
    rows = []
    for index, tok in enumerate(tokens):
        if tok.type == 'tr_open':
            rows.append([])
        if tok.type in {'td_open', 'th_open'}:
            rows[-1].append(tokens[index + 1].content.strip())
    return rows


def cell_matches(case_id, row, col, expected, actual):
    if case_id == 'cohort-table' and (row, col) == (1, 3):
        # Allow an explicit, source-backed scope qualifier in the same metric cell.
        return bool(re.fullmatch(r'0\.82(?:\s*[（(](?:仅限|仅)[^）)\n]*9\s*个[^）)\n]*有效视频[^）)\n]*[）)])?', actual)
                    and (actual == '0.82' or '人工审阅' in actual))
    if case_id == 'asset-inventory' and row > 0 and col == 2:
        return bool(re.search(expected, actual))
    return expected == actual


class OutOfScope(ValueError):
    pass


ALLOWED_CODE_NODES = {ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.If, ast.Assign,
                      ast.Return, ast.Name, ast.Load, ast.Store, ast.Constant, ast.Call,
                      ast.Attribute, ast.Compare, ast.Is, ast.IsNot, ast.In, ast.NotIn, ast.BoolOp,
                      ast.And, ast.Or, ast.Tuple, ast.UnaryOp, ast.Not}


def expression(node, env):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name) and node.id in env:
        return env[node.id]
    if isinstance(node, ast.Tuple):
        return tuple(expression(value, env) for value in node.elts)
    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        a, b = expression(node.left, env), expression(node.comparators[0], env)
        op = node.ops[0]
        if isinstance(op, ast.Is):
            return a is b
        if isinstance(op, ast.IsNot):
            return a is not b
        if isinstance(op, ast.In):
            return a in b
        if isinstance(op, ast.NotIn):
            return a not in b
    if isinstance(node, ast.BoolOp):
        for value in node.values:
            result = expression(value, env)
            if bool(result) != isinstance(node.op, ast.And):
                break
        return result
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not expression(node.operand, env)
    if isinstance(node, ast.Call) and len(node.args) == 1 and not node.keywords:
        value = expression(node.args[0], env)
        if isinstance(node.func, ast.Name) and node.func.id == 'type':
            return type(value)
        if (isinstance(node.func, ast.Attribute) and node.func.attr == 'get' and
                isinstance(node.func.value, ast.Name) and node.func.value.id == 'record' and
                type(env['record']) is dict and isinstance(value, str)):
            return env['record'].get(value)
    raise OutOfScope('expression is outside the supported pure-function subset')


NO_RETURN = object()


def statements(nodes, env):
    for node in nodes:
        if isinstance(node, ast.Return):
            return expression(node.value, env) if node.value is not None else None
        if isinstance(node, ast.If):
            result = statements(node.body if expression(node.test, env) else node.orelse, env)
            if result is not NO_RETURN:
                return result
        elif (isinstance(node, ast.Assign) and len(node.targets) == 1 and
              isinstance(node.targets[0], ast.Name) and node.targets[0].id not in {'record', 'int', 'float', 'type'}):
            env[node.targets[0].id] = expression(node.value, env)
        else:
            raise OutOfScope('statement is outside the supported pure-function subset')
    return NO_RETURN


def code_checks(source, target):
    checks = {
        'valid-zero': ({'reviewed': True, 'score': 0}, 0),
        'valid-negative': ({'reviewed': True, 'score': -2}, -2),
        'valid-fraction': ({'reviewed': True, 'score': 0.5}, 0.5),
        'reviewed-false': ({'reviewed': False, 'score': 2}, None),
        'reviewed-int': ({'reviewed': 1, 'score': 2}, None),
        'reviewed-missing': ({'score': 2}, None),
        'score-bool': ({'reviewed': True, 'score': True}, None),
        'score-str': ({'reviewed': True, 'score': '2'}, None),
        'score-none': ({'reviewed': True, 'score': None}, None),
        'score-missing': ({'reviewed': True}, None),
    }
    try:
        if len(source) > 1500:
            raise OutOfScope('source too long')
        tree = ast.parse(source)
        nodes = list(ast.walk(tree))
        if len(nodes) > 120 or any(type(node) not in ALLOWED_CODE_NODES for node in nodes):
            raise OutOfScope('unsupported AST node')
        if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
            raise OutOfScope('expected one function')
        function = tree.body[0]
        args = function.args
        if (function.name != target or function.decorator_list or function.returns or
                [arg.arg for arg in args.args] != ['record'] or args.args[0].annotation or
                args.posonlyargs or args.vararg or args.kwonlyargs or args.kwarg or args.defaults):
            raise OutOfScope('unexpected function signature')
    except (SyntaxError, OutOfScope):
        return {'code-structure': False}
    outcomes = {'code-structure': True}
    for name, (record, expected) in checks.items():
        try:
            result = statements(function.body, {'record': record, 'int': int, 'float': float})
            if result is NO_RETURN:
                result = None
            outcomes['code-' + name] = type(result) is type(expected) and result == expected
        except (OutOfScope, TypeError):
            outcomes['code-' + name] = False
    return outcomes


def content_checks(case, text):
    tokens = MARKDOWN.parse(text)
    # Ignore whitespace in the length budget, but count Markdown punctuation and citation markers.
    checks = {'nonempty': bool(text.strip()), 'length': len(re.sub(r'\s', '', text)) <= case['max_chars'],
              'no-stock-filler': not FILLER.search(text),
              'no-forbidden-claim': not any(re.search(pattern, text, re.S | re.I) for pattern in case['forbidden'])}
    case_id = case['id']
    for name, patterns in case['checks'].items():
        if case_id == 'qbr-draft' and name == 'status-ask':
            patterns = [r'未复现|尚未.*复现|尚无(?:故障)?复现', *patterns[1:]]
        checks[name] = all(re.search(pattern, text, re.S | re.I) for pattern in patterns)
    if 'table' in case:
        observed, expected = table_cells(tokens), case['table']
        checks['one-table'] = (
            bool(tokens) and tokens[0].type == 'table_open' and tokens[-1].type == 'table_close'
            and sum(tok.type == 'table_open' for tok in tokens) == 1)
        checks['table-cells'] = (len(observed) == len(expected) and all(len(a) == len(b) for a, b in zip(observed, expected))
                                 and all(cell_matches(case_id, row, col, value, observed[row][col])
                                         for row, cells in enumerate(expected) for col, value in enumerate(cells)))
    if 'list_count' in case:
        checks['list-format'] = (bool(tokens) and tokens[0].type == 'bullet_list_open' and
                                 tokens[-1].type == 'bullet_list_close' and
                                 sum(tok.type == 'list_item_open' for tok in tokens) == case['list_count'] and
                                 sum(tok.type == 'bullet_list_open' for tok in tokens) == 1)
    if case_id == 'closed-loop':
        items = [tok.content.strip('* ').split('：')[0].strip('* ') for tok in tokens if tok.type == 'inline']
        checks['list-labels'] = items == ['输入与输出', '已验证', '未验证']
    if case_id == 'gait-unit':
        checks['single-paragraph'] = [tok.type for tok in tokens] == ['paragraph_open', 'inline', 'paragraph_close']
    if case_id == 'cash-coverage':
        checks['two-sentences'] = len(re.findall('[。！？]+', text)) == 2
    if 'code' in case:
        checks['single-python-fence'] = len(tokens) == 1 and tokens[0].type == 'fence' and tokens[0].info == 'python'
        if checks['single-python-fence']:
            checks.update(code_checks(tokens[0].content, case['code']))
    return checks


def score_arm(folder, case, arm, state, manifest, fixture):
    if not state.get('finished_at'):
        return {'passed': False, 'checks': {'terminal-receipt': False}, 'failed': ['terminal-receipt']}
    source = folder / case['id'] / arm
    job = read_json(source / 'job.json')
    final_id, final_text, _, turns = select_final(job, arm, manifest['final_model'])
    text = (source / 'final.md').read_text(encoding='utf-8')
    request = build_request(arm, case, fixture, manifest, state)
    original_request = read_json(source / 'request.json')
    result = job.get('result') or {}
    checks = {'terminal-receipt': job['id'] == state['job_id'] and job['conversation_id'] == state['conversation_id']
              and job['status'] == state['job_status'] == 'complete' and not job.get('error'),
              'paired-input-scope': original_request == request and all(job.get(k) == v for k, v in request.items())
              and (job.get('context_history') or {}).get('used_turns') == 0
              and all(job.get(key) == [] for key in ('knowledge_ids', 'memory_ids', 'attachment_ids', 'mcp_server_ids'))
              and all(job.get(key) is None for key in ('project_id', 'snapshot_id')) and job.get('remember') is False,
              'model-turns': [turn.get('model') for turn in turns] == request['models'] and not state['response_issues']
              and not any(turn.get('error') or turn.get('incomplete') for turn in turns)
              and not result.get('partial_failure'),
              'final-artifact-match': final_id == state['artifact_id'] and final_text == text}
    body_ok = False
    if state['export']['status'] == 'downloaded' and (source / 'final.html').is_file():
        data = (source / 'final.html').read_bytes()
        body_ok = (len(data) == state['export']['bytes'] and digest(data) == state['export']['sha256'] and
                   exported_body(data.decode('utf-8')) == MARKDOWN.render(text).strip())
    checks['verified-html'] = body_ok
    checks.update(content_checks(case, text))
    return {'passed': all(checks.values()), 'checks': checks, 'failed': [k for k, ok in checks.items() if not ok],
            'elapsed_s': state['job_elapsed_s'], 'recorded_turns': state['recorded_turns'],
            'export_bytes': state['export'].get('bytes')}


def score(folder: Path):
    manifest = read_json(folder / 'manifest.json')
    fixture = FIXTURE.read_bytes()
    if manifest['fixture_sha256'] != digest(fixture):
        raise ValueError('Fixture does not match this run')
    fixture = json.loads(fixture)
    conversation_ids = [state['conversation_id'] for state in manifest['runs'].values()]
    if len(conversation_ids) != len(set(conversation_ids)):
        raise ValueError('Benchmark arms do not have independent conversation IDs')
    arms = ('single',) if manifest.get('single_only') else ('single', 'group')
    pairs = [{'id': case['id'], 'name': case['name'], **{
        arm: score_arm(folder, case, arm, manifest['runs'][f'{case["id"]}/{arm}'], manifest, fixture)
        for arm in arms}} for case in fixture['cases']]
    results = {'run_id': manifest['run_id'], 'fixture_sha256': manifest['fixture_sha256'],
               'provider_billed_cost': manifest['provider_billed_cost'],
               'provider_token_usage': manifest['provider_token_usage'], 'pairs': pairs}
    for arm in arms:
        records = [pair[arm] for pair in pairs]
        times = [record['elapsed_s'] for record in records if record['checks']['terminal-receipt']]
        results[arm] = {'passed': sum(record['passed'] for record in records),
                        'verified_html': sum(record['checks'].get('verified-html', False) for record in records),
                        'complete': len(times), 'recorded_turns': sum(r.get('recorded_turns', 0) for r in records),
                        'median_elapsed_s': round(statistics.median(times), 3) if times else None}
    if 'group' not in arms:
        return results
    deltas = [p['group']['elapsed_s'] - p['single']['elapsed_s'] for p in pairs if
              p['group']['checks']['terminal-receipt'] and p['single']['checks']['terminal-receipt']]
    results['paired'] = {'single_only': sum(p['single']['passed'] and not p['group']['passed'] for p in pairs),
                         'group_only': sum(p['group']['passed'] and not p['single']['passed'] for p in pairs),
                         'both': sum(p['group']['passed'] and p['single']['passed'] for p in pairs),
                         'neither': sum(not p['group']['passed'] and not p['single']['passed'] for p in pairs),
                         'group_slower': sum(x > 0 for x in deltas),
                         'median_group_minus_single_s': round(statistics.median(deltas), 3) if deltas else None}
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    print(json.dumps(score(DEFAULT_OUTPUT / str(uuid.UUID(args.run_id))), indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
