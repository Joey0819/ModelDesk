"""User-owned collaboration rules compiled into the existing execution graph.

These rules constrain scheduling and context transfer; they never grant tools,
select a different executor, or widen the recipients of an @-scoped task.
"""
import copy

from .harness.scheduler import validate_graph


def role_contract(member):
    return {"required": bool(member.get("required", False)),
            "after_member_ids": list(member.get("after_member_ids") or []),
            "expected_output": member.get("expected_output", "")}


def has_role_contract(member):
    return any(role_contract(member).values())


def validate_role_graph(members):
    validate_graph([{"id": member["id"], "depends_on": member.get("after_member_ids", [])} for member in members])


def validate_selected_roles(members, selected_ids):
    """Do not silently add an unselected native/MCP agent to a user's task."""
    selected = set(selected_ids)
    names = {member["id"]: member["name"] for member in members}
    for member in members:
        if member["id"] not in selected:
            continue
        missing = [key for key in member.get("after_member_ids", []) if key not in selected]
        if missing:
            raise ValueError(f"{member['name']} 需要先接收 {'、'.join(names.get(key, key) for key in missing)} 的成果；请一并选择这些成员或调整交接设置")


def bind_role_contracts(steps, members):
    """Compile saved role edges before dispatch, including model repair plans."""
    by_member = {member["id"]: [] for member in members}
    for step in steps:
        by_member[step["member_id"]].append(step)
    for member in members:
        assigned = by_member[member["id"]]
        if member.get("required") and not assigned:
            raise ValueError(f"必须参与的角色 {member['name']} 未分配实际步骤，不能用统筹总结代替")
        if not assigned:
            continue
        for predecessor in member.get("after_member_ids", []):
            parents = by_member.get(predecessor, [])
            if not parents:
                raise ValueError(f"{member['name']} 的前置角色 {predecessor} 未分配步骤；需先完成交接来源")
            for step in assigned:
                step["depends_on"] = list(dict.fromkeys([*step.get("depends_on", []), *(parent["id"] for parent in parents)]))
        if has_role_contract(member):
            for step in assigned:
                step["role_contract"] = copy.deepcopy(role_contract(member))
    # A planner can create a reverse edge even when the saved role graph is a
    # DAG. Reject that combined graph before any external side effect.
    validate_graph(steps)


def role_execution_summary(members, steps):
    return [{"member_id": member["id"], "member_name": member["name"],
             "kind": member.get("kind", "model"), **role_contract(member),
             "step_ids": [step["id"] for step in steps if step["member_id"] == member["id"]]}
            for member in members if member["id"] != "__coordinator__"]
