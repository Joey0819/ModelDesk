from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
import unittest
from unittest.mock import patch

import httpx
from fastapi import HTTPException
from mcp.types import Result
from backend import mcp_agent, mcp_tasks, mcp_observation
from backend.harness.context import revision
from backend import test_mcp_agent


class DurableMcpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await test_mcp_agent.McpAgentTests.asyncSetUp(self)
        self.calls=[]
        self.remote={"resultType":"task","taskId":"task-221", "status":"working", "createdAt":datetime.now(timezone.utc).isoformat(),
                     "lastUpdatedAt":datetime.now(timezone.utc).isoformat(), "ttlMs":3600000, "pollIntervalMs":1}
        self.observed={**self.remote, "resultType":"complete", "status":"completed", "result":{"content":[{"type":"text","text":"Real deferred result"}]}}
        async def send(request, result_type, **kwargs):
            self.calls.append(request.model_dump())
            if request.method == 'server/discover':
                return Result(capabilities={'extensions':{mcp_tasks.EXTENSION:{}}})
            if request.method == 'tools/call':
                return Result(**self.remote)
            if request.method == 'tasks/get':
                if isinstance(self.observed,Exception):
                    raise self.observed
                return Result(**self.observed)
            if request.method in {'tasks/update','tasks/cancel'}:
                return Result(resultType='complete')
            raise AssertionError(request.method)
        self.session.send_request=send
        await mcp_agent.freeze_mcp_members(self.job)
        self.prompt='Complete the original assignment; retain every requirement.'
        self.step={'kind':'mcp','member_id':self.member['id'],'dispatch_prompt':self.prompt}

    async def asyncTearDown(self):
        await test_mcp_agent.McpAgentTests.asyncTearDown(self)

    async def submit(self):
        result=await mcp_agent.run_mcp_step(self.job,self.member,self.prompt,scope=self.scope)
        self.identifier=result['task_id']
        return result

    def due(self):
        record=mcp_observation.read_record(self.identifier)
        record['next_poll_at']=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
        mcp_agent.STORE.save(record)

    def call_count(self,method):
        return sum(call['method']==method for call in self.calls)

    def test_malformed_input_requests_are_rejected_before_ui_or_dispatch(self):
        for value in [None, 1, 'not a request', {'method':'elicitation/create','params':None}]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                mcp_tasks.task_state({**self.remote, 'status':'input_required', 'inputRequests':{'question':value}})

    async def test_acknowledged_handle_survives_restart_and_output_schema_is_checked(self):
        first=await self.submit()
        self.assertEqual(first['remote_state'],'observing')
        self.assertEqual(first['external_task_id'],'task-221')
        self.assertEqual((await self.submit())['external_task_id'],'task-221')
        self.due()
        final=await mcp_observation.poll(self.job,self.step)
        self.assertEqual(final['status'],'complete')
        self.assertEqual(final['output'],'Real deferred result')
        self.assertEqual(self.call_count('tools/call'),1)
        self.assertEqual(self.call_count('tasks/get'),1)
        self.session.call_tool.assert_not_awaited()
        for call in self.calls:
            self.assertIn(mcp_tasks.EXTENSION,call['params']['_meta']['io.modelcontextprotocol/clientCapabilities']['extensions'])

    async def test_disconnection_retries_only_observation_with_backoff(self):
        await self.submit(); self.due()
        self.observed=TimeoutError('server private details')
        first=await mcp_observation.poll(self.job,self.step)
        self.assertEqual(first['status'],'observing')
        self.assertNotIn('private details',str(mcp_agent.public_record(first)))
        await mcp_observation.poll(self.job,self.step)
        self.assertEqual(self.call_count('tasks/get'),1)
        self.observed={**self.remote,'resultType':'complete','status':'completed','result':{'content':[{'type':'text','text':'Recovered'}]}}
        self.due()
        final=await mcp_observation.poll(self.job,self.step)
        self.assertEqual(final['output'],'Recovered')
        self.assertEqual(self.call_count('tools/call'),1)

    async def test_wrong_remote_identity_and_expiry_never_create_new_task(self):
        await self.submit(); self.due()
        self.observed['taskId']='another-task'
        record=await mcp_observation.poll(self.job,self.step)
        self.assertEqual(record['remote_task']['taskId'],'task-221')
        record['remote_task']['ttlMs']=0; record['next_poll_at']=self.remote['createdAt']
        mcp_agent.STORE.save(record)
        final=await mcp_observation.poll(self.job,self.step)
        self.assertEqual(final['status'],'error')
        self.assertIn('期限',final['error'])
        await self.submit()
        self.assertEqual(self.call_count('tools/call'),1)

    async def test_tool_error_inside_completed_task_is_not_success(self):
        await self.submit(); self.due()
        self.observed['result']['isError']=True
        record=await mcp_observation.poll(self.job,self.step)
        self.assertEqual(record['status'],'error')
        self.assertEqual(record['remote_task']['status'],'completed')
        await self.submit()
        self.assertEqual(self.call_count('tools/call'),1)

    async def test_input_requires_user_answer_and_exact_request_hash(self):
        request={'method':'elicitation/create','params':{'mode':'form','message':'Approve this test?','requestedSchema':{'type':'object','properties':{'approved':{'type':'boolean'}},'required':['approved']}}}
        await self.submit(); self.due()
        self.observed={**self.remote,'resultType':'complete','status':'input_required','inputRequests':{'approval':request}}
        record=await mcp_observation.poll(self.job,self.step)
        self.assertEqual(self.call_count('tasks/update'),0)
        self.due()
        await mcp_observation.poll(self.job,self.step)
        self.assertEqual(self.call_count('tasks/get'),1)
        with patch.object(mcp_observation,'resolve',return_value=(self.job,self.step)):
            with self.assertRaises(HTTPException):
                await mcp_observation.act_on_task(self.identifier,mcp_observation.TaskAction(action='accept',request_key='approval',request_sha256='0'*64,content={'approved':True}))
            action=mcp_observation.TaskAction(action='accept',request_key='approval',request_sha256=revision(request),content={'approved':False})
            answered=await mcp_observation.act_on_task(self.identifier,action)
            self.assertEqual(answered['task_observation']['input_responses']['approval']['state'],'acknowledged')
            with self.assertRaises(HTTPException):
                await mcp_observation.act_on_task(self.identifier,action)
        sent=next(call for call in self.calls if call['method']=='tasks/update')
        self.assertEqual(sent['params']['inputResponses'],{'approval':{'action':'accept','content':{'approved':False}}})
        self.assertEqual(self.call_count('tasks/update'),1)
        self.assertEqual(self.call_count('tools/call'),1)

    async def test_cancel_acknowledgement_is_not_cancelled_and_is_not_repeated(self):
        await self.submit()
        with patch.object(mcp_observation,'resolve',return_value=(self.job,self.step)):
            action=mcp_observation.TaskAction(action='cancel_task')
            result=await mcp_observation.act_on_task(self.identifier,action)
            self.assertEqual(result['task_observation']['remote_task']['status'],'working')
            self.assertEqual(result['task_observation']['cancel_requested']['state'],'acknowledged')
            with self.assertRaises(HTTPException):
                await mcp_observation.act_on_task(self.identifier,action)
        self.due()
        self.observed={**self.remote,'resultType':'complete','status':'cancelled'}
        final=await mcp_observation.poll(self.job,self.step)
        self.assertEqual(final['remote_task']['status'],'cancelled')
        self.assertEqual(self.call_count('tasks/cancel'),1)
        self.assertEqual(self.call_count('tools/call'),1)

    async def test_seed_completed_still_fetches_the_final_result(self):
        self.remote['status']='completed'
        await self.submit(); self.due()
        final=await mcp_observation.poll(self.job,self.step)
        self.assertEqual(final['status'],'complete')

    async def test_required_routing_headers(self):
        request=httpx.Request('POST','http://127.0.0.1/mcp',json={'method':'tasks/get','params':{'taskId':'task-221'}})
        await mcp_tasks.routing_headers(request)
        self.assertEqual(request.headers['Mcp-Name'],'task-221')
        self.assertEqual(request.headers['Mcp-Method'],'tasks/get')

    async def test_transport_close_failure_cannot_erase_acknowledged_task(self):
        @asynccontextmanager
        async def broken_close(config):
            yield self.session
            raise RuntimeError('connection closed after acknowledgment')
        with patch.object(mcp_agent,'agent_session',broken_close):
            receipt=await self.submit()
        self.assertEqual(receipt['remote_state'],'observing')
        self.due()
        self.assertEqual((await mcp_observation.poll(self.job,self.step))['status'],'complete')
        self.assertEqual(self.call_count('tools/call'),1)
