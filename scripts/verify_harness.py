"""Bounded real-model acceptance for cross-framework collaboration.

Uses only self-generated image/text fixtures and the configured local gateway.
`--phase start` stops at the review gate; restart the API, then use `--phase
finish` to verify recovery without replaying the successful first stage.
"""
import argparse
import io
import json
from pathlib import Path
import time
import uuid

import httpx
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / 'data/harness-acceptance.json'
MODELS = ['gpt-6.1-sol', 'gemini-3-flash']
STOPPED = {'complete', 'error', 'cancelled', 'interrupted', 'waiting_review'}


def call(client, method, route, **kwargs):
    response = client.request(method, route, **kwargs)
    response.raise_for_status()
    return response.json()


def check_public(value):
    if isinstance(value, dict):
        assert not ({'token', '_runtime', '_attachments', 'resource_revisions'} & value.keys()), 'private runtime keys exposed'
        for item in value.values(): check_public(item)
    elif isinstance(value, list):
        for item in value: check_public(item)
    elif isinstance(value, str):
        assert 'data:image/' not in value and ';base64,' not in value, 'image bytes exposed'
        assert 'attachment-inputs/' not in value, 'private input path exposed'


def wait(client, identifier, timeout=1200):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        run = call(client, 'GET', '/api/harness/runs/' + identifier)
        check_public(run)
        state = (run['status'], tuple(step['status'] for step in run['steps']))
        if state != last:
            print(json.dumps({'run':identifier, 'status':state[0], 'steps':state[1]}), flush=True)
            last = state
        if run['status'] in STOPPED: return run
        time.sleep(1)
    call(client, 'POST', '/api/harness/runs/' + identifier + '/cancel')
    raise AssertionError('acceptance exceeded its bounded timeout')


def save(report):
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2))


def start(client):
    tag = uuid.uuid4().hex[:6].upper()
    marker = 'HANDOFF-' + tag
    text_marker = 'DOC-' + tag
    visual_marker = tag[:5]
    project = call(client, 'POST', '/api/workbench/projects', json={
        'name':'跨框架协作验收 ' + tag, 'description':'仅自建的图片和文字资料。',
        'instructions':'中文简短回答，准确标记材料与实际读取内容。'})
    pid = project['id']
    image = Image.new('RGB', (600, 260), 'white')
    draw = ImageDraw.Draw(image)
    draw.rectangle((50, 45, 170, 165), fill='red')
    draw.ellipse((230, 45, 350, 165), fill='blue')
    draw.text((180, 190), visual_marker, fill='black', font=ImageFont.load_default(size=38))
    output = io.BytesIO(); image.save(output, format='PNG')
    picture = call(client, 'POST', f'/api/workbench/projects/{pid}/assets',
                   files={'file':('harness-visual.png', output.getvalue(), 'image/png')}, data={'shared':'false'})
    brief = call(client, 'POST', f'/api/workbench/projects/{pid}/assets',
                 files={'file':('brief.txt', ('合成文稿代号：' + text_marker + '\n目标：发布前检查功能、文档和备份。').encode(), 'text/plain')}, data={'shared':'true'})
    workflow = {'agents':[{'id':'writer', 'role':'交付编辑', 'goal':'根据实际前序交付形成简短清单，准确保留代号', 'model':MODELS[0], 'tools':[]}],
                'tasks':[{'id':'delivery', 'agent':'writer', 'description':'完成当前任务要求，准确保留前序步骤通行码、图中五位文字、文稿代号和两种颜色形状。',
                          'expected_output':'含代号、颜色形状的三条发布清单', 'context':[]}], 'max_iter':3, 'process':'sequential'}
    payload = {'goal':'依据附图和项目文稿，研究并交叉核查一份发布前检查清单。每一步回答必须保留：图片下方五位文字、从左到右的两种颜色与形状、文稿代号，以及前序步骤通行码（若已有）。总长不超过200字。',
               'models':MODELS, 'project_id':pid, 'attachment_ids':[picture['id']], 'include_project_assets':True,
               'timeout_seconds':1500, 'step_timeout_seconds':600,
               'steps':[{'id':'research', 'title':'角色研究', 'mode':'camel', 'rounds':1, 'require_review':True,
                         'instructions':'实际读取图片与文稿，研究检查清单。为后续步骤记录通行码 ' + marker + '。'},
                        {'id':'review', 'title':'匿名核查', 'mode':'council', 'instructions':'重新读取冻结的图片、文稿，核查前序实际交付；准确保留其通行码，不得猜测。'},
                        {'id':'deliver', 'title':'团队交付', 'mode':'crewai', 'workflow':workflow,
                         'instructions':'结合前序研究与评审，输出三条可执行检查清单。准确保留前序通行码、附图五位文字、文稿代号、颜色与形状。'}]}
    created = call(client, 'POST', '/api/harness/runs', json=payload)
    report = {'run_id':created['id'], 'project_id':pid, 'image_id':picture['id'], 'brief_id':brief['id'],
              'expected':{'handoff':marker, 'visual':visual_marker, 'brief':text_marker}, 'phase':'running'}
    save(report)
    gated = wait(client, created['id'])
    assert gated['status']=='waiting_review', gated.get('error') or gated['status']
    assert not gated['steps'][1]['attempts'], 'next stage ran before approval'
    first = gated['steps'][0]
    assert len(first['attempts'])==1
    report['first_job_id'] = first['attempts'][0]['job_id']
    report['first_output'] = first['output']['text']
    # This fixture is ours: changing its shared flag must not change already frozen inputs.
    call(client, 'PUT', f'/api/workbench/projects/{pid}/assets/{brief["id"]}', json={'shared':False})
    report['phase']='waiting_review'; report['gate_prevented_next_step']=True
    save(report)
    return report


