"""Exercise the Tasks extension through a real HTTP socket and official SDK."""
import asyncio
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
import uuid

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from backend import mcp_agent, mcp_tasks, mcp_observation
from backend.harness.models import TeamMember
from backend.harness.store import RunStore
from backend.platform_store import PlatformStore


class McpTasksHttpTests(unittest.IsolatedAsyncioTestCase):
    async def test_server_owned_task_reconnects_with_required_wire_fields_and_single_dispatch(self):
        calls=[]
        created=datetime.now(timezone.utc).isoformat()
        task={'taskId':'opaque:221', 'status':'working', 'createdAt':created,
              'lastUpdatedAt':created, 'ttlMs':3600000, 'pollIntervalMs':1}
        schema={'type':'object','properties':{'prompt':{'type':'string'}},'required':['prompt']}

        async def endpoint(request: Request):
            if request.method != 'POST':
                return Response(status_code=405)
            message=await request.json()
            method=message['method']; params=message.get('params',{})
            if 'id' not in message:
                return Response(status_code=202)
            calls.append(method)
            if method == 'initialize':
                result={'protocolVersion':params['protocolVersion'],'capabilities':{'tools':{}},'serverInfo':{'name':'Tasks wire acceptance','version':'1'}}
            elif method == 'tools/list':
                result={'tools':[{'name':'execute_task','inputSchema':schema}]}
            else:
                self.assertIn(mcp_tasks.EXTENSION, params['_meta']['io.modelcontextprotocol/clientCapabilities']['extensions'])
                self.assertEqual(request.headers['Mcp-Method'],method)
                if method == 'server/discover':
                    result={'capabilities':{'extensions':{mcp_tasks.EXTENSION:{}}}}
                elif method == 'tools/call':
                    self.assertEqual(request.headers['Mcp-Name'],'execute_task')
                    self.assertIn('original constraints',params['arguments']['prompt'])
                    result={'resultType':'task',**task}
                elif method == 'tasks/get':
                    self.assertEqual(params['taskId'],task['taskId'])
                    self.assertEqual(request.headers['Mcp-Name'],task['taskId'])
                    result={'resultType':'complete',**task,'status':'completed',
                            'result':{'content':[{'type':'text','text':'Verified deferred HTTP result'}]}}
                else:
                    self.fail(method)
            return JSONResponse({'jsonrpc':'2.0','id':message['id'],'result':result})

        sock=socket.socket(); sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(Starlette(routes=[Route('/mcp',endpoint,methods=['GET','POST','DELETE'])]),log_level='error',lifespan='off'))
        serving=asyncio.create_task(server.serve(sockets=[sock]))
        try:
            for _ in range(100):
                if server.started: break
                await asyncio.sleep(.01)
            self.assertTrue(server.started)
            with tempfile.TemporaryDirectory() as folder, ExitStack() as patches:
                resources=PlatformStore(Path(folder)/'resources.json')
                config=resources.put('mcp',{'name':'Tasks HTTP acceptance','url':f'http://127.0.0.1:{port}/mcp','transport':'streamable_http','allowed_tools':['execute_task']})
                member=TeamMember(id='worker',name='Worker',kind='mcp',mcp_server_id=config['id'],mcp_tool='execute_task').model_dump(mode='json')
                scope=str(uuid.uuid4()),None,str(uuid.uuid4())
                job={'id':scope[0],'project_id':None,'group_room_id':scope[2],'group_team_snapshot':{'members':[member]}}
                for context in [patch.object(mcp_agent,'resources',resources),patch.object(mcp_agent,'STORE',RunStore(Path(folder)/'runs')),patch('backend.group_delivery_runtime._scope',return_value=scope)]:
                    patches.enter_context(context)
                await mcp_agent.freeze_mcp_members(job)
                prompt='Preserve original constraints and finish the artifact'
                receipt=await mcp_agent.run_mcp_step(job,member,prompt,scope=scope)
                self.assertEqual(receipt['remote_state'],'observing',receipt)
                record=mcp_observation.read_record(receipt['task_id'])
                record['next_poll_at']=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
                mcp_agent.STORE.save(record)
                final=await mcp_observation.poll(job,{'member_id':member['id'],'dispatch_prompt':prompt})
                self.assertEqual(final['output'],'Verified deferred HTTP result')
                repeated=await mcp_agent.run_mcp_step(job,member,prompt,scope=scope)
                self.assertEqual(repeated['status'],'complete')
                self.assertEqual(calls.count('tools/call'),1)
                self.assertEqual(calls.count('tasks/get'),1)
                self.assertGreaterEqual(calls.count('initialize'),3)
        finally:
            server.should_exit=True
            await asyncio.wait_for(serving,5)
            sock.close()
