import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from backend import image_brief, role_images
from backend.media_providers import MAX_PROMPT_CHARS, MediaProviderError


class ImageBriefTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.member = dict(id='artist', name='绘图者', model='gpt-image-2.5')
        self.step = dict(id='keyframes', image_count=1)
        self.delivery = dict(media=[], media_tasks=[], model='coordinator')
        self.long_source = '用户目标：狮子与蚂蚁。\n' + '审核后的剧本内容。' * 2000 + '\n结尾：和解，一起回家。'
        self.asset = dict(id='picture', sha256='image-sha', kind='image', provider='openai')

    async def execute(self, source=None):
        await role_images.execute_images({}, self.member, self.step, source or self.long_source, self.delivery, lambda: None)

    async def test_full_long_review_becomes_brief_then_exact_model_image(self):
        with patch.object(image_brief, 'model_output', AsyncMock(return_value=('狮子与蚂蚁从争吵到和解的四格绘本。', False))) as editor, \
                patch.object(role_images, 'generate_image', AsyncMock(return_value=self.asset)) as generate:
            await self.execute()
        self.assertEqual(editor.await_args.args[1], 'coordinator')
        self.assertEqual(editor.await_args.args[3], self.long_source)
        self.assertTrue(editor.await_args.args[3].endswith('和解，一起回家。'))
        self.assertEqual(editor.await_args.kwargs['image_parts'], [])
        self.assertLess(len(generate.await_args.args[1]), MAX_PROMPT_CHARS)
        self.assertIn('和解', generate.await_args.args[1])
        self.assertEqual(generate.await_args.kwargs['model'], 'gpt-image-2.5')
        self.assertEqual(self.step['status'], 'complete')
        self.assertEqual(self.delivery['media_tasks'][0]['status'], 'complete')
        self.assertEqual(self.step['image_brief']['source_characters'], len(self.long_source))

    async def test_retry_reuses_brief_and_replaces_duplicate_error_cards(self):
        old = dict(id='role-keyframes-1', kind='image', model='gpt-image-2.5', status='error', error='old error')
        self.delivery['media_tasks'] = [dict(old), dict(old)]
        with patch.object(image_brief, 'model_output', AsyncMock(return_value=('完整画面说明', False))) as editor, \
                patch.object(role_images, 'generate_image', AsyncMock(side_effect=[MediaProviderError('图像服务暂时不可用'), self.asset])):
            with self.assertRaises(MediaProviderError):
                await self.execute()
            self.assertEqual(self.delivery['media_tasks'][0]['error'], '图像服务暂时不可用')
            await self.execute()
        self.assertEqual(editor.await_count, 1)
        self.assertEqual(len(self.delivery['media_tasks']), 1)
        task = self.delivery['media_tasks'][0]
        self.assertEqual([a['status'] for a in task['attempts']], ['error', 'complete'])
        self.assertEqual(task['status'], 'complete')
        self.assertNotIn('error', task)

    async def test_changed_input_or_tampered_brief_cannot_reuse_cache(self):
        with patch.object(image_brief, 'model_output', AsyncMock(return_value=('画面说明', False))) as editor:
            args = ({}, self.member, self.step)
            await image_brief.prepare_image_prompt(*args, self.long_source, self.delivery, lambda: None)
            self.step['image_brief']['prompt'] = 'tampered'
            await image_brief.prepare_image_prompt(*args, self.long_source, self.delivery, lambda: None)
            await image_brief.prepare_image_prompt(*args, self.long_source + '新结尾', self.delivery, lambda: None)
        self.assertEqual(editor.await_count, 3)

    async def test_incomplete_or_oversize_brief_never_calls_image_provider(self):
        for brief, truncated in [('incomplete', True), ('x' * 12001, False), ('   ', False)]:
            with self.subTest(truncated=truncated, length=len(brief)), \
                    patch.object(image_brief, 'model_output', AsyncMock(return_value=(brief, truncated))), \
                    patch.object(role_images, 'generate_image', AsyncMock()) as generate:
                with self.assertRaisesRegex(MediaProviderError, '尚未调用绘图模型'):
                    await self.execute()
                generate.assert_not_awaited()
                self.assertIn('尚未调用绘图模型', self.delivery['media_tasks'][0]['error'])

    async def test_short_prompt_does_not_call_text_model_and_cancellation_is_not_failure(self):
        with patch.object(image_brief, 'model_output', AsyncMock()) as editor, \
                patch.object(role_images, 'generate_image', AsyncMock(side_effect=asyncio.CancelledError())):
            with self.assertRaises(asyncio.CancelledError):
                await self.execute('A lion and an ant')
            editor.assert_not_awaited()
        self.assertEqual(self.delivery['media_tasks'][0]['status'], 'cancelled')

    async def test_unknown_provider_error_does_not_expose_credentials_in_card(self):
        with patch.object(role_images, 'generate_image', AsyncMock(side_effect=ValueError('private-token'))):
            with self.assertRaises(ValueError):
                await self.execute('Draw')
        self.assertNotIn('private-token', str(self.delivery['media_tasks']))
