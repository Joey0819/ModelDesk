"""Real provider acceptance; submissions are opt-in and never retried.

Creates labeled isolated resources and verifies downloaded bytes independently.
Use --submit to create a new article/video/code task and --collect to read that
same task later. Existing evidence is never overwritten by a new submission.
"""
import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
import subprocess
import sys
import shutil
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    import httpx
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--submit', choices=['article','video','code'])
    parser.add_argument('--collect', action='store_true')
    parser.add_argument('--output', required=True)
    args=parser.parse_args()
    folder=Path(args.output).resolve()
    if bool(args.submit) == bool(args.collect):
        parser.error('Choose either --submit or --collect')
    if args.submit:
        folder.mkdir(parents=True,exist_ok=False)
    client=httpx.Client(base_url='http://127.0.0.1:8001',timeout=45,trust_env=False,headers={'Origin':'http://localhost:5173'})
    def save(name,value):
        (folder/name).write_text(json.dumps(value,ensure_ascii=False,indent=2))
    def post(path,body):
        save('last-request.json',{'path':path,'state':'dispatching'})
        response=client.post(path,json=body)
        response.raise_for_status()
        value=response.json()
        save('last-request.json',{'path':path,'state':'acknowledged','id':value.get('id')})
        return value
    def get(path):
        response=client.get(path);response.raise_for_status();return response.json()
    if args.submit:
        body={'name':'2.21 实际交付验收 · '+args.submit,'description':'隔离验收资源，不是用户生产项目。'}
        if args.submit=='code':
            fixture=folder/'fixture'; fixture.mkdir()
            (fixture/'duration.py').write_text('def format_duration(seconds):\n    return str(seconds)\n')
            (fixture/'README.md').write_text('# Isolated ModelDesk acceptance fixture\nNo user code or credentials.\n')
            subprocess.run(['git','init','-q',str(fixture)],check=True)
            subprocess.run(['git','-C',str(fixture),'add','.'],check=True)
            subprocess.run(['git','-C',str(fixture),'-c','user.name=ModelDesk Acceptance','-c','user.email=acceptance@example.invalid','commit','-qm','Acceptance baseline / 验收基线'],check=True)
            body['path']=str(fixture)
        project=post('/api/workbench/projects',body)
        pin=post('/api/platform/memories',{'name':'验收固定要求','content':'项目文稿使用简体中文。最终交付说明保留校验词“星河221”。不要在成品中混入内部执行日志。',
                   'project_id':project['id'],'memory_type':'constraint','pinned':True})
        writer={'id':'writer','name':'策划作者','kind':'model','model':'gpt-6.1-sol','role':'遵守项目约束，写出完整可交付的实际内容，并为下游准备精确指令。'}
        reviewer={'id':'reviewer','name':'独立审阅','kind':'model','model':'gemini-3-flash','role':'根据原始要求、固定项目记忆和实际执行结果审阅。保留所有未被用户修改的要求，不把计划或假链接当成果。'}
        worker={'article':{'id':'artist','name':'配图师','kind':'image','model':'gpt-image-2.5','role':'按文稿创作两张风格统一但构图不同的真实插图，交给排版与审阅。'},
                'video':{'id':'producer','name':'Muse 制片','kind':'muse','role':'依照编剧与用户要求制作实际可播放 MP4，返回真实附件。核对时长，不能用脚本或文字承诺代替视频。'},
                'code':{'id':'engineer','name':'Codex 工程师','kind':'codex','role':'在隔离项目中实现功能并运行有意义的单元测试，保存代码补丁和实际测试输出，不合并用户项目。'}}[args.submit]
        team=post('/api/harness/teams',{'name':body['name'],'project_id':project['id'],'coordinator_model':'gpt-6.1-sol','members':[writer,worker,reviewer]})
        room=post('/api/groups',{'title':body['name'],'project_id':project['id'],'team_id':team['id']})
        prompts={
            'article':'请制作一篇约500字的中文图文指南《把任务交给协作团队》。面向第一次使用多 Agent 的用户，包含“说清目标”和“检查成果”两个章节，各放一张实际生成的插图，一张讲清任务分工，一张讲清检查实际文件，画风统一。作者→绘图师→独立审阅，图片嵌入对应章节。交付正文、两张独立图片，以及内嵌两图的 Word、PDF、独立 HTML。保留固定项目约束。无需视频和联网。',
            'video':'请制作60–90秒的完整16:9 MP4动画短片《小纸船的回家路》。纸船从安静岸边出发，遇到小雨、经过桥洞、最终回到亮灯的码头，四段故事顺序清楚。可使用你支持的动画与剪辑方式；必须有真实连续运动和镜头变化，不是只交脚本。无需旁白和中文字幕，声音可选。编剧→Muse 制片→独立审阅；交付可播放、可下载的实际视频和简短中文作品说明，不需要额外图片。不要自作主张改成3–4分钟。保留固定项目约束。',
            'code':'请修复本项目 duration.py 中的 format_duration(seconds)：接受非负整数（不接受bool），返回 MM:SS，分钟允许超过59；负数或非整数抛 ValueError。创建 unittest 测试，覆盖0、59、60、3599、3600、负数、浮点数、字符串、bool。作者明确边界→Codex 工程师修改并运行 python -m unittest -v→独立审阅检查真实补丁与测试结果。交付实际代码补丁、测试输出和中文审阅报告，不自动合并原项目。保留固定项目约束。'}
        job=post('/api/groups/'+room['id']+'/messages?project_id='+project['id'],{'prompt':prompts[args.submit], 'delivery_mode':'auto','background_mode':True,
                   'context_mode':'fresh','include_project_assets':False, 'output_format':{'article':'illustrated','video':'video','code':'text'}[args.submit],
                   'image_count':2,'document_formats':['docx','pdf','html'] if args.submit=='article' else ['html']})
        save('receipt.json',{'workflow':args.submit,'project_id':project['id'],'room_id':room['id'],'team_id':team['id'],'memory_id':pin['id'],'job_id':job['id']})
        print(json.dumps({'workflow':args.submit,'job_id':job['id'],'room_id':room['id']},ensure_ascii=False))
        return
    receipt=json.loads((folder/'receipt.json').read_text())
    job=get('/api/workbench/jobs/'+receipt['job_id']); save('job.json',job)
    delivery=(job.get('result') or {}).get('delivery') or {}
    state={'job':job['status'],'delivery':delivery.get('status'),'autonomy':job.get('autonomy',{}).get('state'),
           'steps':[(step.get('member_id'),step.get('status')) for step in delivery.get('steps',[])], 'limitations':delivery.get('limitations',[])}
    print(json.dumps(state,ensure_ascii=False))
    if state['autonomy']!='complete':
        return
    assert delivery['status']=='complete'
    assert '星河221' in delivery['content']
    for marker in receipt.get('expected_markers', []):
        assert marker in delivery['content'], marker
    assert job['project_memory']['entries'][0]['revision']==receipt.get('expected_memory_revision',1)
    if receipt.get('source_job_id'):
        original=get('/api/workbench/jobs/'+receipt['source_job_id'])
        assert original['project_memory']['entries'][0]['revision']==1
        before={entry['sha256'] for entry in original['result']['delivery']['media']}
        assert before=={entry['sha256'] for entry in delivery['media']}
    files=[]
    for kind,entries in [('document',delivery.get('documents',[])),('media',delivery.get('media',[]))]:
        for entry in entries:
            response=client.get(entry['url']);response.raise_for_status();raw=response.content
            assert hashlib.sha256(raw).hexdigest()==entry['sha256']
            name=entry.get('filename') or entry.get('name')
            if not name or Path(name).name!=name: raise AssertionError('Invalid export filename')
            path=folder/name;path.write_bytes(raw)
            evidence={'kind':kind,'name':name,'bytes':len(raw),'sha256':entry['sha256']}
            if kind=='media' and entry['kind']=='image':
                from PIL import Image
                im=Image.open(BytesIO(raw));im.verify();evidence['type']='image'
                assert entry['url'] in delivery['content']
            elif kind=='media' and entry['kind']=='video':
                probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(path)]))
                duration=float(probe['format']['duration']);assert 60<=duration<=90,duration
                subprocess.run(['ffmpeg','-v','error','-i',str(path),'-f','null','-'],check=True,capture_output=True)
                evidence.update(type='video',duration=duration,full_decode=True)
            elif kind=='document' and receipt['workflow']=='article':
                if entry['format']=='docx':
                    with zipfile.ZipFile(BytesIO(raw)) as archive:
                        assert len([name for name in archive.namelist() if name.startswith('word/media/')])>=2
                elif entry['format']=='html':
                    assert len(re.findall(r'data:image/',raw.decode()))>=2
                elif entry['format']=='pdf':
                    from pypdf import PdfReader
                    assert sum(len(page.images) for page in PdfReader(BytesIO(raw)).pages)>=2
            files.append(evidence)
    if receipt['workflow']=='article':
        assert sum(item.get('type')=='image' for item in files)==2
        assert sum(item['kind']=='document' for item in files)==3
    if receipt['workflow']=='video':
        assert any(item.get('type')=='video' for item in files)
    if receipt['workflow']=='code':
        step=next(item for item in delivery['steps'] if item.get('kind')=='codex')
        task=get('/api/group-tasks/'+step['task_id']+'?project_id='+receipt['project_id']+'&room_id='+receipt['room_id'])
        patch=task['diff'].encode()
        assert hashlib.sha256(patch).hexdigest()==step['patch_sha256']==task['diff_sha256']
        assert step['patch_evidence']['content']==task['diff'] and not step['patch_evidence']['truncated']
        (folder/'changes.patch').write_bytes(patch)
        assert not subprocess.check_output(['git','-C',str(folder/'fixture'),'status','--porcelain'])
        with tempfile.TemporaryDirectory(prefix='modeldesk-code-acceptance-') as temporary:
            target=Path(temporary)/'repo'
            shutil.copytree(folder/'fixture',target,ignore=shutil.ignore_patterns('.git','__pycache__'))
            subprocess.run(['git','apply','--check','-'],cwd=target,input=patch,check=True,capture_output=True)
            subprocess.run(['git','apply','-'],cwd=target,input=patch,check=True,capture_output=True)
            tests=subprocess.run([sys.executable,'-m','unittest','discover','-v'],cwd=target,capture_output=True,text=True)
            (folder/'test-output.txt').write_text(tests.stdout+tests.stderr)
            assert tests.returncode==0,tests.stderr
            verifier='''from duration import format_duration
for value, expected in [(0,'00:00'),(59,'00:59'),(60,'01:00'),(3599,'59:59'),(3600,'60:00')]:
    assert format_duration(value)==expected
for value in [-1,1.5,'60',True,False]:
    try: format_duration(value)
    except ValueError: pass
    else: raise AssertionError(repr(value))
print('10 independent boundary cases passed')
'''
            independent=subprocess.run([sys.executable,'-c',verifier],cwd=target,capture_output=True,text=True,check=True)
            for path in target.glob('*.py'):
                shutil.copy2(path,folder/path.name)
        save('code-verification.json',{'patch_sha256':task['diff_sha256'],'original_project_unchanged':True,
             'independent_checks':independent.stdout.strip(),'agent_tests_exit_code':tests.returncode,
             'actual_command_count':task.get('command_count'),'independent_review_complete':True})
        files.append({'kind':'code','name':'changes.patch','bytes':len(patch),'sha256':task['diff_sha256']})
    save('verified.json',{'workflow':receipt['workflow'],'job_id':job['id'],'files':files,'pinned_memory_retained':True})


if __name__=='__main__':
    main()
