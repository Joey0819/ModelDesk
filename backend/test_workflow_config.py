"""Validate real team assignments and dependency boundaries before any worker runs."""
import copy
import unittest

from backend.workflow_config import validate_workflow


def configuration():
    return {"agents": [
        {"id": "author", "role": "起草者", "goal": "提出方案", "model": "a", "tools": ["math"]},
        {"id": "critic", "role": "审查者", "goal": "核查证据", "model": "b", "tools": ["text_stats"]},
    ], "tasks": [
        {"id": "draft", "description": "起草", "expected_output": "方案", "agent": "author", "context": []},
        {"id": "check", "description": "检查初稿", "expected_output": "问题与依据", "agent": "critic", "context": ["draft"]},
    ], "process": "sequential", "max_iter": 3}


class WorkflowTests(unittest.TestCase):
    def test_custom_agents_tasks_and_explicit_prior_context(self):
        team = validate_workflow(configuration(), "crewai", ["a", "b"])
        self.assertEqual(team.tasks[1].context, ["draft"])
        self.assertEqual(team.agents[0].tools, ["math"])
        self.assertEqual(team.model_dump()["agents"][1]["goal"], "核查证据")

    def test_forward_cycle_unknown_agent_and_arbitrary_tools_rejected(self):
        mutations = [
            lambda value: value["tasks"][0].update(context=["check"]),
            lambda value: value["tasks"][1].update(agent="absent"),
            lambda value: value["agents"][0].update(tools=["shell"]),
            lambda value: value["agents"][0].update(model="not-selected"),
            lambda value: value["tasks"][1].update(id="draft"),
            lambda value: value["agents"][0].update(code="print('arbitrary')"),
        ]
        for mutate in mutations:
            value = copy.deepcopy(configuration())
            mutate(value)
            with self.subTest(configuration=value), self.assertRaises(ValueError):
                validate_workflow(value, "crewai", ["a", "b"])

    def test_hierarchical_requires_selected_manager(self):
        value = configuration()
        value["process"] = "hierarchical"
        with self.assertRaises(ValueError):
            validate_workflow(value, "crewai", ["a", "b"])
        value["manager_model"] = "a"
        self.assertEqual(validate_workflow(value, "crewai", ["a", "b"]).process, "hierarchical")
        value["manager_model"] = "other"
        with self.assertRaises(ValueError):
            validate_workflow(value, "crewai", ["a", "b"])

    def test_camel_is_two_custom_roles_without_crew_task_graph(self):
        value = configuration()
        with self.assertRaises(ValueError):
            validate_workflow(value, "camel", ["a", "b"])
        value["tasks"] = []
        result = validate_workflow(value, "camel", ["a", "b"])
        self.assertEqual([agent.role for agent in result.agents], ["起草者", "审查者"])
        with self.assertRaises(ValueError):
            validate_workflow(value, "chat", ["a", "b"])

    def test_native_advanced_models_and_mode_boundaries(self):
        value = configuration()
        value["tasks"] = []
        value.update(task_specify=True, task_planning=True, critic=True, critic_model="b")
        team = validate_workflow(value, "camel", ["a", "b"])
        self.assertTrue(team.critic)
        value["critic_model"] = "unselected"
        with self.assertRaises(ValueError):
            validate_workflow(value, "camel", ["a", "b"])
        value = configuration()
        value.update(planning=True, planning_model="a", memory=True)
        self.assertTrue(validate_workflow(value, "crewai", ["a", "b"]).memory)
        value["critic"] = True
        with self.assertRaises(ValueError):
            validate_workflow(value, "crewai", ["a", "b"])
        value = configuration()
        self.assertEqual(len(validate_workflow(value, "camel_workforce", ["a", "b"]).tasks), 2)

    def test_flow_condition_dag_and_resource_tool_bindings(self):
        value = configuration()
        value["tasks"][1]["when"] = {"task": "draft", "operator": "contains", "value": "approved"}
        self.assertEqual(validate_workflow(value, "crewai_flow", ["a", "b"]).tasks[1].when.task, "draft")
        with self.assertRaises(ValueError):
            validate_workflow(value, "crewai", ["a", "b"])
        value["tasks"][0]["when"] = {"task": "check", "operator": "equals", "value": "yes"}
        with self.assertRaises(ValueError):
            validate_workflow(value, "crewai_flow", ["a", "b"])
        for name in ("knowledge_search", "project_read", "mcp_call"):
            value = configuration()
            value["agents"][0]["tools"] = [name]
            with self.subTest(tool=name), self.assertRaises(ValueError):
                validate_workflow(value, "crewai", ["a", "b"])
        value = configuration()
        value.update(knowledge_ids=["11111111-1111-4111-8111-111111111111"], memory_scope="team/research")
        value["agents"][0]["tools"] = ["knowledge_search", "memory_save"]
        self.assertEqual(validate_workflow(value, "crewai", ["a", "b"]).model_dump(mode="json")["knowledge_ids"], value["knowledge_ids"])
        for scope in ("../secret", "team//secret", "team/../secret", "/global"):
            value["memory_scope"] = scope
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                validate_workflow(value, "crewai", ["a", "b"])


if __name__ == "__main__":
    unittest.main()
