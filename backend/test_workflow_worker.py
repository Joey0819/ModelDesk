"""Check finite tool output, private runtime bridge and actual native Flow routing."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from backend.workers.workflow import arithmetic, branch_decision, lexical_embeddings, runtime_functions


class WorkerTests(unittest.TestCase):
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
