"""Verified image parts survive the real native SDK request paths without calls."""
import asyncio
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import httpx

from backend.vision import with_images, rewrite_request, user_content, public_summary, SafeWorkerStream
from backend import council
from backend.group_chat import discussion_messages

PARTS = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,cGl4ZWxz", "detail": "auto"}}]
ROOT = Path(__file__).resolve().parents[1]


class VisionTests(unittest.TestCase):
    def test_tool_roundtrips_keep_images_and_all_assistant_tool_fields(self):
        original = [{"role": "user", "content": "根据图片回答"},
                    {"role": "assistant", "content": None, "tool_calls": [{"id": "call1", "type": "function", "function": {"name": "math", "arguments": "{}"}}]},
                    {"role": "tool", "tool_call_id": "call1", "content": "2"}]
        updated = with_images(original, PARTS)
        self.assertEqual(updated[0]["content"][1], PARTS[0])
        self.assertEqual(updated[1:], original[1:])
        self.assertEqual(with_images(updated, PARTS), updated)
        self.assertEqual(original[0]["content"], "根据图片回答")

    def test_no_remote_images_or_silent_no_user_downgrade(self):
        with self.assertRaisesRegex(ValueError, "本地"):
            user_content("look", [{"type": "image_url", "image_url": {"url": "https://example.com/image.png"}}])
        with self.assertRaisesRegex(ValueError, "用户消息"):
            with_images([{"role": "assistant", "content": "only assistant"}], PARTS)
        with self.assertRaisesRegex(ValueError, "端点"):
            rewrite_request(httpx.Request("POST", "https://example.com/v1/chat/completions", json={"messages": []}), PARTS)

    def test_wire_request_preserves_tool_fields_headers_and_reports_only_counts(self):
        payload = {"model": "selected-model", "messages": [{"role": "user", "content": "look"}], "tools": [{"type": "function"}], "response_format": {"type": "json_object"}}
        report = {"image_requests": 0, "requests": []}
        original = httpx.Request("POST", "http://127.0.0.1:8317/v1/chat/completions", headers={"Authorization": "Bearer local-oauth"}, json=payload)
        updated = rewrite_request(original, PARTS, report)
        self.assertEqual(json.loads(updated.content)["messages"][0]["content"][1], PARTS[0])
        self.assertEqual(json.loads(updated.content)["tools"], payload["tools"])
        self.assertEqual(updated.headers["Content-Length"], str(len(updated.content)))
        self.assertEqual(json.loads(original.content), payload)
        self.assertEqual(report, {"image_requests": 1, "requests": [{"model": "selected-model", "image_count": 1}]})

    def test_council_all_three_phases_send_actual_image_parts(self):
        calls = []
        async def parallel(models, messages, **kwargs):
            calls.append(messages)
            return {name: {"content": "FINAL RANKING:\n1. Response A"} for name in models}
        async def single(model, messages, **kwargs):
            calls.append(messages)
            return {"content": "实际综合"}
        async def run():
            with patch.object(council, "query_models_parallel", side_effect=parallel), patch.object(council, "query_model", side_effect=single):
                first = await council.stage1_collect_responses("看图", ["a"], image_parts=PARTS)
                second, _ = await council.stage2_collect_rankings("看图", first, ["a"], image_parts=PARTS)
                await council.stage3_synthesize_final("看图", first, second, "a", image_parts=PARTS)
        asyncio.run(run())
        self.assertEqual(len(calls), 3)
        for messages in calls:
            self.assertEqual(messages[-1]["content"][-1], PARTS[0])

    def test_group_relay_retains_same_actual_images_while_quoting_other_models(self):
        job = {"prompt": "看图", "context_turns": 12, "_conversation_context_prepared": True}
        with patch("backend.group_chat.image_content_parts", return_value=PARTS):
            messages = discussion_messages(job, "a", [{"speaker": "b", "content": "other actual opinion"}])
        self.assertIn("other actual opinion", messages[-1]["content"][0]["text"])
        self.assertEqual(messages[-1]["content"][1], PARTS[0])

    def test_public_summaries_and_split_framework_logs_never_export_image_bytes(self):
        source = {"content": PARTS, "output": "source " + PARTS[0]["image_url"]["url"], "path": "/Users/example/data/workbench/attachment-inputs/job/id.png"}
        saved = json.dumps(public_summary(source))
        self.assertNotIn("base64", saved); self.assertNotIn("attachment-inputs", saved)
        output = io.StringIO(); safe = SafeWorkerStream(output)
        safe.write("first data:image/png;base64,"); safe.flush(); safe.write("cGl4ZWxz\nsecond\n")
        self.assertNotIn("cGl4ZWxz", output.getvalue())
        self.assertIn("second", output.getvalue())
        safe.write("x" * 65000); safe.write("\n")
        self.assertIn("过长原生日志已省略", output.getvalue())

    def native(self, sdk, source):
        python = ROOT.parent / "llm-tools" / (".venv-" + sdk) / "bin/python"
        if not python.exists(): self.skipTest("native SDK runtime unavailable")
        completed = subprocess.run([str(python), "-c", source], cwd=ROOT, capture_output=True, text=True, timeout=90)
        self.assertEqual(completed.returncode, 0, completed.stderr[-4000:] + completed.stdout[-1000:])

    def test_installed_camel_chat_clone_and_async_use_real_native_vision_wire(self):
        self.native("camel", r'''
import asyncio,json,httpx
from camel.agents import ChatAgent
from camel.models import ModelFactory
from camel.types import ModelPlatformType
from backend.workers.workflow import camel_clients
parts=[{"type":"image_url","image_url":{"url":"data:image/png;base64,cGl4ZWxz"}}]
seen=[]; report={"image_requests":0,"requests":[]}
def handle(request):
    payload=json.loads(request.content); seen.append(payload)
    assert any(isinstance(m.get("content"),list) and any(p.get("type")=="image_url" for p in m["content"]) for m in payload["messages"])
    return httpx.Response(200,json={"id":"mock","object":"chat.completion","created":1,"model":payload["model"],"choices":[{"index":0,"message":{"role":"assistant","content":"实际模型返回"},"finish_reason":"stop"}],"usage":{"prompt_tokens":20,"completion_tokens":5,"total_tokens":25}})
transport=httpx.MockTransport(handle)
client,async_client=camel_clients("model-a",transport,transport,image_parts=parts,vision_report=report)
model=ModelFactory.create(model_platform=ModelPlatformType.OPENAI_COMPATIBLE_MODEL,model_type="model-a",url="http://127.0.0.1:8317/v1",api_key="local-oauth",client=client,async_client=async_client,model_config_dict={"max_tokens":64})
agent=ChatAgent("观察材料",model=model)
assert agent.step("look").msgs[0].content=="实际模型返回"
assert agent.clone().step("review").msgs
assert asyncio.run(agent.astep("next")).msgs
assert len(seen)==3 and report["image_requests"]==3
assert "base64" not in json.dumps(report)
''')

    def test_installed_crewai_agent_task_and_native_flow_keep_vision_requests(self):
        self.native("crewai", r'''
import contextlib,io,json,tempfile,httpx
from pathlib import Path
from unittest.mock import patch
from backend.workers.workflow import crewai,crewai_flow
parts=[{"type":"image_url","image_url":{"url":"data:image/png;base64,cGl4ZWxz"}}]
seen=[]
def handle(self,request):
    payload=json.loads(request.content); seen.append(payload)
    assert any(isinstance(m.get("content"),list) and any(p.get("type")=="image_url" for p in m["content"]) for m in payload["messages"])
    return httpx.Response(200,request=request,json={"id":"mock","object":"chat.completion","created":1,"model":payload["model"],"choices":[{"index":0,"message":{"role":"assistant","content":"Final Answer: 实际看图结论"},"finish_reason":"stop"}],"usage":{"prompt_tokens":20,"completion_tokens":5,"total_tokens":25}})
workflow={"process":"sequential","max_iter":3,"planning":False,"memory":False,"agents":[{"id":"a","role":"观察员","goal":"观察","backstory":"依据材料","model":"model-a","tools":[]}],"tasks":[{"id":"t","agent":"a","description":"辨认图片","expected_output":"结论","context":[]}]}
with tempfile.TemporaryDirectory() as directory,patch('backend.attachment_io.image_content_parts',return_value=parts),patch.object(httpx.HTTPTransport,'handle_request',handle),contextlib.redirect_stdout(io.StringIO()):
    for mode,run in [('crewai',crewai),('crewai_flow',crewai_flow)]:
        output=Path(directory)/(mode+'.json'); data={"mode":mode,"models":["model-a"],"prompt":"看图片","workflow":workflow}
        run(data,output); result=json.loads(output.read_text())
        assert result['partial'] is False and result['vision']['image_requests']>=1
        assert 'base64' not in output.read_text()
assert len(seen)>=2
''')

    def test_native_workforce_worker_clone_keeps_image_transport_and_typed_finish(self):
        self.native("camel", r'''
import asyncio,contextlib,io,json,tempfile,httpx
from pathlib import Path
from unittest.mock import patch
from camel.societies.workforce.utils import TaskResult
from backend.workers.workflow import camel_clients,workforce_agent
parts=[{"type":"image_url","image_url":{"url":"data:image/png;base64,cGl4ZWxz"}}]
seen=[]
def handle(request):
    body=json.loads(request.content);seen.append(body)
    assert any(isinstance(m.get("content"),list) and any(p.get("type")=="image_url" for p in m["content"]) for m in body["messages"])
    return httpx.Response(200,json={"id":"mock","object":"chat.completion","created":1,"model":body["model"],"choices":[{"index":0,"message":{"role":"assistant","content":'{"content":"真实观察结果","failed":false}'},"finish_reason":"stop"}],"usage":{"prompt_tokens":20,"completion_tokens":5,"total_tokens":25}})
native_clients=camel_clients
transport=httpx.MockTransport(handle)
spec={"id":"a","role":"观察员","goal":"看图","backstory":"依据材料","model":"model-a","tools":[]}
data={"models":["model-a"],"prompt":"看图","workflow":{"max_iter":3,"agents":[spec]}}
def clients(name,**kwargs):return native_clients(name,transport,transport,**kwargs)
with tempfile.TemporaryDirectory() as directory,patch('backend.attachment_io.image_content_parts',return_value=parts),patch('backend.workers.workflow.camel_clients',side_effect=clients),contextlib.redirect_stdout(io.StringIO()):
    agent=workforce_agent(data,spec,{},Path(directory)/'out.json').clone()
    response=asyncio.run(agent.astep('观察图片',response_format=TaskResult))
    assert response.msgs[0].parsed.failed is False
assert seen and data['_vision_report']['image_requests']==len(seen)
''')

    def test_native_crew_memory_storage_is_isolated_by_project_without_changing_global_scope(self):
        self.native("crewai", r'''
import contextlib,hashlib,io
from unittest.mock import patch
from backend.workers.workflow import native_memory
seen=[]
def memory(**options): seen.append(options);return object()
with patch('crewai.Memory',side_effect=memory),patch('backend.workers.workflow.Path.mkdir'),patch('backend.workers.workflow.Path.chmod'),contextlib.redirect_stdout(io.StringIO()):
    for project in ['project-a','project-b',None]:
        native_memory({'workflow':{'memory_scope':'default','project_id':project}},'model')
assert seen[0]['storage']!=seen[1]['storage'] and seen[0]['root_scope']!=seen[1]['root_scope']
assert seen[2]['storage'].endswith(hashlib.sha256(b'default').hexdigest())
assert seen[2]['root_scope']=='/default'
''')

    def test_reviewed_sdk_factories_observe_images_and_plain_custom_scripts_remain_unverified(self):
        self.native("camel", r'''
import json,httpx
from unittest.mock import patch
from backend.workers.sdk_script import sdk_namespace
parts=[{"type":"image_url","image_url":{"url":"data:image/png;base64,cGl4ZWxz"}}]
data={"sdk":"camel","models":["a"],"prompt":"观察","context":"实际文本材料","attachment_manifest":{"items":[{"id":"image","name":"figure.png","sha256":"digest"}]}}
with patch('backend.attachment_io.image_content_parts',return_value=parts):
    namespace,report=sdk_namespace(data)
assert namespace['CONTEXT']=='实际文本材料' and namespace['ATTACHMENTS'][0]['id']=='image'
with patch('backend.attachment_io.image_content_parts',return_value=parts):
    list_namespace,_=sdk_namespace({**data,'attachment_manifest':[{'id':'second','name':'image.png'}]})
assert list_namespace['ATTACHMENTS'][0]['id']=='second'
assert report['image_requests']==0
assert namespace['ATTACHMENT_CONTENT']()[1]==parts[0]
assert report['content_helpers']==1 and report['image_requests']==0
def handle(self,request):
    body=json.loads(request.content)
    assert body['messages'][0]['content'][1]==parts[0]
    return httpx.Response(200,request=request,json={'id':'mock','object':'chat.completion','created':1,'model':'a','choices':[{'index':0,'message':{'role':'assistant','content':'观察结果'},'finish_reason':'stop'}]})
with patch.object(httpx.HTTPTransport,'handle_request',handle):
    answer=namespace['VISION_CLIENT']().chat.completions.create(model='a',messages=[{'role':'user','content':'看图'}])
assert answer.choices[0].message.content=='观察结果' and report['image_requests']==1
''')


if __name__ == "__main__": unittest.main()
