"""Regressions from a partial family-video task, not just a successful demo."""
import copy
import json
import uuid
import unittest
from unittest.mock import patch, AsyncMock

from backend import coordination
from backend.conversations import build_history, quoted_history
from backend.delivery_harness import explicit_video_constraints
from backend.test_conversations import completed
from backend import test_coordination as fixtures
from backend.test_coordination import plan


class TaskMemoryTests(unittest.TestCase):
    def test_partial_and_cancelled_intent_survive_without_error_traces(self):
        room = 'family-room'
        story = completed('group', {'delivery': {'status': 'partial', 'content': 'Family draft', 'limitations': ['Missing images']}, 'raw': 'PRIVATE_LOG'},
                          id='story', group_room_id=room, project_id='family', prompt='Daughter and baby brother', created_at='2026-10-18T01:00:00+00:00')
        correction = completed('group', None, id='correction', group_room_id=room, project_id='family', status='cancelled', prompt='Need narration and captions', created_at='2026-10-18T02:00:00+00:00')
        current = completed('group', None, id='current', group_room_id=room, project_id='family', prompt='60–120 seconds', created_at='2026-10-18T03:00:00+00:00')
        history = build_history({'story': story, 'correction': correction}, current)
        self.assertEqual([t['job_id'] for t in history['turns']], ['story', 'correction'])
        self.assertEqual(history['turns'][0]['deliverables'][0]['kind'], 'partial_group_delivery')
        self.assertTrue(history['turns'][1]['user_intent_only'])
        self.assertIn('Need narration and captions', quoted_history(history))
        self.assertNotIn('PRIVATE_LOG', quoted_history(history))
        foreign = {**story, 'id': 'foreign', 'project_id': 'other', 'prompt': 'OTHER_PROJECT'}
        self.assertNotIn('OTHER_PROJECT', quoted_history(build_history({'foreign': foreign}, current)))

    def test_explicit_new_task_is_a_persistent_boundary(self):
        old = completed('group', {'messages': [{'content': 'Blue boat'}]}, id='old', group_room_id='r', created_at='2026-10-18T01:00:00+00:00')
        fresh = completed('group', None, id='new', group_room_id='r', group_context_mode='fresh', status='error', prompt='Family story', created_at='2026-10-18T02:00:00+00:00')
        self.assertEqual(build_history({'old': old}, fresh)['turns'], [])
        followup = {**fresh, 'id': 'followup', 'group_context_mode': 'continue', 'created_at': '2026-10-18T03:00:00+00:00'}
        history = build_history({'old': old, 'new': fresh}, followup)
        self.assertEqual([t['job_id'] for t in history['turns']], ['new'])
        self.assertEqual(history['metadata']['task_boundary_job_id'], 'new')
        self.assertNotIn('Blue boat', quoted_history(history))


class DeliveryReliabilityTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.CoordinationTests.setUp
    run_delivery = fixtures.CoordinationTests.run_delivery
    provider = fixtures.CoordinationTests.provider

    def test_user_duration_replaces_invented_plan_and_preserves_criteria(self):
        raw = json.dumps(plan(requirements=[{'id': 'video', 'kind': 'video', 'count': 1,
            'description': '中文字幕，家人角色一致', 'min_duration_seconds': 180, 'max_duration_seconds': 240}]))
        parsed = coordination.parse_plan(raw, coordination.roster(self.job), {'images': 0, 'videos': 1}, 'auto', '最终约 1–2 分钟，需要中文旁白和字幕')
        video = parsed['requirements'][0]
        self.assertEqual((video['min_duration_seconds'], video['max_duration_seconds']), (60, 120))
        self.assertTrue(video['audio_required'] and video['captions_required'])
        self.assertIn('家人角色一致', video['description'])
        self.assertFalse(explicit_video_constraints('视频，不要旁白，不需要字幕').get('audio_required', False))

    def test_video_delivery_keeps_requested_keyframes(self):
        raw = json.dumps(plan(requirements=[{'id': 'frames', 'kind': 'image', 'count': 2, 'description': '2 downloadable keyframes'}]))
        contract = {'images': 0, 'videos': 1}
        parsed = coordination.parse_plan(raw, coordination.roster(self.job), contract, 'video')
        self.assertEqual(contract['images'], 2)
        self.assertEqual({item['kind'] for item in parsed['requirements']}, {'image', 'video'})

    def test_real_video_duration_and_audio_override_model_success(self):
        asset = {'id': 'v', 'kind': 'video', 'sha256': 'same'}
        delivery = {'media': [asset], 'task_spec': {'requirements': [{'id': 'media-video', 'kind': 'video', 'count': 1,
            'min_duration_seconds': 60, 'max_duration_seconds': 120, 'audio_required': True}]},
            'visual_evidence': {'assets': [{'asset_id': 'v', 'sha256': 'same', 'duration_seconds': 52.872, 'audio_streams': 1}]}}
        with patch('backend.delivery_media.store.read', return_value=(asset, b'video')):
            check = coordination.actual_checks(self.job, delivery)[0]
            self.assertFalse(check['met'])
            self.assertIn('52.872', check['reason'])
            delivery['visual_evidence']['assets'][0]['duration_seconds'] = 75
            self.assertTrue(coordination.actual_checks(self.job, delivery)[0]['met'])
            delivery['visual_evidence']['assets'][0]['audio_streams'] = 0
            self.assertFalse(coordination.actual_checks(self.job, delivery)[0]['met'])
            delivery['visual_evidence']['assets'][0].update(audio_streams=1, sha256='different')
            self.assertFalse(coordination.actual_checks(self.job, delivery)[0]['met'])

    async def native_case(self, draft, missing_images=False, provider_name='muse'):
        self.job['group_team_snapshot']['members'].append({'id': 'creator', 'kind': 'muse', 'name': 'Creator', 'role': 'Director'})
        self.job['group_recipient_member_ids'].append('creator')
        if missing_images:
            self.job['group_output_format'] = 'auto'
            self.job['group_media_provider'] = provider_name
        async def provider(job, model, system, prompt, **kwargs):
            if '你是项目统筹者' in system:
                requirements = [{'id': 'pictures', 'kind': 'image', 'count': 2, 'description': '2 keyframes'}] if missing_images else None
                return json.dumps(plan([{'id': 'script', 'member_id': 'writer', 'instruction': 'Write script', 'expected_output': 'Script'},
                    {'id': 'create', 'member_id': 'creator', 'instruction': 'Create video', 'expected_output': 'Actual media', 'depends_on': ['script']}], requirements)), False
            if '你是 作者' in system:
                return draft, False
            if '你是交付审查者' in system and missing_images:
                return json.dumps({'goal_met': False, 'missing_requirements': ['2 actual images missing'],
                    'checks': [{'requirement_id': 'media-image', 'met': False, 'evidence_status': 'verified', 'reason': 'Missing'}], 'revision_instructions': 'Return real images'}), False
            return await self.provider(job, model, system, prompt, **kwargs)
        sent = []
        async def runtime(job, member, prompt, **kwargs):
            sent.append(prompt)
            return {'status': 'complete', 'task_id': str(uuid.uuid4()), 'output': 'Muse reply', 'sources': []}
        with patch('backend.group_delivery_runtime.run_runtime_step', side_effect=runtime), patch('backend.delivery_harness.produce_media', new=AsyncMock()):
            delivery = (await self.run_delivery(provider))['delivery']
        return delivery, sent

    async def test_native_handoff_keeps_script_tail_and_blocks_oversize(self):
        delivery, sent = await self.native_case('x' * 7600 + 'FINAL_SCENE_SENTINEL')
        self.assertIn('FINAL_SCENE_SENTINEL', sent[0])
        self.assertTrue(delivery['steps'][1]['handoff']['complete'])

    async def test_native_handoff_and_review_receive_durable_task_context(self):
        from backend.task_context import quote_task_context
        memory = {'sha256': 'frozen-memory', 'requests': [{'prompt': 'ROOT_PAPER_BOAT'}, {'prompt': 'CORRECTION_SILENT_60S'}]}
        self.job['_task_context'] = memory
        self.job['prompt'] = '继续\n' + quote_task_context(memory)
        delivery, sent = await self.native_case('APPROVED_SCRIPT_END')
        self.assertIn('ROOT_PAPER_BOAT', sent[0])
        self.assertIn('CORRECTION_SILENT_60S', sent[0])
        self.assertIn('APPROVED_SCRIPT_END', sent[0])
        self.assertEqual(delivery['steps'][1]['handoff']['context_sha256'], 'frozen-memory')
        self.assertIn('ROOT_PAPER_BOAT', next(prompt for _, system, prompt in self.calls if '最终交付编辑' in system))

    async def test_oversize_script_never_dispatches_a_truncated_task(self):
        delivery, sent = await self.native_case('x' * 23000 + 'FINAL_SCENE_SENTINEL')
        self.assertEqual(sent, [])
        self.assertEqual(delivery['steps'][1]['status'], 'error')
        self.assertIn('未截断或派发', delivery['steps'][1]['error'])

    async def test_missing_files_schedule_one_repair_without_optional_model_field(self):
        delivery, sent = await self.native_case('Script', missing_images=True)
        self.assertEqual(len(sent), 2)
        self.assertEqual(delivery['workflow']['native_revision_count'], 1)
        self.assertEqual(delivery['steps'][-1]['replace_media_kinds'], ['image'])
        self.assertNotEqual(delivery['status'], 'complete')

    async def test_missing_explicit_provider_images_never_reroute_to_muse(self):
        delivery, sent = await self.native_case('Script', missing_images=True, provider_name='gemini')
        self.assertEqual(len(sent), 1)
        self.assertNotEqual(delivery['status'], 'complete')
