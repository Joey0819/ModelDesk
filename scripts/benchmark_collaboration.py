#!/usr/bin/env python3
"""Opt-in paired chat/group run through a running loopback ModelDesk Workbench."""

import argparse
import hashlib
import ipaddress
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'scripts/fixtures/collaboration_benchmark.json'
API = '/api/workbench/jobs'
TERMINAL = {'complete', 'error', 'cancelled', 'interrupted'}
DEFAULT_OUTPUT = ROOT / 'data/collaboration-benchmark'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def checked_base(value):
    url = urlsplit(value.rstrip('/'))
    try:
        loopback = ipaddress.ip_address(url.hostname).is_loopback
    except ValueError:
        loopback = False
    if (url.scheme != 'http' or not loopback or url.username is not None or url.password is not None or
            url.path or url.query or url.fragment):
        raise ValueError('Use a literal loopback HTTP origin such as http://127.0.0.1:8001')
    return url.geturl()


class Client:
    def __init__(self, base):
        self.base = checked_base(base)
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, path, body=None):
        if not path.startswith('/api/workbench/'):
            raise ValueError('Unexpected Workbench route')
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
        with self.opener.open(Request(self.base + path, data=data, headers={'Content-Type': 'application/json'}),
                              timeout=30) as response:
            return response.read(), response.headers

    def get(self, path):
        return json.loads(self.request(path)[0])

    def submit(self, body):
        return json.loads(self.request(API, body)[0])


def write_json(path, payload):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    tmp.replace(path)


def now():
    return datetime.now(timezone.utc).isoformat()


