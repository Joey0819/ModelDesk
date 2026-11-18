#!/usr/bin/env python3
"""Verify and score real automatic team deliveries and their paired single baselines offline."""
import argparse
from collections import Counter
import json
from pathlib import Path
from statistics import median
import uuid

from benchmark_automatic_collaboration import build_auto_request
from benchmark_collaboration import DEFAULT_OUTPUT, FIXTURE, TERMINAL
from score_collaboration_benchmark import MARKDOWN, content_checks, digest, exported_body, read_json, score_arm


def med(values):
    return round(median(values), 3) if values else None


def score_team(folder, case, state, manifest, fixture, *, recovered=False):
    source = folder / case['id'] / ('recovery' if recovered else 'automatic')
    initial = folder / case['id'] / 'automatic'
    receipt = read_json(source / 'job.json')
    task = receipt.get('result') or {}
    delivery = task.get('delivery') or {}
    workflow = delivery.get('workflow') or {}
    members = (receipt.get('group_team_snapshot') or {}).get('members') or []
    text = (source / 'final.md').read_bytes().decode('utf-8')
    txt_sha = digest(text.encode('utf-8'))
    artifact = delivery.get('artifact') or {}
    visible = bool(text.strip())
    checks = {
        'request-match': read_json(initial / 'request.json') == build_auto_request(case, fixture)
                         and receipt.get('prompt') == case['prompt'] and receipt.get('system_prompt') == fixture['system'],
        'terminal-receipt': bool(state.get('finished_at')) and receipt.get('status') in TERMINAL
                            and receipt.get('status') == state.get('job_status'),
        'auto-in-saved-room': receipt['id'] == state['job_id']
                              and receipt.get('mode') == 'group' and receipt.get('group_delivery_mode') == 'auto'
                              and receipt.get('group_harness_version') == 1 and receipt.get('group_context_mode') == 'fresh'
                              and receipt.get('group_room_id') == receipt.get('conversation_id') == state['room_id']
                              and receipt.get('project_id') == manifest['resources']['project_id']
                              and receipt['group_team_snapshot']['id'] == manifest['resources']['team_id'],
        'model-and-roster': receipt.get('group_coordinator_model') == manifest['final_model']
                            and [(m.get('id'), m.get('model'), m.get('after_member_ids') or []) for m in members]
                            == [('author', manifest['final_model'], []), ('reviewer', manifest['reviewer_model'], ['author'])]
                            and all(m.get('kind') == 'model' and m.get('required') is True and not m.get('skill_ids') for m in members),
        'isolated-context': receipt.get('include_project_assets') is False and receipt.get('remember') is False
                            and receipt.get('snapshot_id') is None
                            and (receipt.get('context_history') or {}).get('used_turns') == 0
                            and (receipt.get('project_memory') or {}).get('entries') == []
                            and not any(receipt.get(key) for key in ('tools', 'memory_ids', 'knowledge_ids', 'attachment_ids',
                                                                    'mcp_server_ids', '_skill_snapshots', 'group_document_formats'))
                            and receipt.get('group_document_layout') == 'report' and receipt.get('group_output_format') == 'text',
        'delivery-complete': delivery.get('status') == state.get('delivery_status') == 'complete'
                             and receipt.get('status') == 'complete',
        'final-artifact-match': visible and state.get('artifact_id') == 'group:delivery'
                                and text == delivery.get('content') and artifact.get('id') == receipt['id'] + ':delivery'
                                and txt_sha == artifact.get('content_sha256') == delivery.get('content_sha256'),
    }
    if recovered:
        previous = read_json(initial / 'job.json')
        prior = previous['result']['delivery']['workflow']
        checks['original-job-continued-once'] = (receipt['id'] == previous['id'] and workflow.get('resume_count') == 1
            and prior.get('resume_count') == 0 and workflow.get('revision') == state.get('final_revision')
            and workflow['revision'] > prior['revision'] == state.get('first_revision')
            and read_json(source / 'continue_request.json') == {'expected_revision': prior['revision']})
    data_path = source / 'final.html'
    attachment = state.get('export') or {}
    data = data_path.read_bytes() if data_path.is_file() else b''
    verified = (attachment.get('status') == 'downloaded' and bool(data) and attachment.get('bytes') == len(data)
                and attachment.get('sha256') == digest(data)
                and exported_body(data.decode('utf-8')) == MARKDOWN.render(text).strip())
    checks['verified-html'] = bool(verified)
    rubric = content_checks(case, text) if visible else None
    issues = ' '.join(str(item) for item in (delivery.get('limitations') or []))
    roles = [step.get('member_name') for step in delivery.get('steps', []) if 'HTTP 500' in (step.get('error') or '')]
    return {'passed': all(checks.values()) and rubric is not None and all(rubric.values()), 'checks': checks,
            'rubric_pass': all(rubric.values()) if rubric is not None else None, 'rubric': rubric,
            'elapsed_s': state.get('continuation_elapsed_s' if recovered else 'job_elapsed_s'),
            'job_status': receipt.get('status'), 'delivery_status': delivery.get('status'), 'nonempty': visible,
            'recorded_turns': len(task.get('messages') or []), 'http_500_roles': roles,
            'envelope_title_error': 'FinalDelivery' in issues and '\ntitle' in issues}


