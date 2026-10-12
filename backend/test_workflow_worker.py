"""Check finite tool output, private runtime bridge and actual native Flow routing."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

from backend.workers.workflow import (arithmetic, branch_decision, lexical_embeddings, runtime_functions,
                                      record_workforce_event, finish_workforce_result, workforce_capabilities,
                                      workforce_failure)


class WorkerTests(unittest.TestCase):
    def test_failed_task_keeps_partial_diagnostic_and_never_becomes_answer(self):
        from types import SimpleNamespace
        result = {"tasks": [], "worker_roles": {"worker-a": {"agent": "reader", "role": "研究员", "model": "a", "tools": []}}}
        record_workforce_event(result, {"event_type": "task_created", "task_id": "part1", "description": "核实原帖"})
        record_workforce_event(result, {"event_type": "task_started", "task_id": "part1", "worker_id": "worker-a"})
        record_workforce_event(result, {"event_type": "task_started", "task_id": "part2", "worker_id": "worker-a"})
        record_workforce_event(result, {"event_type": "task_created", "task_id": "part3", "description": "最后审稿"})
        failed_text = "没有可用的联网检索工具，无法核实原帖。证据缺口保留。"
        record_workforce_event(result, {"event_type": "task_failed", "task_id": "part1", "worker_id": "worker-a",
                                        "error_message": failed_text, "metadata": {"failure_count": 1}})
        task = SimpleNamespace(id="parent", state=SimpleNamespace(value="FAILED"), result=failed_text)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/"partial.json"
            with self.assertRaisesRegex(RuntimeError, "未配置 web_search/web_fetch"):
                finish_workforce_result(result, task, output)
            persisted = json.loads(output.read_text())
        self.assertTrue(persisted["partial"])
        self.assertEqual(persisted["answer"], "")
        self.assertEqual(persisted["failure"]["kind"], "capability_missing")
        self.assertEqual(persisted["failure"]["worker_role"], "研究员")
        self.assertEqual(persisted["failure"]["description"], "核实原帖")
        self.assertEqual([item["status"] for item in persisted["tasks"]], ["failed", "interrupted", "not_started"])
        self.assertEqual(persisted["tasks"][0]["output"], failed_text)

    def test_root_native_task_is_trace_only_and_recovered_subtask_can_complete(self):
        from types import SimpleNamespace
        result = {"main_task_id": "root", "tasks": [], "worker_roles": {}}
        record_workforce_event(result, {"event_type": "task_created", "task_id": "root", "description": "main"})
        self.assertEqual(result["tasks"], [])
        self.assertEqual(result["workforce_events"][0]["task_id"], "root")
        record_workforce_event(result, {"event_type": "task_failed", "task_id": "child", "error_message": "HTTP408"})
        record_workforce_event(result, {"event_type": "task_completed", "task_id": "child", "result_summary": "real completed output"})
        with tempfile.TemporaryDirectory() as directory:
            finish_workforce_result(result, SimpleNamespace(id="root", state=SimpleNamespace(value="DONE"), result="actual final"), Path(directory)/"output.json")
        self.assertFalse(result["partial"])
        self.assertEqual(result["tasks"][0]["status"], "complete")
        self.assertNotIn("failure", result["tasks"][0])
        self.assertNotIn("failure", result)
        self.assertEqual(result["answer"], "actual final")

    def test_capabilities_list_actual_tools_and_do_not_invent_network_access(self):
        prompt = workforce_capabilities([{"id": "reader", "role": "研究员", "tools": []},
                                         {"id": "reviewer", "role": "审稿人", "tools": ["web_fetch"]}])
        self.assertIn('"tools": []', prompt)
        self.assertIn('"tools": ["web_fetch"]', prompt)
        self.assertIn("不能假定角色能自行联网", prompt)
        self.assertIn("必须报告失败或缺口", prompt)

    def test_unverified_source_retains_actual_evidence_and_failure_precedence(self):
        from types import SimpleNamespace
        source = {"agent": "researcher", "role": "研究员", "model": "gpt-6.1-sol", "tools": ["web_search", "web_fetch"]}
        evidence = "案例证据包｜指定 Reddit 帖子核查失败\n实际联网检索已完成：返回公开资料；原帖被 challenge 拦截。\n关键证据缺失，不能声称原帖已经核实。"
        result = {"tasks": [], "tool_events": [{"tool": "web_search", "output": "实际检索资料"}],
                  "worker_roles": {"reader": source}, "answer": "", "partial": True}
        record_workforce_event(result, {"event_type": "task_failed", "task_id": "verify", "worker_id": "reader", "error_message": evidence})
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output.json"
            with self.assertRaisesRegex(RuntimeError, "来源未核实"):
                finish_workforce_result(result, SimpleNamespace(id="main", state=SimpleNamespace(value="FAILED"), result=evidence), output)
            stored = json.loads(output.read_text())
        self.assertEqual(stored["failure"]["kind"], "source_unverified")
        self.assertEqual(stored["tasks"][0]["output"], evidence)
        self.assertEqual(stored["tool_events"], result["tool_events"])
        self.assertTrue(stored["partial"])
        self.assertEqual(stored["answer"], "")
        self.assertNotIn("权限", stored["failure"]["reason"])
        for prefix, expected in [("HTTP 408 ", "upstream_timeout"), ("HTTP 502 ", "upstream_connection"),
                                 ("原生 TaskResult 结构化输出无效 ", "structured_output_invalid"),
                                 ("没有可用的联网检索工具 ", "capability_missing")]:
            self.assertEqual(workforce_failure("verify", prefix + evidence, source=source)["kind"], expected)
        self.assertEqual(workforce_failure("generic", "写作任务未完成。", source=source)["kind"], "task_failed")

    def test_native_task_finish_keeps_tool_evidence_and_rejects_truncation_and_coercion(self):
        python = Path(__file__).resolve().parents[2] / "llm-tools/.venv-camel/bin/python"
        if not python.exists():
            self.skipTest("isolated CAMEL runtime is unavailable")
        script = r'''
import asyncio, contextlib, io, json, tempfile
from pathlib import Path
from unittest.mock import patch
import httpx
from camel.models import ModelFactory
from camel.types import ModelPlatformType
from camel.toolkits import FunctionTool
from camel.societies.workforce.utils import TaskResult
from camel.agents import ChatAgent
from backend.workers.workflow import workforce_agent, camel_clients, camel_options, text_stats, workforce_failure, runtime_functions

general=camel_options({"workflow":{"max_iter":8}},{"model":"gemini-3.8-flash-high","tools":[]})
assert general["step_timeout"]==240 and general["max_iteration"]==8
assert general["tool_execution_timeout"]==40

def completion(content, finish="stop", tool=False, tool_name="text_stats", arguments='{"text":"HTML {verified-tool-evidence}"}'):
 message={"role":"assistant","content":content}
 if tool:
  message["tool_calls"]=[{"id":"call_fixture","type":"function","function":{"name":tool_name,"arguments":arguments}}]
 return {"id":"fixture","object":"chat.completion","created":0,"model":"gemini-3.8-flash-high","choices":[{"index":0,"message":message,"finish_reason":finish}]}

async def case(kind):
 requests=[];clients=[]
 def handler(request):
  body=json.loads(request.content);requests.append(body)
  typed=bool(body.get("response_format"))
  if not typed:
   if kind=="tools":return httpx.Response(200,json=completion(None,"tool_calls",True))
   if kind=="source_unverified":return httpx.Response(200,json=completion(None,"tool_calls",True,"web_search",'{"query":"verify-fixture","max_results":1}'))
   if kind=="large":return httpx.Response(200,json=completion("😀"*19000))
   if kind=="explicit_failed":return httpx.Response(200,json=completion(json.dumps({"content":"原帖未能核实，要求未完成","failed":True},ensure_ascii=False)))
   return httpx.Response(200,json=completion('<article>{"nested":"HTML braces"}</article>'))
  if kind=="tools":
   assert "tools" not in body or body["tools"]==[],body
   assert "verified-tool-evidence" in body["messages"][-1]["content"],body
   assert "characters" in body["messages"][-1]["content"],body
   assert "original task retained" in body["messages"][-1]["content"],body
  if kind=="latest":
   dossier=json.loads(body["messages"][-1]["content"])
   serialized=json.dumps(dossier,ensure_ascii=False)
   assert "LATEST_VERIFIED_EVIDENCE" in serialized,serialized[-500:]
   assert "OLDEST_EVIDENCE" not in serialized,serialized[:500]
   assert dossier["evidence_truncated"] is True
  if kind.startswith("http"):
   return httpx.Response(int(kind[4:]),json={"error":{"message":"private upstream body fixture","type":"server_error"}})
  if kind=="length":return httpx.Response(200,json=completion('{"content":"unfinished '+"x"*80,"length"))
  if kind=="coercion":return httpx.Response(200,json=completion('{"content":"unsafe coerced flag","failed":"false"}'))
  if kind=="missing_failed":return httpx.Response(200,json=completion('{"content":"missing explicit completion flag"}'))
  if kind=="source_unverified":return httpx.Response(200,json=completion(json.dumps({"content":"指定 Reddit 帖子核查失败：已执行工具，但原始来源未核实。","failed":True},ensure_ascii=False)))
  return httpx.Response(200,json=completion(json.dumps({"content":"actual final evidence <article>{ok}</article>","failed":False})))
 def options(data,spec,max_tokens=3072):
  sync,asynchronous=camel_clients(spec["model"],sync_transport=httpx.MockTransport(handler),async_transport=httpx.MockTransport(handler))
  # Assertions exercise the worker's error classification, not SDK backoff.
  sync.max_retries=asynchronous.max_retries=0
  clients.extend([sync,asynchronous])
  model=ModelFactory.create(model_platform=ModelPlatformType.OPENAI_COMPATIBLE_MODEL,model_type=spec["model"],url="http://127.0.0.1:8317/v1",api_key="local-oauth",model_config_dict={"max_tokens":max_tokens},client=sync,async_client=asynchronous)
  tools=[]
  if spec.get("tools"):tools=[FunctionTool(runtime_functions(data)["web_search"] if kind=="source_unverified" else text_stats)]
  return {"model":model,"tools":tools,"max_iteration":data["workflow"]["max_iter"],"retry_attempts":0,"step_timeout":240}
 spec={"id":"reviewer","role":"审稿人","goal":"保留证据","backstory":"fixture","model":"gemini-3.8-flash-high","tools":["web_search"] if kind=="source_unverified" else ["text_stats"] if kind=="tools" else []}
 data={"workflow":{"max_iter":2 if kind in {"tools","source_unverified"} else 1},"runtime":{"token":"private-source-fixture"}}
 result={"tasks":[],"answer":"","partial":True}
 with tempfile.TemporaryDirectory() as directory, patch("backend.workers.workflow.camel_options",side_effect=options):
  output=Path(directory)/"result.json"
  agent=workforce_agent(data,spec,result,output)
  assert agent.step_timeout is None
  if kind=="tools":
   agent=agent.clone()
   assert type(agent).__name__=="TaskAgent"
  if kind=="latest":
   original_context=agent.memory.get_context
   def expanded_context():
    context,tokens=original_context()
    return context+[{"role":"tool","content":"OLDEST_EVIDENCE "+"x"*12000}]+[{"role":"tool","content":"middle "+"x"*12000} for _ in range(7)]+[{"role":"tool","content":"LATEST_VERIFIED_EVIDENCE"}],tokens
   agent.memory.get_context=expanded_context
  stream=io.StringIO()
  step_budgets=[]
  native_astep=ChatAgent.astep
  async def tracked_astep(self,*args,**kwargs):
   step_budgets.append((type(self).__name__,self.step_timeout))
   return await native_astep(self,*args,**kwargs)
  class BridgeResponse:
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def read(self,maximum):return b'{"results":[{"url":"https://example.com/fixture","title":"Actual fixture search result"}]}'
  with contextlib.redirect_stdout(stream),patch.object(ChatAgent,"astep",new=tracked_astep),patch("urllib.request.urlopen",return_value=BridgeResponse()) as bridge_request:
   try:
    response=await agent.astep("original task retained",TaskResult)
   except RuntimeError as error:
    assert kind in {"length","coercion","missing_failed","http400","http403","http408","http500"},(kind,error)
    diagnostic=workforce_failure("fixture",str(error))
    expected={"http400":"upstream_request","http403":"upstream_access","http408":"upstream_timeout","http500":"upstream_connection"}.get(kind,"structured_output_invalid")
    assert diagnostic["kind"]==expected,(kind,diagnostic)
    assert "private upstream body fixture" not in str(error)
    assert result["partial"] is True and result["answer"]==""
    assert any(item["phase"]=="research" and item["raw"] for item in result["format_diagnostics"])
    if kind=="length":
     last=result["format_diagnostics"][-1]
     assert last["phase"]=="finalization_error" and last["finish_reasons"]==["length"] and last["raw"].startswith('{"content":"unfinished'),last
    if kind.startswith("http"):
     assert result["format_diagnostics"][-1]["status_code"]==int(kind[4:])
   else:
    assert kind not in {"length","coercion","missing_failed","http400","http403","http408","http500"},kind
    assert type(response.msg.parsed.failed) is bool
    if kind=="explicit_failed":
     assert response.msg.parsed.failed is True and len(requests)==1
    elif kind=="source_unverified":
     assert response.msg.parsed.failed is True and len(requests)==3
     assert workforce_failure("verify",response.msg.parsed.content,source={"tools":["web_search","web_fetch"]})["kind"]=="source_unverified"
     assert len(response.info["tool_calls"])==2
     assert bridge_request.call_count==2
     assert bridge_request.call_args.args[0].full_url.endswith("/web_search")
     assert "Actual fixture search result" in str(response.info["tool_calls"])
     assert "private-source-fixture" not in stream.getvalue()
     assert result["partial"] is True and result["answer"]==""
    else:
     assert response.msg.parsed.failed is False
     assert all(item["model"]==spec["model"] for item in requests)
     assert requests[-1]["max_tokens"]==8192 and requests[-1].get("response_format",{}).get("type")=="json_schema"
     assert response.info["termination_reasons"]==["stop"]
     if kind=="tools":assert len(requests)==3,requests
  events=[json.loads(line.removeprefix("MODELDESK_EVENT ")) for line in stream.getvalue().splitlines() if line.startswith("MODELDESK_EVENT ")]
  assert step_budgets[0]==("TaskAgent",None),step_budgets
  if kind!="explicit_failed":assert step_budgets[1:]==[("TypedFinishAgent",240)],step_budgets
  assert all(len(json.dumps(event,ensure_ascii=False).encode())<65536 for event in events)
  if kind=="large":
   assert events[0]["preview_truncated"] is True
   assert len(result["format_diagnostics"][0]["raw"])==19000
  persisted=json.loads(output.read_text())
  assert persisted["format_diagnostics"]==result["format_diagnostics"]
 for client in clients:
  if client.__class__.__name__=="AsyncOpenAI":await client.close()
  else:client.close()
for kind in ["tools","html","latest","large","explicit_failed","source_unverified","length","coercion","missing_failed","http400","http403","http408","http500"]:
 asyncio.run(case(kind))
print("NATIVE_TYPED_FINISH_PASS")
'''
        run = subprocess.run([str(python), "-c", script], cwd=Path(__file__).resolve().parents[1],
                             text=True, capture_output=True, timeout=45)
        self.assertEqual(run.returncode, 0, (run.stdout + run.stderr)[-5000:])
        self.assertIn("NATIVE_TYPED_FINISH_PASS", run.stdout)

    def test_native_transport_retries_408_500_connection_once_and_stops_at_budget(self):
        python = Path(__file__).resolve().parents[2]/"llm-tools/.venv-camel/bin/python"
        if not python.exists():
            self.skipTest("isolated CAMEL runtime is unavailable")
        script = '''
import asyncio, contextlib, io, json
from unittest.mock import patch
import httpx
from openai import APIStatusError
from backend.workers.workflow import camel_clients
good={"id":"fixture","object":"chat.completion","created":0,"model":"fixture","choices":[{"index":0,"message":{"role":"assistant","content":"transport fixture"},"finish_reason":"stop"}]}
def handler_factory(statuses):
 calls=[]
 def handle(request):
  calls.append(dict(request.headers))
  value=statuses[min(len(calls)-1,len(statuses)-1)]
  if value=="connection":raise httpx.RemoteProtocolError("fixture EOF")
  return httpx.Response(value,json=good if value==200 else {"error":{"message":"fixture timeout","type":"server_error"}})
 return calls,handle
for failures in ([408,500,200],["connection",408,200],[408]):
 calls,handle=handler_factory(failures)
 stream=io.StringIO()
 with contextlib.redirect_stdout(stream):
  sync,asynchronous=camel_clients("fixture",sync_transport=httpx.MockTransport(handle))
  with patch("openai._base_client.time.sleep"):
   if failures==[408]:
    try:sync.chat.completions.create(model="fixture",messages=[{"role":"user","content":"fixture"}])
    except APIStatusError:pass
    else:raise AssertionError("exhausted error was incorrectly succeeded")
   else:
    answer=sync.chat.completions.create(model="fixture",messages=[{"role":"user","content":"fixture"}])
    assert answer.choices[0].message.content=="transport fixture"
  sync.close();asyncio.run(asynchronous.close())
 assert len(calls)==3,calls
 events=[json.loads(line.removeprefix("MODELDESK_EVENT ")) for line in stream.getvalue().splitlines() if line.startswith("MODELDESK_EVENT ")]
 assert [event["retry"] for event in events]==[1,2],events
 assert "Authorization" not in stream.getvalue()
print("NATIVE_RETRY_PASS")
'''
        run = subprocess.run([str(python), "-c", script], cwd=Path(__file__).resolve().parents[1], text=True,
                             capture_output=True, timeout=30)
        self.assertEqual(run.returncode, 0, (run.stdout+run.stderr)[-4000:])
        self.assertIn("NATIVE_RETRY_PASS", run.stdout)

    def test_native_pool_clone_preserves_retry_and_timeout_without_multiplying_429(self):
        python = Path(__file__).resolve().parents[2] / "llm-tools/.venv-camel/bin/python"
        if not python.exists():
            self.skipTest("isolated CAMEL runtime is unavailable")
        script = r'''
import asyncio, contextlib, io, json, tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch
import httpx
from camel.models import ModelFactory
from camel.types import ModelPlatformType
from camel.societies.workforce.utils import TaskResult
from backend.workers.workflow import workforce_agent, camel_clients

async def check(status):
 calls=[]
 def handle(request):
  calls.append(json.loads(request.content))
  return httpx.Response(status,json={"error":{"message":"fixture upstream error","type":"server_error"}})
 sync,asynchronous=camel_clients("fixture",sync_transport=httpx.MockTransport(handle),async_transport=httpx.MockTransport(handle))
 model=ModelFactory.create(model_platform=ModelPlatformType.OPENAI_COMPATIBLE_MODEL,model_type="gpt-6.1-sol",url="http://127.0.0.1:8317/v1",api_key="local-oauth",model_config_dict={"max_tokens":512},client=sync,async_client=asynchronous)
 spec={"id":"reviewer","role":"审稿人","goal":"fixture","backstory":"fixture","model":"gpt-6.1-sol","tools":[]}
 result={"tasks":[],"partial":True,"answer":""}
 with tempfile.TemporaryDirectory() as directory:
  agent=workforce_agent({"workflow":{}},spec,result,Path(directory)/"result.json",options={"model":model,"tools":[],"retry_attempts":0,"step_timeout":240,"max_iteration":1})
  clone=agent.clone()
  assert clone.retry_attempts==agent.retry_attempts==1
  assert clone.step_timeout is agent.step_timeout is None
  agent.step_timeout=75
  assert agent.clone().step_timeout==75
  stream=io.StringIO()
  with contextlib.redirect_stdout(stream),patch("openai._base_client.anyio.sleep",new=AsyncMock()),patch("camel.agents.chat_agent.asyncio.sleep",new=AsyncMock()) as native_backoff:
   try:await clone.astep("fixture task",TaskResult)
   except Exception:pass
   else:raise AssertionError("exhausted transport incorrectly completed")
  assert len(calls)==3,(status,len(calls))
  assert native_backoff.await_count==0
  events=[json.loads(line.removeprefix("MODELDESK_EVENT ")) for line in stream.getvalue().splitlines() if line.startswith("MODELDESK_EVENT ")]
  assert [entry["retry"] for entry in events]==[1,2],events
  assert result["partial"] is True and result["answer"]=="" and "format_diagnostics" not in result
 sync.close();await asynchronous.close()
asyncio.run(check(429));asyncio.run(check(408))
print("NATIVE_CLONE_RETRY_PASS")
'''
        run = subprocess.run([str(python), "-c", script], cwd=Path(__file__).resolve().parents[1],
                             text=True, capture_output=True, timeout=30)
        self.assertEqual(run.returncode, 0, (run.stdout + run.stderr)[-5000:])
        self.assertIn("NATIVE_CLONE_RETRY_PASS", run.stdout)
    def test_nonfinite_result_is_rejected_before_emit(self):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream), self.assertRaises(ValueError):
            arithmetic(1e100, 1e-300, "divide")
        self.assertEqual(stream.getvalue(), "")
        with contextlib.redirect_stdout(stream):
            self.assertEqual(arithmetic(6, 7, "multiply"), 42)
        event = json.loads(stream.getvalue().strip().removeprefix("MODELDESK_EVENT "))
        self.assertEqual(event["output"], 42)

    def test_lexical_vectors_are_local_deterministic_normalized(self):
        vectors = lexical_embeddings(["alpha beta", "alpha beta", "不同文本", ""])
        self.assertEqual(vectors[0], vectors[1])
        self.assertAlmostEqual(sum(x * x for x in vectors[0]), 1)
        self.assertEqual(sum(vectors[3]), 0)
        self.assertNotEqual(vectors[0], vectors[2])

    def test_fixed_runtime_endpoint_never_emits_token(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, maximum): return b'{"matches":[{"content":"fixture"}]}'
        stream = io.StringIO()
        token = "private-runtime-fixture"
        with patch("urllib.request.urlopen", return_value=Response()) as request, contextlib.redirect_stdout(stream):
            value = runtime_functions({"runtime": {"token": token}})["knowledge_search"]("sample", 2)
        req = request.call_args.args[0]
        self.assertEqual(req.full_url, "http://127.0.0.1:8001/api/platform/runtime/tools/knowledge_search")
        self.assertEqual(json.loads(req.data)["token"], token)
        self.assertNotIn(token, stream.getvalue())
        self.assertIn("fixture", value)
        with self.assertRaises(ValueError):
            runtime_functions({})["memory_read"]()

    def test_runtime_error_status_keeps_permission_and_service_failures_distinct(self):
        token = "private-runtime-error-fixture"
        for status, expected in [(403, "权限"), (404, "不存在"), (408, "超时"), (504, "超时"),
                                 (429, "过于频繁"), (502, "暂时异常")]:
            body = io.BytesIO(b'upstream-private-fixture-body')
            error = urllib.error.HTTPError("http://fixture", status, "fixture", {}, body)
            stream = io.StringIO()
            with self.subTest(status=status), patch("urllib.request.urlopen", side_effect=error), contextlib.redirect_stdout(stream):
                with self.assertRaises(RuntimeError) as raised:
                    runtime_functions({"runtime": {"token": token}})["web_search"]("fixture")
                message = str(raised.exception)
                self.assertIn(f"HTTP {status}", message)
                self.assertIn(expected, message)
                if status == 502:
                    self.assertNotIn("权限", message)
                self.assertNotIn(token, message + stream.getvalue())
                self.assertNotIn("upstream-private-fixture-body", message)

    def test_native_flow_executes_one_branch_and_propagates_skipped_context(self):
        python = Path(__file__).resolve().parents[2] / "llm-tools/.venv-crewai/bin/python"
        if not python.exists():
            self.skipTest("isolated CrewAI runtime is unavailable")
        script = '''
import json, tempfile
from pathlib import Path
from backend.workers.workflow import configured_flow
with tempfile.TemporaryDirectory() as directory:
 result={"tasks":[],"answer":"","routes":[]}
 steps=[
  {"id":"start","agent":"a","description":"fixture","context":[]},
  {"id":"yes","agent":"a","description":"fixture","context":["start"],"when":{"task":"start","operator":"contains","value":"YES"}},
  {"id":"no","agent":"a","description":"fixture","context":["start"],"when":{"task":"start","operator":"not_contains","value":"YES"}},
  {"id":"dependent","agent":"a","description":"fixture","context":["no"]}]
 executed=[]
 def execute(ids):
  executed.extend(ids)
  result["answer"]="YES fixture"
  return result["answer"]
 flow=configured_flow(steps,execute,result,Path(directory)/"result.json")
 flow.kickoff()
 assert executed==["start","yes"],executed
 assert [entry["id"] for entry in result["tasks"]]==["no","dependent"]
 assert all(entry["status"]=="skipped" for entry in result["tasks"])
 assert [entry["decision"] for entry in result["routes"]]==["run","run","skip","skip"]
 print("NATIVE_FLOW_PASS")
'''
        run = subprocess.run([str(python), "-c", script], cwd=Path(__file__).resolve().parents[1],
                             text=True, capture_output=True, timeout=30)
        self.assertEqual(run.returncode, 0, (run.stdout + run.stderr)[-4000:])
        self.assertIn("NATIVE_FLOW_PASS", run.stdout)

    def test_native_workforce_pipeline_is_built_with_actual_dependencies(self):
        python = Path(__file__).resolve().parents[2] / "llm-tools/.venv-camel/bin/python"
        if not python.exists():
            self.skipTest("isolated CAMEL runtime is unavailable")
        script = '''
from camel.models import ModelFactory
from camel.types import ModelPlatformType
from camel.societies.workforce import Workforce
model=ModelFactory.create(model_platform=ModelPlatformType.OPENAI_COMPATIBLE_MODEL,model_type="gpt-6.1-sol",url="http://127.0.0.1:8317/v1",api_key="local-oauth",model_config_dict={"max_tokens":512})
workforce=Workforce("pipeline fixture",default_model=model)
workforce.pipeline_add("first",task_id="first",dependencies=[],auto_depend=False)
workforce.pipeline_add("second",task_id="second",dependencies=["first"],auto_depend=False)
workforce.pipeline_build()
assert [task.id for task in workforce._pending_tasks]==["first","second"]
assert workforce._task_dependencies["second"]==["first"]
print("NATIVE_PIPELINE_PASS")
'''
        with tempfile.TemporaryDirectory() as directory:
            run = subprocess.run([str(python), "-c", script], cwd=directory,
                                 text=True, capture_output=True, timeout=30)
        self.assertEqual(run.returncode, 0, (run.stdout + run.stderr)[-4000:])
        self.assertIn("NATIVE_PIPELINE_PASS", run.stdout)


if __name__ == "__main__":
    unittest.main()
