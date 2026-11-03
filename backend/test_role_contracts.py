import copy
import json
import unittest

from backend.coordination import parse_plan, dependency_lineage
from backend.group_delivery import execution_evidence
from backend.harness.models import TeamRequest, RunRequest
from backend.harness.scheduler import run_graph
from backend.role_contracts import bind_role_contracts, validate_selected_roles
from backend import test_coordination as fixtures

plan = fixtures.plan


def team():
    return {"name": "Build and review", "members": [
        {"id": "writer", "name": "作者", "kind": "model", "model": "model-a", "role": "写作", "required": True,
         "expected_output": "完整初稿与待核实事实"},
        {"id": "reader", "name": "审阅", "kind": "model", "model": "model-b", "role": "核查", "required": True,
         "after_member_ids": ["writer"], "expected_output": "基于实际初稿的修订内容"}]}


class RoleContractTests(unittest.IsolatedAsyncioTestCase):
    def test_saved_contract_roundtrip_and_legacy_shape(self):
        saved = TeamRequest.model_validate(team()).model_dump(mode="json")
        self.assertEqual(saved["members"][1]["after_member_ids"], ["writer"])
        self.assertEqual(TeamRequest.model_validate(saved).model_dump(mode="json"), saved)
        legacy = team()
        for member in legacy["members"]:
            for key in ["required", "after_member_ids", "expected_output"]:
                member.pop(key, None)
        self.assertNotIn("required", TeamRequest.model_validate(legacy).model_dump()["members"][0])

    def test_cycles_unknown_self_and_duplicate_roles_rejected_at_save(self):
        for after in [["reader"], ["missing"], ["writer", "writer"]]:
            value = team()
            value["members"][1]["after_member_ids"] = after
            with self.assertRaises(ValueError):
                TeamRequest.model_validate(value)
        value = team()
        value["members"][0]["after_member_ids"] = ["reader"]
        with self.assertRaises(ValueError):
            TeamRequest.model_validate(value)

    def test_focused_dispatch_never_adds_unselected_predecessor(self):
        with self.assertRaisesRegex(ValueError, "一并选择"):
            validate_selected_roles(team()["members"], ["reader"])
        validate_selected_roles(team()["members"], ["writer"])

    def test_required_member_cannot_be_silently_skipped(self):
        value = plan()
        value["steps"] = value["steps"][:1]
        with self.assertRaisesRegex(ValueError, "必须参与"):
            parse_plan(json.dumps(value), team()["members"], {"images": 0, "videos": 0, "provider": "auto"})

    async def test_planner_omitted_edge_is_enforced_and_real_predecessor_context_arrives(self):
        value = plan()
        value["steps"][1]["depends_on"] = []
        compiled = parse_plan(json.dumps(value), team()["members"], {"images": 0, "videos": 0, "provider": "auto"})
        observed = []
        async def execute(step, parents):
            if step["member_id"] == "reader":
                self.assertEqual(parents[0]["output"], "ACTUAL_DRAFT")
                self.assertEqual(dependency_lineage(step, compiled["steps"])[0]["output"], "ACTUAL_DRAFT")
            observed.append(step["member_id"])
            step.update(status="complete", output="ACTUAL_DRAFT")
        await run_graph(compiled["steps"], execute, lambda: None)
        self.assertEqual(observed, ["writer", "reader"])
        self.assertEqual(execution_evidence(compiled["steps"])[1]["role_contract"]["expected_output"], "基于实际初稿的修订内容")

    def test_reverse_planner_edge_rejected_before_dispatch(self):
        value = plan()
        value["steps"][0]["depends_on"] = ["facts"]
        value["steps"][1]["depends_on"] = []
        with self.assertRaisesRegex(ValueError, "循环"):
            parse_plan(json.dumps(value), team()["members"], {"images": 0, "videos": 0, "provider": "auto"})

    def test_contract_does_not_mutate_team_and_binds_mcp_native_and_image_roles(self):
        for kind in ["mcp", "muse", "codex", "claude", "image"]:
            members = team()["members"]
            members[1]["kind"] = kind
            before = copy.deepcopy(members)
            steps = plan()["steps"]
            bind_role_contracts(steps, members)
            self.assertEqual(members, before)
            self.assertEqual(steps[1]["role_contract"]["after_member_ids"], ["writer"])

    def test_legacy_workflow_rejects_unsupported_contracts(self):
        with self.assertRaisesRegex(ValueError, "角色交接"):
            RunRequest(goal="write", members=team()["members"])


class RoleContractRuntimeTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.CoordinationTests.setUp
    provider = fixtures.CoordinationTests.provider
    run_delivery = fixtures.CoordinationTests.run_delivery

    async def test_runtime_preserves_contracts_in_receipts_and_review(self):
        self.job["group_team_snapshot"] = team()
        delivery = (await self.run_delivery())["delivery"]
        self.assertEqual(delivery["status"], "complete")
        self.assertEqual(delivery["role_execution"][1]["step_ids"], ["facts"])
        self.assertEqual(delivery["steps"][1]["input_context"]["role_packet"]["role_contract"]["after_member_ids"], ["writer"])
        reviewer = next(prompt for _, system, prompt in self.calls if "你是交付审查者" in system)
        self.assertIn("基于实际初稿的修订内容", reviewer)