def elapsed(start, end):
    return round((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds(), 3)


def build_request(arm, case, fixture, manifest, state):
    final = manifest['final_model']
    models = [final]
    roles = {final: fixture['final_role']}
    if arm == 'group':
        reviewer = manifest['reviewer_model']
        models.insert(0, reviewer)
        roles[reviewer] = fixture['reviewer_role']
    return {
        'mode': 'chat' if arm == 'single' else 'group', 'prompt': case['prompt'], 'models': models,
        'system_prompt': fixture['system'], 'roles': roles, 'rounds': 1,
        'conversation_id': state['conversation_id'], 'include_project_assets': False, 'tools': [],
    }


def select_final(job, arm, final):
    result = job.get('result') or {}
    if arm == 'single':
        responses = result.get('responses', [])
        item = next((row for row in responses if row.get('model') == final), {})
        return f'chat:{final}', item.get('text', ''), item, responses
    messages = result.get('messages', [])
    for idx in range(len(messages) - 1, -1, -1):
        if messages[idx].get('model') == final:
            return f'group:{idx}', messages[idx].get('content', ''), messages[idx], messages
    return None, '', {}, messages


def export_final(client, folder, job, artifact_id):
    job_route = f'{API}/{job["id"]}'
    payload, headers = client.request(job_route + '/export?' + urlencode({'format': 'html', 'artifact_id': artifact_id, 'layout': 'report'}))
    route = headers.get('X-ModelDesk-Document-URL', '')
    if not route.startswith(job_route + '/documents/') or len(route) == len(job_route + '/documents/'):
        raise ValueError('Export did not return a scoped download URL')
    download, download_headers = client.request(route)
    if (payload != download or headers.get_content_type() != 'text/html' or
            download_headers.get_content_type() != 'text/html' or not download.strip()):
        raise ValueError('Persisted HTML download differs from the export or MIME type is wrong')
    (folder / 'final.html').write_bytes(download)
    return {'status': 'downloaded', 'sha256': hashlib.sha256(download).hexdigest(), 'bytes': len(download)}


def run(client, folder, manifest, fixture, idle_limit, poll_limit):
    for case_idx, case in enumerate(fixture['cases']):
        order = ('single',) if manifest.get('single_only') else (('single', 'group') if case_idx % 2 == 0 else ('group', 'single'))
        for arm in order:
            key = f'{case["id"]}/{arm}'
            state = manifest['runs'][key]
            if state.get('finished_at'):
                continue
            sub = folder / key
            sub.mkdir(exist_ok=True, parents=True)
            if not state.get('job_id'):
                start = time.monotonic()
                while True:
                    jobs = client.get(API)
                    matches = [job for job in jobs if job.get('conversation_id') == state['conversation_id']]
                    if len(matches) > 1:
                        raise RuntimeError(f'{key}: duplicate conversation; stop and inspect')
                    if matches:
                        job = matches[0]
                        break
                    if state.get('submitting_at'):
                        raise RuntimeError(f'{key}: previous submission has an unknown outcome; inspect recent jobs before taking action')
                    busy = [job for job in jobs if job.get('status') not in TERMINAL]
                    if not busy:
                        state['submitting_at'] = now()
                        write_json(folder / 'manifest.json', manifest)
                        experiment_body = build_request(arm, case, fixture, manifest, state)
                        write_json(sub / 'request.json', experiment_body)
                        job = client.submit(experiment_body)
                        break
                    if time.monotonic() - start > idle_limit:
                        raise RuntimeError('Workbench is busy; run paused before sending the next request')
                    time.sleep(5)
                state['job_id'] = job['id']
                write_json(folder / 'manifest.json', manifest)
                print(f'{key}: submitted {job["id"]}', flush=True)
            deadline = time.monotonic() + poll_limit
            while True:
                job = client.get(f'{API}/{state["job_id"]}')
                if job.get('status') in TERMINAL:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'{key}: job in progress; resume later without re-submitting')
                time.sleep(1.0)
            write_json(sub / 'job.json', job)
            artifact, text, _, turns = select_final(job, arm, manifest['final_model'])
            state.update(job_status=job.get('status'), job_elapsed_s=elapsed(job['created_at'], job['updated_at']),
                         artifact_id=artifact, recorded_turns=len(turns),
                         response_issues=[{'model': turn.get('model'), 'error': turn.get('error'),
                                           'incomplete': bool(turn.get('incomplete'))}
                                          for turn in turns if turn.get('error') or turn.get('incomplete')])
            (sub / 'final.md').write_text(text, encoding='utf-8')
            if artifact and text.strip():
                try:
                    state['export'] = export_final(client, sub, job, artifact)
                except (OSError, ValueError) as exc:
                    state['export'] = {'status': 'failed', 'error': str(exc)}
            else:
                state['export'] = {'status': 'no_final_output'}
            state['finished_at'] = now()
            write_json(folder / 'manifest.json', manifest)
            print(f'{key}: {state["job_status"]} {state["job_elapsed_s"]:.2f}s, export={state["export"]["status"]}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Submit real model jobs (uses provider capacity)')
    parser.add_argument('--run-id', required=True, help='UUID to identify or resume this run')
    parser.add_argument('--base-url', default='http://127.0.0.1:8001')
    parser.add_argument('--final-model', default='gpt-6.1-sol')
    parser.add_argument('--reviewer-model', default='gemini-3-flash')
    parser.add_argument('--idle-wait', type=int, default=60)
    parser.add_argument('--job-wait', type=int, default=720)
    args = parser.parse_args()
    try:
        run_id = uuid.UUID(args.run_id)
        base_url = checked_base(args.base_url)
    except ValueError as exc:
        parser.error(str(exc))
    if args.idle_wait < 0 or args.job_wait < 1:
        parser.error('Wait periods must be nonnegative; the job wait must be at least 1 second')
    if args.final_model == args.reviewer_model:
        parser.error('The reviewer and final model must be distinct')
    fixture_data = FIXTURE.read_bytes()
    fixture = json.loads(fixture_data)
    identity = {'version': 1, 'run_id': str(run_id), 'fixture_sha256': hashlib.sha256(fixture_data).hexdigest(),
                'base_url': base_url, 'final_model': args.final_model, 'reviewer_model': args.reviewer_model}
    folder = DEFAULT_OUTPUT / str(run_id)
    target = folder / 'manifest.json'
    if not args.live:
        print(json.dumps(load_manifest(target, identity, fixture, run_id), ensure_ascii=False, indent=2))
        return
    run_live_baseline(target, identity, fixture, run_id, args.idle_wait, args.job_wait)


def run_live_baseline(target, identity, fixture, run_id, idle_wait, job_wait):
    folder = target.parent
    folder.mkdir(exist_ok=True, parents=True, mode=0o700)
    lock = folder / 'run.lock'
    try:
        lock.mkdir()
    except FileExistsError:
        raise RuntimeError('This run is locked; inspect the process before removing a stale lock directory') from None
    try:
        manifest = load_manifest(target, identity, fixture, run_id)
        write_json(target, manifest)
        run(Client(identity['base_url']), folder, manifest, fixture, idle_wait, job_wait)
        return manifest
    finally:
        lock.rmdir()


def load_manifest(target, identity, fixture, run_id):
    if target.exists():
        manifest = json.loads(target.read_bytes())
        if (any(manifest.get(key) != value for key, value in identity.items())
                or manifest.get('single_only', False) != identity.get('single_only', False)):
            raise ValueError('This run ID is already linked to different settings or fixtures')
    else:
        manifest = {**identity, 'started_at': now(), 'provider_billed_cost': None,
                    'provider_token_usage': None, 'runs': {}}
        for case in fixture['cases']:
            for arm in (('single',) if identity.get('single_only') else ('single', 'group')):
                key = f'{case["id"]}/{arm}'
                manifest['runs'][key] = {'conversation_id': str(uuid.uuid5(run_id, key))}
    return manifest


if __name__ == '__main__':
    main()
