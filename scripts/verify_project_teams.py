"""Real-model acceptance for saved project teams, roles, and frozen membership.

Creates only synthetic projects and prompts. Requires the existing local API and
gateway; it never creates provider credentials or enables tools.
"""
import argparse
import json
from pathlib import Path
import uuid

import httpx

from verify_harness import call, check_public, wait

ROOT = Path(__file__).resolve().parents[1]
MODELS = ["gpt-6.1-sol", "gemini-3-flash"]


def verify_results(client, report):
    runs = [call(client, "GET", "/api/harness/runs/" + identifier) for identifier in report["run_ids"]]
    teams = report["team_snapshots"]
    projects, tag = report["project_ids"], report["tag"]
    final_runs = [wait(client, run["id"]) for run in runs]
    for index, run in enumerate(final_runs):
        assert run["status"] == "complete", run.get("error")
        check_public(run)
        assert run["team_snapshot"] == teams[index]
        for step in run["steps"]:
            assert len(step["attempts"]) == 1
            job = call(client, "GET", "/api/workbench/jobs/" + step["attempts"][0]["job_id"])
            result = job["result"]
            responses = result["responses"] if step["mode"] == "chat" else result["stage1"]
            by_model = {item["model"]: item.get("text", item.get("content", item.get("response", ""))) for item in responses}
            for member in teams[index]["members"]:
                marker = ("WRITER" if member["id"] == "writer" else "REVIEWER") + f"-{tag}-{index}"
                assert marker in by_model[member["model"]], f"missing actual role marker: {step['id']} {member['name']}"
                assert "UPDATED-" + tag not in by_model[member["model"]], "task used edited roles"
                assert f"-{tag}-{1-index}" not in by_model[member["model"]], "cross-project role leakage"
        exported = client.get("/api/harness/runs/" + run["id"] + "/export", params={"format": "md"})
        exported.raise_for_status()
        assert teams[index]["name"] in exported.text
    archived = call(client, "DELETE", "/api/harness/teams/" + teams[1]["id"], params={"project_id": projects[1]})
    assert archived["archived"]
    assert call(client, "GET", "/api/harness/teams", params={"project_id": projects[1]}) == []
    assert call(client, "GET", "/api/harness/runs/" + runs[1]["id"])["team_snapshot"] == teams[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8001")
    args = parser.parse_args()
    tag = uuid.uuid4().hex[:6].upper()
    report = {"tag": tag, "phase": "running"}
    target = ROOT / "data/project-teams-acceptance.json"
    target.parent.mkdir(parents=True, exist_ok=True)

    def save():
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    with httpx.Client(base_url=args.base, trust_env=False, timeout=30) as client:
        projects, teams = [], []
        for index in range(2):
            project = call(client, "POST", "/api/workbench/projects", json={
                "name": f"协作组验收 {tag} · {index + 1}",
                "description": "仅合成资料，用于验证项目隔离与实际角色派发。"})
            projects.append(project["id"])
            members = [{"id": "writer", "name": "方案编辑", "model": MODELS[0],
                        "role": f"你的专属校验词为 WRITER-{tag}-{index}。负责写发布检查清单。"},
                       {"id": "reviewer", "name": "独立核查", "model": MODELS[1],
                        "role": f"你的专属校验词为 REVIEWER-{tag}-{index}。负责检查遗漏。"}]
            teams.append(call(client, "POST", "/api/harness/teams", json={
                "project_id": project["id"], "name": f"发布协作组 {index + 1}", "members": members}))
        report.update(project_ids=projects, team_ids=[team["id"] for team in teams], team_snapshots=teams)
        save()
        for index in range(2):
            visible = call(client, "GET", "/api/harness/teams", params={"project_id": projects[index]})
            assert [item["id"] for item in visible] == [teams[index]["id"]]
        response = client.post("/api/harness/runs", json={
            "goal": "应拒绝跨项目成员", "project_id": projects[1], "team_id": teams[0]["id"],
            "steps": [{"id": "check", "title": "范围检查", "mode": "chat"}]})
        assert response.status_code == 403, response.text

        goal = "请先写出本人的成员名称与角色职责里的专属校验词，然后给出一条具体的发布检查建议。使用中文，最多100字。不要猜测其他成员的校验词。"
        runs = []
        for index in range(2):
            steps = [{"id": "draft", "title": "成员初稿", "mode": "chat", "require_review": index == 0}]
            if index == 0:
                steps.append({"id": "review", "title": "成员评审", "mode": "council"})
            runs.append(call(client, "POST", "/api/harness/runs", json={
                "goal": goal, "project_id": projects[index], "team_id": teams[index]["id"],
                "include_project_assets": False, "steps": steps}))
        report["run_ids"] = [run["id"] for run in runs]
        save()
        first = wait(client, runs[0]["id"])
        assert first["status"] == "waiting_review", first.get("error")
        assert not first["steps"][1]["attempts"]
        # Edit only this script's team while its task waits for user review.
        changed = {key: teams[0][key] for key in ["name", "description", "project_id", "members"]}
        changed = json.loads(json.dumps(changed))
        changed["name"] += "（新版本）"
        for member in changed["members"]:
            member["role"] = "你的专属校验词为 UPDATED-" + tag + "。负责新版本任务。"
        call(client, "PUT", "/api/harness/teams/" + teams[0]["id"], json=changed)
        frozen = call(client, "GET", "/api/harness/runs/" + first["id"])
        assert frozen["team_snapshot"] == teams[0], "active task changed with team edits"
        call(client, "POST", "/api/harness/runs/" + first["id"] + "/approve")

        verify_results(client, report)
        report.update(phase="complete", actual_roles_verified=True, project_isolation_verified=True,
                      team_edit_did_not_change_active_task=True, no_completed_step_replay=True,
                      archive_preserved_history=True, export_included_frozen_team=True)
        save()
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
