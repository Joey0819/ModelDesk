import asyncio
from contextlib import asynccontextmanager
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import uuid

from mcp.types import CallToolResult, TextContent, ImageContent, Tool
from pydantic import ValidationError

from backend import mcp_agent
from backend.harness.models import TeamMember
from backend.harness.context import capture_revisions, revision
from backend.harness.store import RunStore
from backend.platform_store import PlatformStore, Resource


class McpAgentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.resources = PlatformStore(Path(self.folder.name) / 'resources.json')
        self.config = self.resources.put('mcp', dict(name='Agent', url='http://127.0.0.1:8371/mcp',
                                    transport='streamable_http', allowed_tools=['execute_task']))
        self.member = TeamMember(id='researcher', name='Researcher', kind='mcp', mcp_server_id=self.config['id'],
                                 mcp_tool='execute_task', role='Research the assigned task').model_dump(mode='json')
        self.scope = str(uuid.uuid4()), None, str(uuid.uuid4())
        self.job = dict(id=self.scope[0], project_id=None, group_room_id=self.scope[2],
                        group_team_snapshot={'members': [self.member]})
        self.tool = Tool(name='execute_task', inputSchema={'type': 'object', 'properties': {'prompt': {'type': 'string'}}, 'required': ['prompt']})
        self.session = SimpleNamespace(list_tools=AsyncMock(return_value=SimpleNamespace(tools=[self.tool])),
            call_tool=AsyncMock(return_value=CallToolResult(content=[TextContent(type='text', text='Actual result')], isError=False)))
        @asynccontextmanager
        async def session(_config):
            yield self.session
        self.patches = [patch.object(mcp_agent, 'resources', self.resources),
                        patch.object(mcp_agent, 'STORE', RunStore(Path(self.folder.name) / 'tasks')),
                        patch.object(mcp_agent, 'agent_session', session),
                        patch('backend.group_delivery_runtime._scope', return_value=self.scope)]
        for value in self.patches:
            value.start()
        await mcp_agent.freeze_mcp_members(self.job)

    async def asyncTearDown(self):
        for value in reversed(self.patches):
            value.stop()
        self.folder.cleanup()

    async def run_step(self, prompt='Original goal, correction and upstream final draft'):
        return await mcp_agent.run_mcp_step(self.job, self.member, prompt, scope=self.scope)

    async def test_actual_output_and_durable_replay_protection(self):
        result = await self.run_step()
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['output'], 'Actual result')
        self.assertEqual(result['dispatch_count'], 1)
        self.assertEqual((await self.run_step())['task_id'], result['task_id'])
        self.session.call_tool.assert_awaited_once()
        sent = self.session.call_tool.await_args.args
        self.assertEqual(sent[0], 'execute_task')
        self.assertIn('upstream final draft', sent[1]['prompt'])
        with self.assertRaisesRegex(ValueError, '交接不一致'):
            await self.run_step('silently changed task')

    async def test_timeout_never_replays_after_recovery(self):
        self.session.call_tool.side_effect = TimeoutError('contains secret provider URL')
        first = await self.run_step()
        self.assertEqual(first['status'], 'blocked')
        self.assertEqual(first['remote_state'], 'uncertain')
        self.assertNotIn('secret', first['error'])
        second = await self.run_step()
        self.assertEqual(second['task_id'], first['task_id'])
        self.session.call_tool.assert_awaited_once()

    async def test_concurrent_same_member_dispatches_once(self):
        a, b = await asyncio.gather(self.run_step(), self.run_step())
        self.assertEqual(a['task_id'], b['task_id'])
        self.session.call_tool.assert_awaited_once()

    async def test_cancellation_and_crash_window_never_repeat_side_effect(self):
        dispatched = asyncio.Event()
        async def pending(*args):
            dispatched.set()
            await asyncio.sleep(60)
        self.session.call_tool.side_effect = pending
        task = asyncio.create_task(self.run_step())
        await dispatched.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        resumed = await self.run_step()
        self.assertEqual(resumed['remote_state'], 'uncertain')
        path = mcp_agent.STORE.directory / (resumed['task_id'] + '.json')
        record = json.loads(path.read_text())
        record['status'] = 'dispatching'
        mcp_agent.STORE.save(record)
        self.assertEqual((await self.run_step())['remote_state'], 'uncertain')
        self.session.call_tool.assert_awaited_once()

    async def test_persisted_output_tampering_does_not_promote_or_replay(self):
        result = await self.run_step()
        path = mcp_agent.STORE.directory / (result['task_id'] + '.json')
        record = json.loads(path.read_text())
        record['output'] = 'Invented final result'
        mcp_agent.STORE.save(record)
        with self.assertRaisesRegex(ValueError, '校验失败'):
            await self.run_step()
        self.session.call_tool.assert_awaited_once()

    async def test_tool_error_is_not_a_success_and_is_not_replayed(self):
        self.session.call_tool.return_value = CallToolResult(content=[TextContent(type='text', text='failed')], isError=True)
        result = await self.run_step()
        self.assertEqual(result['status'], 'error')
        self.assertFalse(result['output'])
        await self.run_step()
        self.session.call_tool.assert_awaited_once()

    async def test_changed_connection_and_schema_block_before_send(self):
        self.resources.put('mcp', {**{key: self.config[key] for key in Resource.model_fields}, 'url': 'http://127.0.0.1:9999/mcp'}, self.config['id'])
        with self.assertRaisesRegex(ValueError, '连接或能力已变化'):
            await self.run_step()
        self.session.call_tool.assert_not_awaited()
        await mcp_agent.freeze_mcp_members(self.job)
        self.tool.inputSchema['properties']['prompt']['maxLength'] = 5
        result = await self.run_step()
        self.assertEqual(result['status'], 'error')
        self.session.call_tool.assert_not_awaited()

    async def test_scope_and_tool_allowlist_are_required(self):
        self.resources.put('mcp', {**{key: self.config[key] for key in Resource.model_fields}, 'allowed_tools': []}, self.config['id'])
        with self.assertRaises(Exception):
            await mcp_agent.freeze_mcp_members(self.job)
        self.session.call_tool.assert_not_awaited()
        self.resources.put('mcp', {**{key: self.config[key] for key in Resource.model_fields}, 'project_id': str(uuid.uuid4())}, self.config['id'])
        with self.assertRaises(Exception):
            await mcp_agent.freeze_mcp_members(self.job)

    async def test_schema_requires_single_string_handoff_and_remote_refs_fail_closed(self):
        for schema in [{'type': 'object', 'properties': {'prompt': {'type': 'integer'}}},
                       {'type': 'object', 'properties': {'prompt': {'type': 'string'}}, 'required': ['token']}]:
            with self.assertRaises(ValueError):
                mcp_agent.validate_schema(schema, 'prompt')
        schema = {'type': 'object', 'properties': {'prompt': {'type': 'string', '$ref': 'https://untrusted.invalid/schema'}}}
        with self.assertRaises(Exception):
            mcp_agent.validate_schema(schema, 'prompt', 'text')

    async def test_untrusted_media_not_promoted_and_no_output_truncation(self):
        self.session.call_tool.return_value = CallToolResult(content=[TextContent(type='text', text='A result'),
            ImageContent(type='image', data='ZmFrZQ==', mimeType='image/png')])
        result = await self.run_step()
        self.assertIn('尚未导入', result['output'])
        self.assertNotIn('ZmFrZQ', json.dumps(result))
        with self.assertRaisesRegex(ValueError, '超过 80000'):
            mcp_agent.actual_text(CallToolResult(content=[TextContent(type='text', text='a' * 80001)]))
        with self.assertRaisesRegex(ValueError, '没有返回文本成果'):
            mcp_agent.actual_text(CallToolResult(content=[ImageContent(type='image', data='ZmFrZQ==', mimeType='image/png')]))

    async def test_frozen_role_skills_and_resource_snapshot(self):
        self.member['skill_ids'] = ['research-method']
        instruction = 'Verify every conclusion against the supplied source.'
        skill = {'id': 'research-method', 'name': 'Research method', 'instructions': instruction,
                 'sha256': __import__('hashlib').sha256(instruction.encode()).hexdigest()}
        result = await mcp_agent.run_mcp_step(self.job, self.member, 'Task with context', scope=self.scope,
                                              skill_snapshot={'research-method': skill})
        self.assertEqual(result['status'], 'complete')
        self.assertIn(instruction, self.session.call_tool.await_args.args[1]['prompt'])
        self.assertEqual(result['skill_application']['skills'][0]['id'], 'research-method')
        revisions = capture_revisions(self.job, self.resources)
        self.assertEqual(revisions, [{'kind': 'mcp', 'id': self.config['id'], 'digest': revision(self.config)}])

    async def test_roster_model_validation_and_legacy_serialization(self):
        legacy = TeamMember(id='author', name='Author', model='model').model_dump(mode='json')
        self.assertNotIn('mcp_server_id', legacy)
        with self.assertRaises(ValidationError):
            TeamMember(id='bad', name='Bad', kind='mcp')
        with self.assertRaises(ValidationError):
            TeamMember(id='bad', name='Bad', kind='model', model='model', mcp_tool='execute_task')

