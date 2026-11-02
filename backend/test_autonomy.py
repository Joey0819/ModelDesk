import asyncio
import copy
import json
import tempfile
import unittest
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from starlette.requests import Request

from backend import autonomy, coordination, document_delivery, group_delivery, groups, workbench
from backend.test_coordination import plan


class AutonomyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.manager = workbench.JobManager(Path(self.temp.name) / "jobs")
        self.supervisor = autonomy.AutonomySupervisor(self.manager, retry_delay=0)
        room = str(uuid.uuid4())
        self.job = {"id": str(uuid.uuid4()), "project_id": None, "conversation_id": room, "group_room_id": room,
                    "mode": "group", "models": ["model-a", "model-b"], "prompt": "写完整文章", "status": "running", "events": [],
                    "created_at": workbench.now(), "group_harness_version": 1, "group_delivery_mode": "auto", "group_broadcast": True,
                    "group_runtime_allowed": True, "group_native_allowed": True, "group_output_format": "text", "group_document_formats": [],
                    "group_recipient_member_ids": ["writer", "reader"], "autonomy": autonomy.initial_state(),
                    "group_team_snapshot": {"members": [
                        {"id": "writer", "kind": "model", "name": "作者", "model": "model-a", "role": "写作"},
                        {"id": "reader", "kind": "model", "name": "审阅者", "model": "model-b", "role": "核查"}]}}
        self.manager.jobs[self.job["id"]] = self.job
        self.manager.wakeups[self.job["id"]] = asyncio.Event()
        self.calls = []
        self.addCleanup(patch.stopall)
        patch.object(groups.store, "get", side_effect=lambda *args: {"team_snapshot": copy.deepcopy(self.job["group_team_snapshot"])}).start()
        self.documents = document_delivery.DocumentStore(Path(self.temp.name).resolve() / "documents")
        patch.object(document_delivery, "store", self.documents).start()

    async def provider(self, job, model, system, prompt, **kwargs):
        self.calls.append(system)
        if "你是项目统筹者" in system:
            return json.dumps(plan()), False
        if "最终交付编辑" in system:
            return json.dumps({"title": "文章", "content": "# 成果\n\n真实正文。", "missing_outputs": []}), False
        if "你是交付审查者" in system:
            return json.dumps({"goal_met": True, "missing_requirements": [], "checks": [
                {"requirement_id": "body", "met": True, "evidence_status": "verified", "reason": "正文完整"}], "revision_instructions": ""}), False
        return "完成的具体内容", False

    async def initial(self, provider=None):
        with patch.object(group_delivery, "model_output", side_effect=provider or self.provider):
            self.job["result"] = await coordination.run(self.manager, copy.deepcopy(self.job))
        self.job["status"] = "complete"
        self.manager.save(self.job)
        return self.job["result"]["delivery"]

    async def failure(self):
        async def failing(job, model, system, prompt, **kwargs):
            if system.startswith("你是 审阅者"):
                raise RuntimeError("temporary provider failure")
            return await self.provider(job, model, system, prompt, **kwargs)
        delivery = await self.initial(failing)
        self.assertNotEqual(delivery["status"], "complete")
        return delivery

    async def settle(self):
        await asyncio.gather(*list(self.manager.tasks.values()))
        await self.supervisor.tick()

    async def test_safe_model_retry_preserves_ancestor_and_frozen_context(self):
        delivery = await self.failure()
        ancestor = copy.deepcopy(delivery["steps"][0])
        await self.supervisor.tick()
        self.assertEqual(self.job["autonomy"]["state"], "retry_wait")
        no_media = AsyncMock(side_effect=AssertionError("automatic continuation must not generate media"))
        no_native = AsyncMock(side_effect=AssertionError("automatic continuation must not dispatch native work"))
        with patch.object(group_delivery, "model_output", side_effect=self.provider), patch("backend.delivery_harness.produce_media", no_media), patch("backend.group_delivery_runtime.run_runtime_step", no_native):
            await self.supervisor.tick()
            self.assertEqual(self.job["autonomy"]["attempts"], 1)
            await self.settle()
        self.assertEqual(delivery["steps"][0], ancestor)
        self.assertEqual(len(delivery["steps"][1]["attempts"]), 2)
        self.assertEqual(self.job["autonomy"]["state"], "complete")
        self.assertFalse(self.job["autonomy"]["read"])
        self.assertIsNotNone(self.job["autonomy"]["notification_id"])
        self.assertIsNone(autonomy.completion_problem(self.job))
        no_media.assert_not_awaited()
        no_native.assert_not_awaited()
        saved = json.loads((self.manager.directory / (self.job["id"] + ".json")).read_text())
        self.assertEqual(saved["autonomy"]["attempts"], 1)

    async def test_file_packaging_retry_calls_no_model_or_agent(self):
        self.job["group_document_formats"] = ["html"]
        with patch("backend.document_exports.render_document", side_effect=RuntimeError("renderer unavailable")):
            delivery = await self.initial()
        self.assertTrue(coordination.document_retry_ready(self.job, delivery))
        original = copy.deepcopy({key: delivery[key] for key in ["steps", "versions", "reviews", "content"]})
        no_model = AsyncMock(side_effect=AssertionError("packaging-only must not call model"))
        no_native = AsyncMock(side_effect=AssertionError("packaging-only must not call Agent"))
        with patch.object(group_delivery, "model_output", no_model), patch("backend.group_delivery_runtime.run_runtime_step", no_native):
            await self.supervisor.tick()
            await self.supervisor.tick()
            await self.settle()
        self.assertEqual(self.job["autonomy"]["state"], "complete")
        self.assertEqual(original, {key: delivery[key] for key in original})
        entry, raw = self.documents.read(self.job, delivery["documents"][0]["id"])
        self.assertGreater(len(raw), 100)
        self.assertEqual(entry["format"], "html")
        no_model.assert_not_awaited()
        no_native.assert_not_awaited()

    async def test_restart_collects_completed_mcp_then_finishes_without_duplicate_dispatch(self):
        from types import SimpleNamespace
        from mcp.types import CallToolResult, TextContent, Tool
        from backend import mcp_agent, group_delivery_runtime
        from backend.harness.models import TeamMember
        from backend.harness.store import RunStore
        from backend.platform_store import PlatformStore
        resources = PlatformStore(Path(self.temp.name) / 'resources.json')
        config = resources.put('mcp', dict(name='Recovery fixture', url='http://127.0.0.1:8371/mcp',
                                          transport='streamable_http', allowed_tools=['execute_task']))
        member = TeamMember(id='analyst', name='Analyst', kind='mcp', mcp_server_id=config['id'],
                            mcp_tool='execute_task', role='Return actual measurement').model_dump(mode='json')
        self.job['group_team_snapshot']['members'].insert(1, member)
        self.job['group_recipient_member_ids'].insert(1, 'analyst')
        self.job['group_document_formats'] = ['html']
        native_store = RunStore(Path(self.temp.name) / 'mcp')
        tool = Tool(name='execute_task', inputSchema={'type':'object','properties':{'prompt':{'type':'string'}},'required':['prompt']})
        session = SimpleNamespace(list_tools=AsyncMock(return_value=SimpleNamespace(tools=[tool])),
                                  call_tool=AsyncMock(return_value=CallToolResult(content=[TextContent(type='text',text='Actual measurement: 42')])) )
        @asynccontextmanager
        async def connect(_):
            yield session
        async def provider(job, model, system, prompt, **kwargs):
            if '你是项目统筹者' in system:
                value=plan()
                value['steps'].insert(1, {'id':'measure','member_id':'analyst','instruction':'Measure original draft',
                                         'expected_output':'Actual measured count','depends_on':[value['steps'][0]['id']],'output_kind':'text'})
                value['steps'][-1]['depends_on']=['measure']
                return json.dumps(value), False
            return await self.provider(job,model,system,prompt,**kwargs)
        original = group_delivery_runtime.run_runtime_step
        async def crash_after_receipt(*args, **kwargs):
            result=await original(*args, **kwargs)
            self.assertEqual(result['status'], 'complete')
            raise RuntimeError('Fault fixture: parent lost after MCP saved its result')
        with patch.object(mcp_agent,'resources',resources), patch('backend.harness.context.resources',resources), patch.object(mcp_agent,'STORE',native_store), \
             patch.object(mcp_agent,'agent_session',connect):
            with patch.object(group_delivery_runtime,'run_runtime_step',crash_after_receipt):
                delivery=await self.initial(provider)
            self.assertNotEqual(delivery['status'],'complete')
            session.call_tool.assert_awaited_once()
            self.manager=workbench.JobManager(self.manager.directory)
            self.manager.recover()
            self.job=self.manager.jobs[self.job['id']]
            self.supervisor=autonomy.AutonomySupervisor(self.manager,retry_delay=0)
            # Restoring the saved MCP receipt needs neither network discovery nor
            # another tool invocation. Pure downstream models may still run.
            with patch.object(mcp_agent,'agent_session',side_effect=AssertionError('must collect locally')), \
                 patch.object(group_delivery,'model_output',side_effect=provider):
                await self.supervisor.tick()
                self.assertEqual(self.job['autonomy']['state'],'retry_wait',self.job['autonomy'])
                await self.supervisor.tick()
                await self.settle()
            self.assertEqual(self.job['autonomy']['state'],'complete',self.job['autonomy'])
            step=next(item for item in self.job['result']['delivery']['steps'] if item['kind']=='mcp')
            self.assertEqual(step['operation_receipt']['mode'],'collect_only')
            self.assertEqual(step['output'],'Actual measurement: 42')
            session.call_tool.assert_awaited_once()
            entry=self.job['result']['delivery']['documents'][0]
            _,raw=self.documents.read(self.job,entry['id'])
            self.assertIn('真实正文',raw.decode())

    async def test_unsafe_remaining_steps_and_tools_are_not_replayed(self):
        await self.failure()
        original = copy.deepcopy(self.job)
        for kind, tools in [("mcp", []), ("muse", []), ("image", []), ("codex", []), ("claude", []), ("model", ["web_search"])]:
            self.job.clear()
            self.job.update(copy.deepcopy(original))
            self.job["result"]["delivery"]["steps"][1]["kind"] = kind
            self.job["tools"] = tools
            await self.supervisor.tick()
            self.assertEqual(self.job["autonomy"]["state"], "needs_attention", kind)
            self.assertEqual(self.job["autonomy"]["attempts"], 0)
            self.assertFalse(self.manager.tasks)

    async def test_untouched_dependent_agent_can_start_but_any_attempt_or_receipt_blocks_it(self):
        from backend.operation_recovery import unstarted_operation
        from backend import group_tasks
        from types import SimpleNamespace
        await self.failure()
        step=self.job['result']['delivery']['steps'][1]
        member=self.job['group_team_snapshot']['members'][1]
        member['kind']='codex'
        step.update(kind='codex',status='blocked',attempts=[])
        with patch.object(group_tasks,'manager',SimpleNamespace(records={})):
            self.assertEqual(unstarted_operation(self.job,step)['mode'],'initial_dispatch')
            self.assertIsNone(autonomy.continuation_blocker(self.job))
            step['attempts']=[{'status':'running'}]
            self.assertIsNone(unstarted_operation(self.job,step))
            step['attempts']=[]
            group_tasks.manager.records['existing']={'parent_job_id':self.job['id'],'member_id':member['id']}
            self.assertIsNone(unstarted_operation(self.job,step))
            group_tasks.manager.records.clear()
            self.job['group_native_allowed']=False
            self.assertIsNone(unstarted_operation(self.job,step))

    async def test_missing_parent_media_does_not_block_verified_child_collection(self):
        await self.failure()
        delivery = self.job['result']['delivery']
        step = delivery['steps'][1]
        step.update(kind='muse', status='error', attempts=[{'status': 'error'}])
        delivery['task_spec']['requirements'].append({'id': 'video', 'kind': 'video'})
        missing = [{'requirement_id': 'video', 'met': False, 'reason': 'actual files 0/1'}]
        with patch('backend.coordination.actual_checks', return_value=missing), \
             patch('backend.operation_recovery.unstarted_operation', return_value=None), \
             patch('backend.operation_recovery.completed_operation', return_value={'mode': 'collect_only'}):
            self.assertIsNone(autonomy.continuation_blocker(self.job))
            # After collection, an absent real file still blocks completion.
            step['status'] = 'complete'
            self.assertIn('actual files 0/1', autonomy.continuation_blocker(self.job))
            step['status'] = 'error'
            # A lost/unknown child receipt cannot authorize a new submission.
            with patch('backend.operation_recovery.completed_operation', return_value=None):
                self.assertIn('未确认完成', autonomy.continuation_blocker(self.job))

    async def test_two_automatic_continuations_max(self):
        await self.failure()
        async def always_fail(*args, **kwargs):
            raise RuntimeError("provider remains unavailable")
        with patch.object(group_delivery, "model_output", side_effect=always_fail):
            for attempt in [1, 2]:
                await self.supervisor.tick()
                await self.supervisor.tick()
                await self.settle()
                self.assertEqual(self.job["autonomy"]["attempts"], attempt)
            await self.supervisor.tick()
        self.assertEqual(self.job["autonomy"]["state"], "needs_attention")
        self.assertFalse(self.manager.tasks)

        self.assertEqual(self.job["result"]["delivery"]["workflow"]["resume_count"], 2)

    async def test_recovered_prerequisite_dispatches_untouched_downstream_agent_once(self):
        from backend import group_tasks
        from types import SimpleNamespace
        import hashlib
        self.job['group_team_snapshot']['members'].append({'id':'builder','kind':'codex','name':'工程师','role':'完成实际工作'})
        self.job['group_recipient_member_ids'].append('builder')
        first=True
        async def provider(job,model,system,prompt,**kwargs):
            nonlocal first
            if '你是项目统筹者' in system:
                value=plan();value['steps'].append({'id':'build','member_id':'builder','instruction':'按前置要求完成工作','expected_output':'实际成果','depends_on':['facts']})
                return json.dumps(value),False
            if first and system.startswith('你是 审阅者'):
                first=False
                raise RuntimeError('temporary upstream failure')
            return await self.provider(job,model,system,prompt,**kwargs)
        dispatched=AsyncMock(return_value={'status':'complete','output':'真实正文','output_sha256':hashlib.sha256('真实正文'.encode()).hexdigest()})
        with patch.object(group_tasks,'manager',SimpleNamespace(records={})), patch('backend.group_delivery_runtime.run_runtime_step',dispatched), patch.object(group_delivery,'model_output',side_effect=provider):
            delivery=await self.initial(provider)
            self.assertEqual(delivery['steps'][-1]['attempts'],[])
            dispatched.assert_not_awaited()
            await self.supervisor.tick(); await self.supervisor.tick(); await self.settle()
            dispatched.assert_awaited_once()
            self.assertFalse(dispatched.await_args.kwargs.get('observe_only'))
            self.assertEqual(self.job['autonomy']['state'],'complete')
        self.assertEqual(self.job["result"]["delivery"]["workflow"]["resume_count"], 1)

    async def test_tampered_content_and_missing_documents_cannot_be_complete(self):
        self.job["group_document_formats"] = ["html"]
        delivery = await self.initial()
        original = delivery["content"]
        delivery["content"] += " unreviewed alteration"
        await self.supervisor.tick()
        self.assertEqual(self.job["autonomy"]["state"], "needs_attention")
        delivery["content"] = original
        delivery["documents"] = []
        await self.supervisor.tick()
        self.assertEqual(self.job["autonomy"]["state"], "needs_attention")
        self.assertIn("文件", self.job["autonomy"]["reason"])

    async def test_actual_document_bytes_are_rechecked(self):
        self.job["group_document_formats"] = ["html"]
        delivery = await self.initial()
        path = Path(self.temp.name).resolve() / "documents" / self.job["id"] / delivery["documents"][0]["id"] / "content"
        path.write_bytes(b"corrupted artifact bytes")
        await self.supervisor.tick()
        self.assertEqual(self.job["autonomy"]["state"], "needs_attention")
        self.assertIn("文件", self.job["autonomy"]["reason"])

    async def test_persisted_retry_survives_new_manager_without_duplicate(self):
        await self.failure()
        await self.supervisor.tick()
        self.manager = workbench.JobManager(self.manager.directory)
        self.manager.recover()
        self.job = self.manager.jobs[self.job["id"]]
        self.supervisor = autonomy.AutonomySupervisor(self.manager, retry_delay=0)
        with patch.object(group_delivery, "model_output", side_effect=self.provider):
            await asyncio.gather(self.supervisor.tick(), self.supervisor.tick())
            await self.settle()
        self.assertEqual(self.job["autonomy"]["state"], "complete")
        self.assertEqual(self.job["autonomy"]["attempts"], 1)

    async def test_changed_inputs_pause_cancel_capacity_and_preplan(self):
        await self.failure()
        with patch.object(coordination, "load_inputs", side_effect=HTTPException(409, "资料已改变")):
            await self.supervisor.tick()
        self.assertEqual(self.job["autonomy"]["reason"], "资料已改变")
        await self.supervisor.tick()
        for index in range(4):
            self.manager.jobs[str(index)] = {"status": "running"}
        await self.supervisor.tick()
        self.assertEqual(self.job["autonomy"]["attempts"], 0)
        self.assertEqual(self.job["autonomy"]["state"], "retry_wait")
        for index in range(4):
            self.manager.jobs.pop(str(index))
        await self.supervisor.mutate(self.job["id"], None, "pause")
        await self.supervisor.tick()
        self.assertEqual(self.job["autonomy"]["state"], "paused")
        self.assertFalse(self.manager.tasks)
        self.job["status"] = "cancelled"
        await self.supervisor.tick()
        self.assertEqual(self.job["autonomy"]["state"], "cancelled")
        self.job.update(status="interrupted", autonomy=autonomy.initial_state(), result=None)
        await self.supervisor.tick()
        self.assertEqual(self.job["autonomy"]["state"], "needs_attention")
        self.assertIn("原始输入", self.job["autonomy"]["reason"])

    async def test_scoped_rows_read_and_local_only_inbox(self):
        await self.initial()
        await self.supervisor.tick()
        project = str(uuid.uuid4())
        other = copy.deepcopy(self.job)
        other.update(id=str(uuid.uuid4()), project_id=project)
        self.manager.jobs[other["id"]] = other
        self.assertEqual([row["id"] for row in self.supervisor.rows()["tasks"]], [self.job["id"]])
        self.assertEqual([row["id"] for row in self.supervisor.rows(project)["tasks"]], [other["id"]])
        self.assertEqual(self.supervisor.rows(all_projects=True)["counts"]["total"], 2)
        with self.assertRaises(HTTPException) as raised:
            await self.supervisor.mutate(other["id"], None, "read")
        self.assertEqual(raised.exception.status_code, 404)
        row = await self.supervisor.mutate(self.job["id"], None, "read")
        self.assertTrue(row["read"])
        request = Request({"type": "http", "method": "GET", "scheme": "http", "path": "/api/autonomy/inbox",
                           "server": ("127.0.0.1", 8001), "client": ("127.0.0.1", 123), "headers": [(b"x-modeldesk-remote", b"phone")]})
        with self.assertRaises(HTTPException) as raised:
            await autonomy.inbox(request)
        self.assertEqual(raised.exception.status_code, 403)

    async def test_shutdown_is_distinct_from_explicit_user_cancel(self):
        await self.failure()
        await self.supervisor.tick()
        waiting = asyncio.Event()
        async def provider(*args, **kwargs):
            waiting.set()
            await asyncio.Event().wait()
        with patch.object(group_delivery, "model_output", side_effect=provider):
            await self.supervisor.tick()
            await waiting.wait()
            await self.manager.shutdown()
        self.assertEqual(self.job["status"], "interrupted")
        self.assertEqual(self.job["autonomy"]["stop_reason"], "service_shutdown")
        self.assertFalse(self.manager.tasks)
        self.job["status"] = "running"
        await self.manager.cancel(self.job["id"])
        await self.manager.shutdown()
        await self.supervisor.tick()
        self.assertEqual(self.job["status"], "cancelled")
        self.assertEqual(self.job["autonomy"]["state"], "cancelled")

    async def test_cancel_terminal_partial_job_stops_scheduled_automatic_retry(self):
        await self.failure()
        await self.supervisor.tick()
        self.assertEqual(self.job["status"], "complete")
        self.assertEqual(self.job["autonomy"]["state"], "retry_wait")
        await self.manager.cancel(self.job["id"])
        await self.supervisor.tick()
        self.assertEqual(self.job["autonomy"]["state"], "cancelled")
        self.assertEqual(self.job["autonomy"]["attempts"], 0)
        self.assertFalse(self.manager.tasks)
        self.assertIn("用户停止", autonomy.continuation_blocker(self.job))
        saved = workbench.JobManager(self.manager.directory)
        saved.recover()
        await autonomy.AutonomySupervisor(saved, retry_delay=0).tick()
        self.assertEqual(saved.jobs[self.job["id"]]["autonomy"]["state"], "cancelled")
        self.assertFalse(saved.tasks)

    async def test_background_mode_is_not_a_legacy_job_field(self):
        self.assertFalse(groups.MessageRequest(prompt="hello").background_mode)
        with self.assertRaises(HTTPException) as raised:
            await groups.send_message("unused", groups.MessageRequest(prompt="hello", background_mode=True), None)
        self.assertEqual(raised.exception.status_code, 422)

    async def test_bad_record_or_disk_failure_does_not_stop_other_tasks(self):
        await self.initial()
        bad = copy.deepcopy(self.job)
        bad.update(id=str(uuid.uuid4()), result=None)
        self.manager.jobs = {bad["id"]: bad, self.job["id"]: self.job}
        original_emit = self.manager.emit
        def emit(job, *args, **kwargs):
            if job["id"] == bad["id"]:
                raise OSError("cannot write this job")
            return original_emit(job, *args, **kwargs)
        with patch.object(self.manager, "emit", side_effect=emit), self.assertLogs(autonomy.logger, level="WARNING"):
            await self.supervisor.tick()
        self.assertEqual(self.job["autonomy"]["state"], "complete")

    async def test_pause_stops_future_continuation_but_current_result_is_notified(self):
        await self.supervisor.mutate(self.job["id"], None, "pause")
        await self.initial()
        await self.supervisor.tick()
        self.assertFalse(self.job["autonomy"]["enabled"])
        self.assertEqual(self.job["autonomy"]["state"], "complete")
        self.assertFalse(self.job["autonomy"]["read"])


if __name__ == "__main__":
    unittest.main()
