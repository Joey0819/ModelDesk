import copy
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from backend import native_skills


def skill(text, **fields):
    return dict(id='director', name='Director', instructions=text, sha256=native_skills.digest(text), **fields)


class NativeSkillTests(unittest.IsolatedAsyncioTestCase):
    def task(self, text='Maintain the approved visual continuity.', **fields):
        return dict(prompt='Create a 10-second movie; no narration.', _skill_snapshots={'director': skill(text, **fields)}, _skill_model='coordinator')

    async def test_short_guidance_is_exact_scoped_and_hash_checked(self):
        task = self.task()
        with patch.object(native_skills, 'model_output', AsyncMock()) as model:
            await native_skills.prepare_native_skills(task)
        model.assert_not_called()
        prompt = native_skills.native_prompt(task)
        self.assertTrue(prompt.startswith(task['prompt']))
        self.assertIn('Maintain the approved visual continuity.', prompt)
        self.assertEqual(task['skill_application']['mode'], 'role_instructions')
        task['_skill_guidance'] += 'changed'
        with self.assertRaises(HTTPException): native_skills.native_prompt(task)

    async def test_long_renderer_is_adapted_without_modifying_goal_or_snapshot(self):
        task = self.task('Complete source.' * 3000, renderer='canvas-film')
        before = copy.deepcopy(task)
        with patch.object(native_skills, 'model_output', AsyncMock(return_value=('Preserve framing and review continuity.', False))) as model:
            await native_skills.prepare_native_skills(task)
        self.assertEqual(task['prompt'], before['prompt'])
        self.assertEqual(task['_skill_snapshots'], before['_skill_snapshots'])
        self.assertIn(before['_skill_snapshots']['director']['instructions'], model.call_args.args[3])
        self.assertEqual(model.call_args.kwargs['image_parts'], [])
        self.assertEqual(task['skill_application']['prepared_by'], 'coordinator')
        self.assertFalse(task['skill_application']['native_plugin_installed'])
        self.assertLessEqual(len(native_skills.native_prompt(task)), 22000)

    async def test_missing_model_overlong_brief_and_truncated_brief_fail_closed(self):
        for reply in [('x' * 7000, False), ('incomplete', True), ('', False)]:
            task = self.task('x' * 9000)
            with patch.object(native_skills, 'model_output', AsyncMock(return_value=reply)):
                with self.assertRaises(HTTPException): await native_skills.prepare_native_skills(task)
            with self.assertRaises(HTTPException): native_skills.native_prompt(task)
        task = self.task('x' * 9000); task['_skill_model'] = None
        with self.assertRaises(HTTPException): await native_skills.prepare_native_skills(task)
        task = self.task(); task['prompt'] = 'x' * 21900
        with self.assertRaises(HTTPException): await native_skills.prepare_native_skills(task)

    async def test_only_bound_frozen_sources_are_used_and_missing_source_rejected(self):
        source = {'director': skill('Frozen'), 'other': skill('Another member')}
        result = native_skills.freeze_native_skills({'skill_ids': ['director']}, source)
        source['director']['instructions'] = 'Changed'
        self.assertEqual(list(result), ['director'])
        self.assertEqual(result['director']['instructions'], 'Frozen')
        for snapshot in [source, {}]:
            with self.assertRaises(HTTPException): native_skills.freeze_native_skills({'skill_ids': ['director']}, snapshot)
