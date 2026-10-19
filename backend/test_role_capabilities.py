import asyncio
import copy
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from backend import coordination, groups, role_images, skill_library, muse_bridge
from backend.harness.models import TeamRequest
from backend.member_capabilities import member_kind
from backend.group_delivery_runtime import _finished


class RoleRoutingTests(unittest.TestCase):
    def test_saved_legacy_gpt_image_role_uses_image_capability(self):
        member = {'id': 'artist', 'kind': 'model', 'model': 'gpt-image-2.5', 'name': '绘图者', 'role': '画图'}
        before = copy.deepcopy(member)
        self.assertEqual(member_kind(member), 'image')
        room = {'team_snapshot': {'members': [member, {'id': 'writer', 'model': 'text'}]}}
        self.assertEqual(groups.room_models(room), ['text'])
        self.assertEqual(member, before)
        job = {'group_team_snapshot': room['team_snapshot'], 'group_recipient_member_ids': ['artist'], 'group_broadcast': False}
        roster = coordination.roster(job)
        self.assertEqual(roster[0]['kind'], 'image')
        self.assertEqual(roster[-1]['model'], 'text')

    def test_image_cannot_coordinate_or_receive_text_step(self):
        with self.assertRaises(ValueError):
            TeamRequest(name='a', members=[dict(id='a', name='a', model='text'), dict(id='b', name='b', model='text2')], coordinator_model='gpt-image-2.5')
        plan = {'title':'image','requirements':[{'id':'image','kind':'image','description':'one image'}], 'steps':[{'id':'draw','member_id':'artist','instruction':'draw','expected_output':'image','output_kind':'text'}]}
        with self.assertRaises(ValueError):
            coordination.parse_plan(json.dumps(plan), [dict(id='artist', name='a', kind='image', model='gpt-image-2.5')], {'images':1,'videos':0,'provider':'auto'})

    def test_creative_completion_does_not_require_code_approval(self):
        class Manager:
            def read_patch(self, *args):
                raise AssertionError('creative delivery is not a code merge')
        item = dict(id='task', kind='creative', status='complete', exit_code=0, output='created', project_id=None, room_id='room', member_id='animator', changed_files=[{'path':'film/index.html'}], skill_delivery={'assets':[{'kind':'video'}]})
        result = _finished(Manager(), item, 'codex', None, 'room')
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['skill_delivery'], item['skill_delivery'])


class ImageExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_model_no_chat_and_dependency_evidence(self):
        step = dict(id='draw', image_count=2)
        delivery = dict(media=[], media_tasks=[])
        assets = [dict(id='one',sha256='a',kind='image',provider='openai'),dict(id='two',sha256='b',kind='image',provider='openai')]
        with patch.object(role_images, 'generate_image', AsyncMock(side_effect=assets)) as call:
            await role_images.execute_images({}, dict(id='artist', name='绘图者', model='gpt-image-2.5'), step, 'draw', delivery, lambda:None)
        self.assertTrue(all(c.kwargs['model']=='gpt-image-2.5' for c in call.call_args_list))
        self.assertEqual(step['media'], assets)
        self.assertEqual(step['status'],'complete')

    async def test_failure_keeps_prior_file_and_does_not_substitute_model(self):
        step = dict(id='draw', image_count=1)
        delivery = dict(media=[], media_tasks=[])
        with patch.object(role_images, 'generate_image', AsyncMock(side_effect=ValueError('provider failed'))) as call:
            with self.assertRaises(ValueError):
                await role_images.execute_images({}, dict(id='artist',name='a',model='gpt-image-2.5'),step,'draw',delivery,lambda:None)
        self.assertEqual(call.await_count,1)
        self.assertEqual(delivery['media_tasks'][0]['status'],'error')


class SkillsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.patch=patch.object(skill_library,'DIRECTORY',Path(self.tmp.name));self.patch.start();self.addCleanup(self.patch.stop)

    def test_frozen_skill_is_unchanged_after_edit(self):
        skill_library.put_skill('writer','Writer','Original brief')
        frozen=skill_library.frozen_skills([{'skill_ids':['writer']}])
        skill_library.put_skill('writer','Writer','Changed brief')
        value=skill_library.skill_prompt(['writer'],snapshot=frozen)
        self.assertIn('Original brief',value);self.assertNotIn('Changed brief',value)

    def test_tamper_symlink_and_path_escape_rejected(self):
        with self.assertRaises(HTTPException):skill_library.get_skill('../outside')
        skill_library.put_skill('writer','Writer','content')
        path=Path(self.tmp.name)/'writer/manifest.json';d=json.loads(path.read_text());d['instructions']='tampered';path.write_text(json.dumps(d))
        with self.assertRaises(HTTPException):skill_library.get_skill('writer')
        path.unlink();path.symlink_to('/tmp/nonexistent')
        with self.assertRaises(HTTPException):skill_library.get_skill('writer')

    def test_missing_skill_and_runtime_binding_are_explicit(self):
        with self.assertRaises(HTTPException):skill_library.skill_prompt(['missing'])
        from backend.harness.models import TeamMember
        self.assertEqual(TeamMember(id='muse',name='Muse',kind='muse',skill_ids=['writer']).skill_ids, ['writer'])
        for kind in ['model', 'image']:
            with self.assertRaises(ValueError):TeamMember(id='image',name='Image',kind=kind,model='gpt-image-2.5',skill_ids=['writer'])

    def test_muse_public_status_distinguishes_reply_from_file(self):
        result=muse_bridge.public_task(dict(status='complete',_native_media=[],_native_media_errors=['Video not found']))
        self.assertEqual(result['media_delivery'],dict(images=0,videos=0,errors=['Video not found']))
        self.assertNotIn('_native_media',result)

class ArtifactGraphTests(unittest.TestCase):
    def test_image_video_dependency_and_skill_executor_are_enforced(self):
        members=[dict(id='artist',name='Artist',kind='image',model='gpt-image-2.5'),dict(id='animator',name='Animator',kind='codex',skill_ids=['lemo-opuscar'])]
        raw=dict(title='Film',requirements=[dict(id='r',kind='video',count=1,description='video')],steps=[
            dict(id='draw',member_id='artist',instruction='draw',expected_output='image',output_kind='image'),
            dict(id='animate',member_id='animator',instruction='animate',expected_output='video',output_kind='video')])
        contract=dict(images=1,videos=1,provider='auto')
        parsed=coordination.parse_plan(json.dumps(raw),members,contract,'video')
        self.assertEqual(parsed['steps'][1]['depends_on'],['draw'])
        self.assertEqual(contract['video_provider'],'skill')
        members[1]['skill_ids']=[]
        with self.assertRaises(ValueError):coordination.parse_plan(json.dumps(raw),members,dict(images=1,videos=1,provider='auto'),'video')

    def test_render_evidence_is_not_lost_behind_long_cli_text(self):
        from backend.group_delivery import execution_evidence
        record=dict(output='x'*8000,skill_delivery=dict(renderer='canvas-film',decoding={'full_decode':True},duration_seconds=6,assets=[dict(id='video')]))
        evidence=execution_evidence([record])[0]
        self.assertTrue(evidence['output_truncated'])
        self.assertTrue(evidence['platform_render']['decoding']['full_decode'])

    def test_discussion_uses_frozen_role_skill_only(self):
        from backend.group_chat import discussion_messages
        snapshot={'writer':dict(id='writer',sha256='digest',instructions='Frozen instruction')}
        job=dict(prompt='task',_skill_snapshots=snapshot)
        member=dict(id='author',name='Author',model='gpt',skill_ids=['writer'])
        text=discussion_messages(job,'gpt',[],member)[0]['content']
        self.assertIn('Frozen instruction',text)
        self.assertNotIn('Frozen instruction',discussion_messages(job,'gpt',[],dict(id='other',name='Other',model='gpt'))[0]['content'])

    def test_legacy_image_model_rejected_by_text_workflow(self):
        from backend.harness.models import RunRequest
        with self.assertRaises(ValueError):
            RunRequest(goal='Task',members=[dict(id='a',name='A',model='gpt-image-2.5'),dict(id='b',name='B',model='text')])

class CreativeSourceTests(unittest.TestCase):
    def test_animation_sources_allow_embedded_images_but_never_modify_frozen_inputs(self):
        from backend.group_tasks import TaskManager, scan_workspace, manifest_digest
        with tempfile.TemporaryDirectory() as tmp:
            manager=TaskManager(Path(tmp))
            item={'id':str(uuid.uuid4()),'kind':'creative'}
            directory=manager.task_directory(item['id']);baseline=directory/'baseline';workspace=directory/'repo'
            baseline.mkdir(parents=True);workspace.mkdir()
            (baseline/'reference.txt').write_text('frozen');(workspace/'reference.txt').write_text('frozen')
            item['base_snapshot_sha256']=manifest_digest(scan_workspace(baseline))
            (workspace/'index.html').write_text('<canvas></canvas>'+' '*(1024*1024+1))
            manager.freeze(item,workspace)
            self.assertIsNone(item['patch_sha256'])
            self.assertTrue(item['creative_source_manifest_sha256'])
            (workspace/'reference.txt').write_text('changed')
            with self.assertRaises(ValueError):manager.freeze(item,workspace)
