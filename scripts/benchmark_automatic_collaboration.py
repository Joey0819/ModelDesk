#!/usr/bin/env python3
"""Opt-in paired evaluation of a saved ModelDesk team's automatic delivery."""
import argparse
import hashlib
import json
import time
import uuid
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request

from benchmark_collaboration import (
    API, DEFAULT_OUTPUT, FIXTURE, TERMINAL, Client, checked_base, elapsed, export_final, load_manifest, now, run_live_baseline, write_json,
)

PROJECTS = '/api/workbench/projects'
TEAMS = '/api/harness/teams'
GROUPS = '/api/groups'
LOCAL_UI_ORIGIN = 'http://127.0.0.1:5173'


class GroupClient(Client):
    def request(self, path, body=None):
        if path.split('?', 1)[0].split('/messages', 1)[0].startswith(GROUPS + '/') or path.split('?', 1)[0] in {GROUPS, TEAMS}:
            data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
            headers = {'Content-Type': 'application/json'}
            if body is not None:
                headers['Origin'] = LOCAL_UI_ORIGIN
            with self.opener.open(Request(self.base + path, data=data, headers=headers), timeout=30) as response:
                return response.read(), response.headers
        return super().request(path, body)


def create_or_resolve(client, manifest_path, manifest, state, key, route, list_route, body, identity):
    if key in state:
        return state[key]
    matches = [row for row in client.get(list_route) if all(row.get(k) == v for k, v in identity.items())]
    if len(matches) > 1:
        raise RuntimeError(f'Ambiguous evaluation resource: {key}')
    if matches:
        item = matches[0]
    else:
        if state.get(key + '_pending'):
            raise RuntimeError(f'Unknown save outcome for {key}; inspect before reusing this run ID')
        state[key + '_pending'] = now()
        write_json(manifest_path, manifest)
        item = json.loads(client.request(route, body)[0])
    state[key] = item['id']
    write_json(manifest_path, manifest)
    return item['id']


def read(path):
    return json.loads(path.read_bytes())


def build_auto_request(case, fixture):
    return {'prompt': case['prompt'], 'system_prompt': fixture['system'], 'delivery_mode': 'auto',
            'context_mode': 'fresh', 'include_project_assets': False, 'tools': [],
            'output_format': 'text', 'document_formats': [], 'document_layout': 'report'}


def wait_idle(client, limit):
    deadline = time.monotonic() + limit
    while any(job.get('status') not in TERMINAL for job in client.get(API)):
        if time.monotonic() >= deadline:
            raise RuntimeError('Workbench is busy; pause until it is available')
        time.sleep(5)


def wait_terminal(client, job_id, limit):
    deadline = time.monotonic() + limit
    while True:
        job = client.get(f'{API}/{job_id}')
        if job.get('status') in TERMINAL:
            return job
        if time.monotonic() >= deadline:
            raise RuntimeError('Job in progress; resume observation with the same run ID')
        time.sleep(2)


def collect(client, sub, state, job):
    write_json(sub / 'job.json', job)
    result = job.get('result') or {}
    delivery = result.get('delivery') or {}
    artifact_id = 'group:delivery'
    text = delivery.get('content') or ''
    state.update(job_status=job['status'], job_elapsed_s=elapsed(job['created_at'], job['updated_at']),
                 delivery_status=delivery.get('status'), artifact_id=artifact_id,
                 final_model=delivery.get('model'), recorded_turns=len(result.get('messages') or []))
    (sub / 'final.md').write_text(text, encoding='utf-8')
    if text.strip():
        try:
            state['export'] = export_final(client, sub, job, artifact_id)
        except (OSError, ValueError) as exc:
            state['export'] = {'status': 'failed', 'error': str(exc)}
    else:
        state['export'] = {'status': 'no_final_output'}
    state['finished_at'] = now()