def finish(client, report):
    identifier = report['run_id']; route = '/api/harness/runs/' + identifier
    before = call(client, 'GET', route)
    assert before['status']=='waiting_review', before['status']
    assert before['steps'][0]['attempts'][0]['job_id']==report['first_job_id']
    assert before['steps'][0]['output']['text']==report['first_output']
    assert not before['steps'][1]['attempts']
    call(client, 'POST', route + '/approve')
    completed = wait(client, identifier)
    assert completed['status']=='complete', completed.get('error') or completed['status']
    assert len(completed['steps'])==3
    manifest_sha = None
    for step in completed['steps']:
        assert step['status']=='complete' and len(step['attempts'])==1, step['id']
        answer = step['output']['text']
        for marker in report['expected'].values(): assert marker in answer, f'{step["id"]} missing {marker}'
        assert '红' in answer and '蓝' in answer and '圆' in answer and ('方' in answer or '矩形' in answer), step['id'] + ' did not read image'
        child = call(client, 'GET', '/api/workbench/jobs/' + step['attempts'][0]['job_id'])
        check_public(child)
        hashes = {a['id']:a['content_sha256'] for a in child.get('attachments',[])}
        assert set(hashes)=={report['image_id'],report['brief_id']}, 'frozen material set changed'
        if manifest_sha is None: manifest_sha=hashes
        assert hashes==manifest_sha, 'input bytes changed between stages'
    assert completed['steps'][0]['attempts'][0]['job_id']==report['first_job_id'], 'first stage replayed'
    exported = client.get(route + '/export', params={'format':'md'}); exported.raise_for_status()
    for marker in report['expected'].values(): assert marker in exported.text
    exported_json = client.get(route + '/export', params={'format':'json'}); exported_json.raise_for_status()
    check_public(exported_json.json())
    global_runs = call(client,'GET','/api/harness/runs')
    listed = global_runs if isinstance(global_runs,list) else global_runs.get('runs',[])
    assert identifier not in [item['id'] for item in listed], 'project run leaked into unscoped list'
    cancel_run = call(client,'POST','/api/harness/runs',json={
        'goal':'生成一份详细的多模型协作说明。', 'models':[MODELS[0]], 'project_id':report['project_id'],
        'include_project_assets':False, 'steps':[{'id':'draft','title':'取消验收','mode':'chat'}]})
    cancelled=call(client,'POST','/api/harness/runs/'+cancel_run['id']+'/cancel')
    assert cancelled['status']=='cancelled', cancelled['status']
    report.update(phase='complete', status=completed['status'], completed_child_jobs=[step['attempts'][0]['job_id'] for step in completed['steps']],
                  no_completed_step_replay=True, all_three_frameworks_received_same_frozen_bytes=True,
                  handoff_code_preserved=True, real_image_reading=True, exports_verified=True, project_list_isolated=True,
                  cancel_run_id=cancel_run['id'], cancellation_verified=True)
    save(report)
    print(json.dumps({k:v for k,v in report.items() if k not in {'first_output','expected'}},ensure_ascii=False,indent=2),flush=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--phase', choices=['all','start','finish'], default='all')
    parser.add_argument('--base', default='http://127.0.0.1:8001')
    args=parser.parse_args()
    with httpx.Client(base_url=args.base, trust_env=False, timeout=30) as client:
        report=start(client) if args.phase!='finish' else json.loads(REPORT.read_text())
        if args.phase!='start': finish(client,report)
    print('Acceptance record:', REPORT, flush=True)


if __name__=='__main__': main()
