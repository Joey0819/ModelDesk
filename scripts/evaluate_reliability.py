"""Repeatable ModelDesk reliability acceptance, with explicit real-call opt in.

Without --live this runs deterministic failure/recovery contracts. --live creates
a labeled group, two real model/MCP tasks, and local Word/PDF/HTML exports. It
never retries a POST, deletes existing work, or creates a third-party connection.
Use a new output directory for each run; existing evidence is never overwritten.
"""
import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def validate_handoff(delivery):
    """Compare the real tool receipt with its exact input, not a model's claim."""
    step = next(item for item in delivery['steps'] if item['kind'] == 'mcp')
    prompt = step['dispatch_prompt']
    counts = json.loads(step['output'])
    assert counts == {'characters': len(prompt), 'words': len(prompt.split()),
                      'lines': len(prompt.splitlines())}, 'MCP statistics do not match the sent input'
    content = delivery['content']
    assert re.findall(r'^([1-9][0-9]*)\. ', content, re.M) == ['1', '2', '3']
    rows = [line for line in content.splitlines() if line.startswith('|')]
    assert len(rows) == 5 and all(len(row.split('|')[1:-1]) == 2 for row in rows)
    assert content.rstrip().endswith('星河220。')
    assert all(str(value) in content for value in counts.values())
    return counts


def validate_revision(previous, current, previous_counts, current_counts):
    expected = previous.replace('蓝色便签', '绿色便签')
    # Replace whole numbers once so one old value cannot mutate another.
    mapping = {str(previous_counts[key]): str(current_counts[key]) for key in previous_counts}
    expected = re.sub(r'\b[0-9]+\b', lambda match: mapping.get(match[0], match[0]), expected)
    assert current == expected, 'An unrequested part of the baseline document changed'