def recover(client, folder, manifest, fixture, idle_wait, job_wait):
    target = folder / 'recovery.json'
    first = folder / 'automatic.json'
    identity = {key: manifest[key] for key in ('version', 'run_id', 'baseline_run_id', 'base_url',
                                               'fixture_sha256', 'final_model', 'reviewer_model', 'resources')}
    identity['first_pass_sha256'] = hashlib.sha256(first.read_bytes()).hexdigest()
    recovery = read(target) if target.exists() else {**identity, 'started_at': now(), 'runs': {}}
    if any(recovery.get(key) != value for key, value in identity.items()):
        raise RuntimeError('Recovery manifest does not match the original first pass')
    cases = [case['id'] for case in fixture['cases']]
    if any(not manifest['runs'].get(case_id, {}).get('finished_at') for case_id in cases):
        raise RuntimeError('Finish the first pass before continuing a job')
    project_id = manifest['resources']['project_id']
    scope = '?' + urlencode({'project_id': project_id})
    for case_id in cases:
        original = manifest['runs'][case_id]
        if original['delivery_status'] == 'complete':
            continue
        state = recovery['runs'].setdefault(case_id, {k: original[k] for k in ('job_id', 'room_id', 'delivery_status')})
        if state.get('finished_at'):
            continue
        sub = folder / case_id / 'recovery'
        sub.mkdir(parents=True, exist_ok=True)
        previous = read(folder / case_id / 'automatic' / 'job.json')
        previous_workflow = ((previous.get('result') or {}).get('delivery') or {}).get('workflow') or {}
        revision, count = previous_workflow.get('revision'), previous_workflow.get('resume_count')
        if (original['delivery_status'] not in {'partial', 'blocked'} or previous.get('status') not in TERMINAL
                or not isinstance(revision, int) or revision < 1 or count != 0):
            state.update(outcome='not_eligible', finished_at=now())
            write_json(target, recovery)
            continue
        current = client.get(f'{API}/{state["job_id"]}')
        if any(current.get(key) != previous.get(key) for key in ('id', 'group_room_id', 'project_id', 'group_team_snapshot')):
            raise RuntimeError(f'{case_id}: live job identity differs from first pass')
        workflow = ((current.get('result') or {}).get('delivery') or {}).get('workflow') or {}
        if state.get('submitting_at'):
            if workflow.get('resume_count') != count + 1:
                raise RuntimeError(f'{case_id}: previous continuation unconfirmed; inspect original job before proceeding')
        else:
            if current.get('status') not in TERMINAL or workflow.get('revision') != revision or workflow.get('resume_count') != count:
                raise RuntimeError(f'{case_id}: original job changed; refusing to continue')
            wait_idle(client, idle_wait)
            body = {'expected_revision': revision}
            write_json(sub / 'continue_request.json', body)
            state.update(submitting_at=now(), first_revision=revision, first_delivery_status=original['delivery_status'])
            write_json(target, recovery)
            try:
                current = json.loads(client.request(f'{GROUPS}/{state["room_id"]}/messages/{state["job_id"]}/continue{scope}', body)[0])
            except HTTPError as exc:
                state['submission_http'] = exc.code
                if exc.code not in {400, 403, 404, 409, 422, 429}:
                    write_json(target, recovery)
                    raise
                state.update(outcome='rejected', finished_at=now())
                (sub / 'rejection.json').write_bytes(exc.read())
                write_json(target, recovery)
                print(f'{case_id}: continuation rejected with HTTP {exc.code}', flush=True)
                continue
            if current.get('id') != state['job_id']:
                raise RuntimeError(f'{case_id}: continuation response did not identify the original job')
            write_json(sub / 'accepted.json', current)
            print(f'{case_id}: continuing {state["job_id"]}', flush=True)
        current = wait_terminal(client, state['job_id'], job_wait)
        workflow = ((current.get('result') or {}).get('delivery') or {}).get('workflow') or {}
        if workflow.get('resume_count') != count + 1 or workflow.get('revision', 0) <= revision:
            raise RuntimeError(f'{case_id}: terminal continuation receipt has an unexpected revision or count')
        state.update(outcome='continued', final_revision=workflow['revision'],
                     continuation_elapsed_s=elapsed(state['submitting_at'], current['updated_at']))
        collect(client, sub, state, current)
        write_json(target, recovery)
        print(f'{case_id}: delivery={state["delivery_status"]}, '
              f'{state["continuation_elapsed_s"]:.2f}s, export={state["export"]["status"]}', flush=True)


