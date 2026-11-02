"""Reconcile saved external results without authorizing another submission.

Each proof is scoped to the original job/member/prompt and rechecked at execution.
Uncertain dispatch, missing bytes or a changed roster produces no proof. A proof
permits collection only; it is never permission to create an operation.
"""
import hashlib
import json
import uuid
import time

_observation_probes = {}


def completed_operation(job, step):
    try:
        return _completed_operation(job, step)
    except (ValueError, KeyError, TypeError, OSError):
        return None
    except Exception:
        # HTTPException and validation errors from existing scoped stores are
        # missing evidence, not reasons to retry a side-effecting request.
        return None


def _completed_operation(job, step):
    kind = step.get("kind")
    member = next(item for item in job["group_team_snapshot"]["members"] if item["id"] == step["member_id"])
    if kind != member.get("kind", "model"):
        return None
    if kind == "image":
        from .delivery_media import store
        delivery = job["result"]["delivery"]
        assets = []
        for index in range(step.get("image_count", 1)):
            slot = f"role-{step['id']}-{index + 1}"
            saved = next(item for item in delivery["media"] if item.get("slot_id") == slot)
            checked, _ = store.read(job, saved["id"])
            if checked["sha256"] != saved["sha256"]:
                return None
            assets.append(checked["id"])
        return {"engine": kind, "mode": "collect_only", "asset_ids": assets} if assets else None
    prompt = step.get("dispatch_prompt")
    if kind not in {"muse", "codex", "claude", "mcp"} or not isinstance(prompt, str) or not prompt:
        return None
    from .group_delivery_runtime import _scope, _check_child, _finished
    scope = _scope(job, member)
    if kind == "mcp":
        from .mcp_agent import STORE, public_record, configured_server
        from .harness.context import revision
        from .native_skills import freeze_native_skills
        from .skill_library import skill_prompt
        identifier = str(uuid.uuid5(uuid.UUID(scope[0]), "mcp-agent:" + member["id"]))
        path = STORE.directory / (identifier + '.json')
        if path.is_symlink() or STORE.directory.is_symlink():
            return None
        record = json.loads(path.read_text())
        frozen = job.get("_mcp_agent_snapshots", {}).get(member["id"])
        # Recovered snapshots are loaded from the immutable input packet before
        # continuation admission. Never use current server declarations instead.
        if not frozen or frozen.get("server_revision") != revision(configured_server(job, member)):
            return None
        skills = freeze_native_skills(member, job.get("_skill_snapshots"))
        guidance = skill_prompt(list(skills), snapshot=skills)
        if any(record.get(key) != value for key, value in {
            "id": identifier, "parent_job_id": scope[0], "project_id": scope[1], "room_id": scope[2], "member": member,
            "status": "complete", "dispatch_count": 1, "schema_sha256": frozen["schema_sha256"],
            "server_id": frozen["server_id"], "tool": frozen["tool"],
            "output_schema_sha256": frozen.get("output_schema_sha256"),
            "prompt_sha256": hashlib.sha256((prompt + guidance).encode()).hexdigest(),
        }.items()):
            return None
        output = record.get("output")
        if not isinstance(output, str) or not output or hashlib.sha256(output.encode()).hexdigest() != record.get("output_sha256"):
            return None
        result = public_record(record)
    else:
        if kind == "muse":
            from .muse_bridge import manager
        else:
            from .group_tasks import manager
        candidates = [item for item in manager.records.values() if item.get("parent_job_id") == scope[0]
                      and item.get("member_id") == member["id"] and item.get("revision_of_task_id") == step.get("revision_of")]
        if len(candidates) != 1:
            return None
        item = candidates[0]
        _check_child(item, *scope, member)
        if item.get("parent_prompt_sha256") != hashlib.sha256(prompt.encode()).hexdigest():
            return None
        result = _finished(manager, item, kind, scope[1], scope[2])
        if not result or result["status"] != "complete":
            return None
    return {"engine": kind, "mode": "collect_only", "task_id": result["task_id"],
            "output_sha256": result.get("output_sha256")}


async def resume_confirmed_observation(job):
    """At most once, observe an acknowledged Muse request after interruption.

    Never restarts a user-stopped/waiting-approval task, and never invokes submit.
    The native bridge checks the original thread + request marker on every read.
    """
    from .muse_bridge import manager, valid_thread, unverified_generation
    from .group_delivery_runtime import _scope, _check_child
    if job.get("group_native_allowed") is not True or job.get("autonomy", {}).get("enabled") is not True:
        return None
    for step in (job.get("result") or {}).get("delivery", {}).get("steps", []):
        if step.get("kind") != "muse" or step.get("status") == "complete" or not step.get("dispatch_prompt"):
            continue
        try:
            member = next(item for item in job["group_team_snapshot"]["members"] if item["id"] == step["member_id"])
            scope = _scope(job, member)
            candidates = [item for item in manager.records.values() if item.get("parent_job_id") == job["id"]
                          and item.get("member_id") == member["id"] and item.get("revision_of_task_id") == step.get("revision_of")]
            if len(candidates) != 1:
                continue
            child = candidates[0]
            _check_child(child, *scope, member)
            if child.get("parent_prompt_sha256") != hashlib.sha256(step["dispatch_prompt"].encode()).hexdigest():
                continue
            if child.get("status") == "waiting_user" or (child.get("_last_native_observation") or {}).get("waiting_user") is True:
                continue
            if child.get("monitoring") and child.get("background_observe_recoveries"):
                return {"task_id": child["id"], "state": "observing"}
            if (child.get("status") != "interrupted" or child.get("sent") is not True
                    or not valid_thread(child.get("thread_url")) or unverified_generation(child)
                    or child.get("interrupted_from") == "waiting_user"
                    or (child.get("_last_native_observation") or {}).get("waiting_user") is True
                    or child.get("background_observe_recoveries", 0) >= 1):
                continue
            if time.monotonic() - _observation_probes.get(child["id"], float('-inf')) < 30:
                continue
            _observation_probes[child["id"]] = time.monotonic()
            ready = await manager.status()
            if not ready.get("native_ready"):
                continue
            child["background_observe_recoveries"] = 1
            manager.save(child, "background_observe_reserved_without_send")
            await manager.resume_observing(child["id"], scope[1])
            return {"task_id": child["id"], "state": "observing"}
        except Exception:
            continue
    return None