def live(args):
    import httpx
    from docx import Document
    from pypdf import PdfReader

    directory = Path(args.output).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    client = httpx.Client(base_url='http://127.0.0.1:8001', timeout=45, trust_env=False,
                          headers={'Origin': 'http://localhost:5173'})
    receipts = {'scope': 'real_models_and_local_mcp', 'resources': [], 'jobs': []}
    def save(name, value):
        (directory / name).write_text(json.dumps(value, ensure_ascii=False, indent=2))
    def post(path, body):
        save('last-submission.json', {'path':path, 'state':'dispatching'})
        # A timeout is an uncertain submission, not permission to repeat it.
        response = client.post(path, json=body)
        response.raise_for_status()
        value = response.json()
        save('last-submission.json', {'path':path, 'state':'acknowledged', 'id':value.get('id')})
        return value
    def get(path):
        response = client.get(path); response.raise_for_status(); return response.json()

    servers = get('/api/platform/mcp')
    server = next(item for item in servers if item.get('enabled') and item.get('url') == 'http://127.0.0.1:8371/mcp'
                  and 'text_statistics' in item.get('allowed_tools', []))
    memory = post('/api/platform/memories', {'name':'Reliability acceptance — full source tail',
                  'content': '此资料仅供自动协作验收，不是外部事实。\n' * 120 + '\n文末校验行必须写：星河220。',
                  'scope':'acceptance220'})
    receipts['resources'].append(memory['id']); save('receipts.json', receipts)
    team = post('/api/harness/teams', {'name':'2.20 自动协作可靠性验收', 'project_id':None,
                 'coordinator_model':args.author_model, 'members':[
        {'id':'writer','name':'作者','kind':'model','model':args.author_model,'role':'按完整用户要求撰写实际文稿，保留资料尾部校验行。'},
        {'id':'analyst','name':'计数 Agent','kind':'mcp','mcp_server_id':server['id'],'mcp_tool':'text_statistics',
         'mcp_prompt_field':'text','role':'对平台交给你的完整交接包计算实际字符、词、行数；统计范围不是最终文章。'},
        {'id':'reviewer','name':'审稿人','kind':'model','model':args.reviewer_model,
         'role':'检查作者原稿及实际计数结果，按最新修改整理完整成品，保留未修改的原始要求。'}]})
    room = post('/api/groups', {'title':'2.20 自动协作可靠性验收','project_id':None,'team_id':team['id']})
    receipts.update(team_id=team['id'], room_id=room['id']); save('receipts.json', receipts)
    original = ('请用中文交付一份初学者指南，标题固定为《离开工作台，任务继续》。作者先写约200字的说明，包含三个编号操作步骤和一个两列三行数据的表格（角色、交付）。'
                '以“蓝色便签”为例，保留所选记忆末尾的校验行。按作者 → 计数 Agent → 审稿人的依赖顺序执行。'
                '计数 Agent 统计它实际收到的完整交接包，审稿人必须保留返回的字符数、词数、行数，并写明“统计对象是完整交接包，不是最终文稿”。'
                '交付完整正文以及实际 Word、PDF、独立 HTML 文件，使用报告版式。不需要图片、视频、代码或外部联网。')
    prompts = [original, '只将例子里的“蓝色便签”改成“绿色便签”，保留原有标题、三个步骤、表格、校验行及全部交付文件。仍按原来的三位成员顺序协作，使用本轮真实计数。']
    summary = []
    previous_content, previous_counts = None, None
    for index, prompt in enumerate(prompts):
        job = post('/api/groups/' + room['id'] + '/messages', {'prompt':prompt,'delivery_mode':'auto','background_mode':True,
                   'output_format':'text','context_mode':'fresh' if index == 0 else 'continue','context_turns':1,
                   'include_project_assets':False,'document_formats':None,'document_layout':'auto',
                   'memory_ids':[memory['id']],'remember':False})
        receipts['jobs'].append(job['id']); save('receipts.json', receipts)
        print(json.dumps({'turn':index+1,'job_id':job['id'],'room_id':room['id']}, ensure_ascii=False), flush=True)
        last = None
        for _ in range(300):
            job = get('/api/workbench/jobs/' + job['id'])
            delivery = (job.get('result') or {}).get('delivery') or {}
            state = (job['status'], delivery.get('status'), job.get('autonomy', {}).get('state'),
                     [(step['member_id'], step['status']) for step in delivery.get('steps', [])])
            if state != last:
                print(state, flush=True); last = state
            save(f'turn-{index+1}.json', job)
            if job.get('autonomy', {}).get('state') in {'complete','needs_attention','cancelled'}:
                break
            time.sleep(2)
        else:
            raise RuntimeError('Acceptance wait expired; original task remains saved and was not resubmitted')
        assert job['autonomy']['state'] == 'complete', job['autonomy']
        assert job['status'] == delivery['status'] == 'complete', delivery.get('limitations')
        assert {entry['format'] for entry in delivery['documents']} == {'docx','pdf','html'}
        assert len(job['task_context']['requests']) == index + 1
        brief = delivery['task_brief']
        assert brief['objective'] == original and brief['latest_instruction'] == prompt
        assert brief['revision'] == index + 1
        mcp_step = next(step for step in delivery['steps'] if step['kind'] == 'mcp')
        assert mcp_step['dispatch_count'] == 1 and mcp_step['operation_receipt']['external_task_id'] == mcp_step['task_id']
        assert all(step['input_context']['role_packet']['brief_sha256'] == brief['sha256'] for step in delivery['steps'])
        counts = validate_handoff(delivery)
        if previous_content is not None:
            validate_revision(previous_content, delivery['content'], previous_counts, counts)
        files = []
        colour = '绿色便签' if index else '蓝色便签'
        for entry in delivery['documents']:
            response=client.get(entry['url']); response.raise_for_status(); raw=response.content
            assert hashlib.sha256(raw).hexdigest() == entry['sha256']
            assert response.headers['content-type'].startswith(entry['media_type'])
            (directory / f'turn-{index+1}.{entry["format"]}').write_bytes(raw)
            if entry['format'] == 'docx':
                doc=Document(BytesIO(raw)); assert doc.tables
                text='\n'.join(p.text for p in doc.paragraphs)+'\n'+'\n'.join(cell.text for table in doc.tables for row in table.rows for cell in row.cells)
            elif entry['format'] == 'pdf':
                doc=PdfReader(BytesIO(raw)); assert doc.pages
                text='\n'.join(page.extract_text() for page in doc.pages)
            else:
                text=raw.decode(); assert '<table' in text
            normalized=''.join(text.split())
            assert colour in normalized and '星河220' in normalized and '交接包' in normalized
            assert '离开工作台，任务继续' in normalized
            assert all(str(value) in normalized for value in counts.values())
            if index:
                assert '蓝色便签' not in normalized
            files.append({'format':entry['format'],'bytes':len(raw),'sha256':entry['sha256']})
        summary.append({'job_id':job['id'],'turn':index+1,'brief_revision':brief['revision'],
                        'original_retained':True,'mcp_dispatch_count':1,'actual_input_counts':counts,
                        'baseline_unchanged_except_requested_revision':bool(index),'documents':files})
        save('summary.json', summary)
        previous_content, previous_counts = delivery['content'], counts
    print(json.dumps({'passed':True, 'turns':len(summary), 'documents':sum(len(row['documents']) for row in summary),
                      'evidence_directory':str(directory)}, ensure_ascii=False), flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--output', default=str(ROOT/'data/acceptance-220/live'))
    parser.add_argument('--author-model', default='gpt-6.1-sol')
    parser.add_argument('--reviewer-model', default='gemini-3-flash')
    args=parser.parse_args()
    if args.live:
        live(args)
    else:
        subprocess.run([sys.executable,'-m','unittest','backend.test_execution_store','backend.test_autonomy',
                        'backend.test_task_brief','backend.test_task_context','backend.test_mcp_agent',
                        'backend.test_group_delivery_runtime','backend.test_readiness','-q'],cwd=ROOT,check=True)


if __name__ == '__main__':
    main()