def run(client, folder, manifest, fixture, idle_wait, job_wait):
    target = folder / 'automatic.json'
    resources = manifest['resources']
    workspace = folder / 'workspace'
    workspace.mkdir(exist_ok=True)
    if any(workspace.iterdir()):
        raise RuntimeError('The evaluation workspace must be empty')
    label = 'ModelDesk paired benchmark ' + manifest['run_id']
    project_id = create_or_resolve(client, target, manifest, resources, 'project_id', PROJECTS, PROJECTS,
                                   {'name': label, 'path': str(workspace)}, {'name': label, 'path': str(workspace)})
    scope = '?' + urlencode({'project_id': project_id})
    team_body = {'name': label, 'project_id': project_id, 'coordinator_model': manifest['final_model'],
                 'members': [
        {'id': 'author', 'name': 'Author', 'kind': 'model', 'model': manifest['final_model'],
         'role': fixture['final_role'], 'required': True, 'expected_output': '按当前题目和来源写一份可独立交付的成品。'},
        {'id': 'reviewer', 'name': 'Reviewer', 'kind': 'model', 'model': manifest['reviewer_model'], 'required': True,
         'after_member_ids': ['author'], 'role': '复核作者成品是否符合题目、给定事实、长度和格式。只指出具体问题，不补充来源外的信息。',
         'expected_output': '根据作者的产出，简要指出若有的事实、推断边界、表达或格式问题。'}]}
    team_id = create_or_resolve(client, target, manifest, resources, 'team_id', TEAMS, TEAMS + scope, team_body, {'name': label})
    write_json(folder / 'team.json', client.get(TEAMS + scope))
    for case in fixture['cases']:
        case_id = case['id']
        state = manifest['runs'].setdefault(case_id, {})
        if state.get('finished_at'):
            continue
        sub = folder / case_id / 'automatic'
        sub.mkdir(parents=True, exist_ok=True)
        title = f'{label} {case_id}'
        room = create_or_resolve(client, target, manifest, state, 'room_id', GROUPS, GROUPS + scope,
                                 {'title': title, 'team_id': team_id, 'project_id': project_id}, {'title': title})
        if not state.get('job_id'):
            wait_from = time.monotonic()
            while True:
                jobs = client.get(API)
                matches = [job for job in jobs if job.get('group_room_id') == room]
                if len(matches) > 1:
                    raise RuntimeError(f'{case_id}: multiple jobs in the evaluation room')
                if matches:
                    job = matches[0]
                    break
                if state.get('submitting_at'):
                    raise RuntimeError(f'{case_id}: previous submission unknown; inspect before taking action')
                if not any(job.get('status') not in TERMINAL for job in jobs):
                    body = build_auto_request(case, fixture)
                    write_json(sub / 'request.json', body)
                    state['submitting_at'] = now()
                    write_json(target, manifest)
                    try:
                        job = json.loads(client.request(f'{GROUPS}/{room}/messages{scope}', body)[0])
                    except HTTPError as exc:
                        state['submission_http'] = exc.code
                        write_json(target, manifest)
                        raise
                    break
                if time.monotonic() - wait_from >= idle_wait:
                    raise RuntimeError('Workbench is busy; pause until it is available')
                time.sleep(5)
            state['job_id'] = job['id']
            write_json(target, manifest)
            print(f'{case_id}: submitted {job["id"]}', flush=True)
        job = wait_terminal(client, state['job_id'], job_wait)
        collect(client, sub, state, job)
        write_json(target, manifest)
        print(f'{case_id}: job={state["job_status"]}, delivery={state["delivery_status"]}, '
              f'{state["job_elapsed_s"]:.2f}s, export={state["export"]["status"]}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Use the real installed gateway and providers')
    parser.add_argument('--run-id', required=True, type=uuid.UUID)
    parser.add_argument('--recover', action='store_true', help='Continue each eligible first-pass job once')
    parser.add_argument('--baseline-run-id', type=uuid.UUID, help='Reuse an existing single baseline instead of running it')
    parser.add_argument('--final-model', default='gpt-6.1-sol', help='Single and final-author model for a new baseline')
    parser.add_argument('--reviewer-model', default='gemini-3-flash', help='Reviewer for a new baseline')
    parser.add_argument('--base-url', default='http://127.0.0.1:8001', type=checked_base)
    parser.add_argument('--idle-wait', type=int, default=90)
    parser.add_argument('--job-wait', type=int, default=360)
    args = parser.parse_args()
    if args.run_id == args.baseline_run_id or args.idle_wait < 0 or args.job_wait < 1:
        parser.error('Use distinct run IDs and valid wait times')
    fixture_bytes = FIXTURE.read_bytes()
    sha = hashlib.sha256(fixture_bytes).hexdigest()
    fixture = json.loads(fixture_bytes)
    folder = DEFAULT_OUTPUT / str(args.run_id)
    target = folder / 'automatic.json'
    if args.recover and not target.exists():
        parser.error('No saved first pass for this run ID')
    recorded = read(target) if target.exists() else None
    baseline_id = args.baseline_run_id or (uuid.UUID(recorded['baseline_run_id']) if recorded else uuid.uuid5(args.run_id, 'single-baseline'))
    baseline_target = DEFAULT_OUTPUT / str(baseline_id) / 'manifest.json'
    if args.baseline_run_id or recorded:
        if not baseline_target.exists():
            parser.error('The requested baseline receipt is missing')
        baseline = read(baseline_target)
    else:
        if args.final_model == args.reviewer_model:
            parser.error('The reviewer and final model must be distinct')
        baseline_identity = {'version': 1, 'run_id': str(baseline_id), 'fixture_sha256': sha, 'base_url': args.base_url,
                             'final_model': args.final_model, 'reviewer_model': args.reviewer_model, 'single_only': True}
        baseline = (run_live_baseline(baseline_target, baseline_identity, fixture, baseline_id, args.idle_wait, args.job_wait)
                    if args.live else load_manifest(baseline_target, baseline_identity, fixture, baseline_id))
    every_baseline = all(baseline.get('runs', {}).get(f'{case["id"]}/single', {}).get('finished_at')
                         for case in fixture['cases'])
    if (baseline.get('run_id') != str(baseline_id) or baseline.get('base_url') != args.base_url
            or baseline.get('fixture_sha256') != sha or (args.live and not every_baseline)):
        parser.error('Need the matching fixture, service, and ten terminal single receipts')
    identity = {'version': 1, 'run_id': str(args.run_id), 'baseline_run_id': str(baseline_id), 'base_url': args.base_url,
                'fixture_sha256': sha, 'final_model': baseline['final_model'], 'reviewer_model': baseline['reviewer_model']}
    if not args.live:
        print(json.dumps({**identity, 'live': False, 'recover': args.recover, 'cases': [case['id'] for case in fixture['cases']]}, indent=2))
        return
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = folder / 'run.lock'
    try:
        lock.mkdir()
    except FileExistsError:
        parser.error('Run is locked; inspect the process before removing a stale lock directory')
    try:
        manifest = read(target) if target.exists() else {**identity, 'started_at': now(), 'resources': {}, 'runs': {}}
        if any(manifest.get(k) != v for k, v in identity.items()):
            parser.error('This run ID has different saved settings')
        client = GroupClient(args.base_url)
        if args.recover:
            recover(client, folder, manifest, fixture, args.idle_wait, args.job_wait)
        else:
            write_json(target, manifest)
            run(client, folder, manifest, fixture, args.idle_wait, args.job_wait)
    finally:
        lock.rmdir()


if __name__ == '__main__':
    main()
