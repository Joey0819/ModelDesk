"""An inspectable task brief alongside the immutable request/artifact sources.

The brief is an index, not a lossy replacement for memory. Planning criteria are
labeled as model interpretation; original instructions remain authoritative.
"""
import copy
import hashlib
import json


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_brief(context, *, goal=None, requirements=None):
    context = context or {}
    requests = context.get("requests") or ([{"prompt": goal, "job_ids": []}] if goal else [])
    sources = [{"id": f"request-{index + 1}", "job_ids": list(item.get("job_ids", [])),
                "kind": "original_goal" if index == 0 else "user_revision",
                "characters": len(item["prompt"]), "sha256": hashlib.sha256(item["prompt"].encode()).hexdigest(),
                "text_ref": f"requests[{index}].prompt"} for index, item in enumerate(requests)]
    latest = context.get("artifact") or {}
    baseline = context.get("baseline_artifact") or {}
    value = {"schema_version": 1, "revision": context.get("revision", 1), "task_id": context.get("task_id"),
             "project_id": context.get("project_id"), "room_id": context.get("room_id"),
             "objective": requests[0]["prompt"] if requests else goal or "",
             "latest_instruction": requests[-1]["prompt"] if requests else goal or "",
             "sources": sources, "source_context_sha256": context.get("sha256"),
             "pending_items": copy.deepcopy(latest.get("missing_outputs", [])),
             "artifact_ref": {key: latest.get(key) for key in ["job_id", "title", "status", "content_sha256"]} if latest else None,
             "baseline_ref": {key: baseline.get(key) for key in ["job_id", "title", "status", "content_sha256"]} if baseline else None,
             "document_preferences": copy.deepcopy(context.get("document_preferences", {})),
             "project_memory": copy.deepcopy(context.get("project_memory")),
             "requirements": copy.deepcopy(requirements or []), "requirements_origin": "planner_interpretation",
             "precedence": "later_user_revision_over_conflicts_keep_unmodified_originals"}
    # Full text already exists in requests. The summary endpoint can expose it
    # once without copying long originals into every step receipt.
    value["sha256"] = fingerprint(value)
    return value


def role_packet(member, step, brief, lineage):
    value = {"schema_version": 1, "task_id": brief.get("task_id"), "brief_revision": brief["revision"],
             "brief_sha256": brief["sha256"], "member_id": member["id"], "role": member.get("role", ""),
             "step_id": step["id"], "user_source_ids": [source["id"] for source in brief["sources"]],
             "requirement_ids": [item["id"] for item in brief["requirements"]],
             "dependency_outputs": [{"step_id": item["id"], "sha256": hashlib.sha256(str(item.get("output") or "").encode()).hexdigest()}
                                    for item in lineage],
             "authority": "instructions_and_sources_only_no_new_tool_permissions"}
    value["sha256"] = fingerprint(value)
    return value