def summarize(arms):
    times = [arm['elapsed_s'] for arm in arms if arm['checks']['terminal-receipt'] and arm['elapsed_s'] is not None]
    return {'jobs_terminal': sum(arm['checks']['terminal-receipt'] for arm in arms),
            'deliveries': dict(Counter(arm['delivery_status'] or 'unknown' for arm in arms)),
            'nonempty': sum(arm['nonempty'] for arm in arms), 'verified_html': sum(arm['checks']['verified-html'] for arm in arms),
            'rubric_pass': sum(arm['rubric_pass'] is True for arm in arms), 'passed': sum(arm['passed'] for arm in arms),
            'recorded_turns': sum(arm['recorded_turns'] for arm in arms), 'median_elapsed_s': med(times),
            'role_http_500_cases': sum(bool(arm['http_500_roles']) for arm in arms),
            'role_http_500_by_role': dict(Counter(role for arm in arms for role in arm['http_500_roles'])),
            'envelope_title_errors': sum(arm['envelope_title_error'] for arm in arms)}


def score(folder: Path, *, with_recovery=False):
    first = folder / 'automatic.json'
    manifest = read_json(first)
    baseline_folder = folder.parent / str(uuid.UUID(manifest['baseline_run_id']))
    baseline = read_json(baseline_folder / 'manifest.json')
    fixture_bytes = FIXTURE.read_bytes()
    if (manifest['fixture_sha256'] != digest(fixture_bytes) or baseline.get('run_id') != manifest['baseline_run_id']
            or any(manifest[key] != baseline[key] for key in ('base_url', 'fixture_sha256', 'final_model', 'reviewer_model'))):
        raise ValueError('Fixture, baseline identity or environment mismatch')
    fixture = json.loads(fixture_bytes)
    specs = [case['id'] for case in fixture['cases']]
    conversations = [baseline['runs'][f'{case_id}/single']['conversation_id'] for case_id in specs]
    rooms = [manifest['runs'][case_id]['room_id'] for case_id in specs]
    if len(set(conversations + rooms)) != len(specs) * 2:
        raise ValueError('Benchmark arms do not have distinct conversations')
    recovery = read_json(folder / 'recovery.json') if with_recovery else None
    if recovery and recovery['first_pass_sha256'] != digest(first.read_bytes()):
        raise ValueError('First-pass receipt changed after continuation')
    pairs = []
    for case in fixture['cases']:
        cid = case['id']
        single = score_arm(baseline_folder, case, 'single', baseline['runs'][f'{cid}/single'], baseline, fixture)
        automatic = score_team(folder, case, manifest['runs'][cid], manifest, fixture)
        pair = {'id': cid, 'name': case['name'], 'single': single, 'automatic': automatic}
        if recovery is not None:
            state = recovery['runs'].get(cid) or {}
            pair['recovery_outcome'] = state.get('outcome')
            if state.get('outcome') == 'continued':
                pair['recovery'] = score_team(folder, case, state, manifest, fixture, recovered=True)
        pairs.append(pair)
    single = [pair['single'] for pair in pairs]
    auto = [pair['automatic'] for pair in pairs]
    delta = [a['elapsed_s'] - s['elapsed_s'] for a, s in zip(auto, single) if all(r['checks']['terminal-receipt'] for r in (a, s))]
    result = {'run_id': manifest['run_id'], 'baseline_run_id': baseline['run_id'], 'fixture_sha256': manifest['fixture_sha256'],
              'single': {'passed': sum(arm['passed'] for arm in single), 'jobs_terminal': sum(arm['checks']['terminal-receipt'] for arm in single),
                         'verified_html': sum(arm['checks'].get('verified-html', False) for arm in single),
                         'recorded_turns': sum(arm.get('recorded_turns', 0) for arm in single),
                         'median_elapsed_s': med([arm['elapsed_s'] for arm in single if arm['checks']['terminal-receipt']])},
              'automatic': summarize(auto),
              'paired': {'both': sum(a['passed'] and s['passed'] for a, s in zip(auto, single)),
                         'single_only': sum(s['passed'] and not a['passed'] for a, s in zip(auto, single)),
                         'automatic_only': sum(a['passed'] and not s['passed'] for a, s in zip(auto, single)),
                         'neither': sum(not a['passed'] and not s['passed'] for a, s in zip(auto, single)),
                         'automatic_slower': sum(x > 0 for x in delta), 'median_automatic_minus_single_s': med(delta)},
              'provider_billed_cost': None, 'provider_token_usage': None, 'pairs': pairs}
    if recovery is not None:
        outcomes = Counter([p['recovery_outcome'] for p in pairs if p['recovery_outcome'] is not None])
        result['continuation'] = {'outcomes': dict(outcomes), 'continued_cases': summarize([p['recovery'] for p in pairs if 'recovery' in p]),
                                  'effective_all_cases': summarize([p.get('recovery', p['automatic']) for p in pairs])}
        result['continuation']['effective_all_cases'].pop('median_elapsed_s')  # Two incomparable time windows.
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True, type=uuid.UUID)
    parser.add_argument('--with-recovery', action='store_true')
    args = parser.parse_args()
    print(json.dumps(score(DEFAULT_OUTPUT / str(args.run_id), with_recovery=args.with_recovery), indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
