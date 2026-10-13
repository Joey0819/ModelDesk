"""Human decisions bound to a completed agent patch and an independent review.

The only write into the registered repository is Git's fast-forward merge. An
alternate index prepares the candidate commit without staging the user's files.
"""
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import groups, projects, workbench
from .harness.store import RunStore, checked_id
from .muse_bridge import local_mutation

router = APIRouter(prefix="/api/group-approvals", tags=["group-approvals"])
SHA = re.compile(r"^[a-f0-9]{40}(?:[a-f0-9]{24})?$")


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: uuid.UUID
    room_id: uuid.UUID
    task_id: uuid.UUID
    review_task_id: uuid.UUID


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: uuid.UUID
    room_id: uuid.UUID
    decision: Literal["approve", "reject"]
    expected_base_sha: str = Field(pattern=r"^[a-f0-9]{40}(?:[a-f0-9]{24})?$")
    expected_patch_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def task_manager():
    from .group_tasks import manager
    return manager


def git(root, args, *, index=None, data=None):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT="0", GIT_AUTHOR_NAME="ModelDesk", GIT_AUTHOR_EMAIL="modeldesk@localhost",
               GIT_COMMITTER_NAME="ModelDesk", GIT_COMMITTER_EMAIL="modeldesk@localhost")
    if index:
        env["GIT_INDEX_FILE"] = str(index)
    # No executable hooks, signing process, external diff, or fsmonitor.
    command = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
               "-c", "commit.gpgSign=false", "-c", "merge.autoStash=false", "-c", "core.autocrlf=false",
               "-C", str(root)]
    filters = subprocess.run([*command, "config", "--name-only", "--get-regexp", r"^filter\."],
                             capture_output=True, env=env, timeout=15)
    for key in filters.stdout.decode(errors="replace").splitlines():
        if re.fullmatch(r"filter\..+\.(clean|smudge|process|required)", key):
            command.extend(["-c", key + ("=false" if key.endswith(".required") else "=")])
    result = subprocess.run([*command, *args], input=data, capture_output=True, env=env, timeout=30)
    if result.returncode:
        raise HTTPException(409, "Git 未完成合入；请检查项目状态、文件冲突和磁盘权限，原有改动不会被强制覆盖")
    return result.stdout


def clean_target(project_id, expected_sha=None, expected_branch=None):
    project = projects.store.get(project_id)
    root = Path(project["path"])
    if root.is_symlink() or not root.is_dir() or root.resolve() != root:
        raise HTTPException(409, "登记的项目目录已经移动或链接到其他位置")
    info = projects.git_info(root)
    if not info["available"] or not info["head"] or info["branch"] == "(detached)":
        raise HTTPException(409, "合入需要有提交且位于本地分支的 Git 项目")
    if info["status"]:
        raise HTTPException(409, "项目存在未提交改动；请先保存提交，再重新执行任务和审查")
    if expected_sha and (info["head"] != expected_sha or info["branch"] != expected_branch):
        raise HTTPException(409, "项目分支或提交已变化；本次审批已过期，请重新执行并审查")
    return root, info


def checked_patch(task):
    patch = task_manager().read_patch(task["id"], task["project_id"], task["room_id"])
    if isinstance(patch, bytes):
        patch = patch.decode("utf-8")
    if not isinstance(patch, str) or not patch or len(patch.encode()) > 2_000_000:
        raise HTTPException(409, "任务没有可合入的完整文本补丁")
    if hashlib.sha256(patch.encode()).hexdigest() != task.get("patch_sha256"):
        raise HTTPException(409, "任务补丁已变化，不能使用旧审查合入")
    return patch


class ApprovalManager:
    def __init__(self, directory=None):
        self.store = RunStore(directory or workbench.PROJECT / "data/group-approvals")
        self.lock = threading.RLock()

    def save(self, card):
        path = self.store.directory / (checked_id(card["id"]) + ".json")
        if self.store.directory.is_symlink() or path.is_symlink() or path.with_suffix(".tmp").is_symlink():
            raise HTTPException(409, "审批记录保存路径无效")
        self.store.save(card)

    def get(self, identifier, project_id, room_id):
        identifier, project_id, room_id = map(checked_id, [identifier, project_id, room_id])
        path = self.store.directory / (identifier + ".json")
        try:
            if path.is_symlink() or self.store.directory.is_symlink():
                raise ValueError("linked record")
            card = json.loads(path.read_text())
            if card["id"] != identifier or card["schema_version"] != 1:
                raise ValueError("invalid identity")
        except FileNotFoundError as error:
            raise HTTPException(404, "审批记录不存在") from error
        except (ValueError, KeyError, OSError) as error:
            raise HTTPException(409, "审批记录不可读取，原文件已保留") from error
        if card.get("project_id") != project_id or card.get("room_id") != room_id:
            raise HTTPException(403, "审批不属于当前项目群")
        return card

    def list(self, project_id, room_id):
        groups.store.get(room_id, project_id)
        cards = []
        for path in self.store.directory.glob("*.json"):
            try:
                cards.append(self.get(path.stem, project_id, room_id))
            except HTTPException:
                continue
        return sorted(cards, key=lambda card: (card["created_at"], card["id"]))

    def evidence(self, request):
        body = request.model_dump(mode="json") if isinstance(request, BaseModel) else request
        groups.store.get(body["room_id"], body["project_id"])
        worker = task_manager()
        task = worker.get(body["task_id"], body["project_id"], body["room_id"])
        review = worker.get(body["review_task_id"], body["project_id"], body["room_id"])
        if (task.get("status") != "complete" or task.get("kind") != "code"
                or review.get("status") != "complete" or review.get("kind") != "review"
                or review.get("source_task_id") != task["id"]
                or review.get("member_id") == task.get("member_id")
                or review.get("review_of_patch_sha256") != task.get("patch_sha256")):
            raise HTTPException(409, "需要另一位成员完成对本次确切补丁的审查后才能发起审批")
        if not SHA.fullmatch(task.get("base_sha", "")) or not review.get("output", "").strip():
            raise HTTPException(409, "缺少真实执行基准或完整审查结果")
        return task, review, checked_patch(task)

    def create(self, request):
        with self.lock:
            task, review, _ = self.evidence(request)
            root, info = clean_target(task["project_id"])
            if info["head"] != task["base_sha"]:
                raise HTTPException(409, "任务执行之后项目已经变化；请重新执行并审查")
            existing = next((card for card in self.list(task["project_id"], task["room_id"])
                             if card["task_id"] == task["id"] and card["review_task_id"] == review["id"]
                             and card["status"] in {"pending", "merged", "merging"}), None)
            if existing:
                return existing
            card = {**request.model_dump(mode="json"), "id": str(uuid.uuid4()), "schema_version": 1,
                    "status": "pending", "title": task.get("prompt", "Agent 任务")[:160],
                    "created_at": workbench.now(), "updated_at": workbench.now(),
                    "base_sha": task["base_sha"], "patch_sha256": task["patch_sha256"],
                    "target_branch": info["branch"], "project_path": str(root),
                    "changed_files": copy.deepcopy(task.get("changed_files", [])),
                    "agent_output": task["output"][:12000], "review_output": review["output"][:20000],
                    "merge_commit_sha": None, "scope": "local_repository", "pushed": False}
            self.save(card)
            return card

    def candidate(self, root, card, patch):
        with tempfile.TemporaryDirectory(prefix="modeldesk-approval-") as directory:
            index = Path(directory) / "index"
            git(root, ["read-tree", card["base_sha"]], index=index)
            git(root, ["apply", "--cached", "--whitespace=nowarn", "-"], index=index, data=patch.encode())
            # Inspect the actual staged tree, not just patch header text.
            changes = git(root, ["diff", "--cached", "--name-only", "-z", card["base_sha"]], index=index).decode().split("\0")
            names = [name for name in changes if name]
            if not names or len(names) > 200:
                raise HTTPException(409, "补丁文件数量超出可审批范围")
            for name in names:
                if projects.excluded(name):
                    raise HTTPException(409, "补丁包含凭据、缓存或不允许合入的文件")
                projects.checked_path(root, name)
                entries = git(root, ["ls-files", "--stage", "-z", "--", name], index=index).decode().strip("\0")
                if entries:
                    mode, blob = entries.split(None, 2)[:2]
                    if mode not in {"100644", "100755"}:
                        raise HTTPException(409, "不能审批符号链接或子模块变更")
                    raw = git(root, ["cat-file", "blob", blob])
                    try:
                        text = raw.decode("utf-8")
                    except UnicodeError as error:
                        raise HTTPException(409, "不能审批二进制变更") from error
                    from .attachments import secret_text
                    if len(raw) > projects.MAX_FILE_BYTES or "\0" in text or secret_text(text):
                        raise HTTPException(409, "补丁内容超过限制或可能包含凭据")
            tree = git(root, ["write-tree"], index=index).decode().strip()
            message = f"Apply reviewed agent task / 合入已审查任务\n\nModelDesk-Approval: {card['id']}\nPatch-SHA256: {card['patch_sha256']}\nReview-Task: {card['review_task_id']}\n"
            return git(root, ["commit-tree", tree, "-p", card["base_sha"]], data=message.encode()).decode().strip()

    def decide(self, identifier, request):
        with self.lock:
            body = request.model_dump(mode="json")
            card = self.get(identifier, body["project_id"], body["room_id"])
            if (body["expected_base_sha"] != card["base_sha"] or body["expected_patch_sha256"] != card["patch_sha256"]):
                raise HTTPException(409, "审批版本不匹配，请重新查看群内审批卡片")
            if card["status"] == "merged" and body["decision"] == "approve":
                return card
            if card["status"] == "rejected" and body["decision"] == "reject":
                return card
            if card["status"] != "pending":
                raise HTTPException(409, "该审批已处理或需人工检查，不能再次执行")
            if body["decision"] == "reject":
                card.update(status="rejected", updated_at=workbench.now())
                self.save(card)
                return card
            task, review, patch = self.evidence(card)
            if task["patch_sha256"] != card["patch_sha256"] or task["base_sha"] != card["base_sha"]:
                raise HTTPException(409, "代码证据已经变化，请重新发起审批")
            root, _ = clean_target(card["project_id"], card["base_sha"], card["target_branch"])
            if str(root) != card["project_path"]:
                raise HTTPException(409, "项目登记路径已变化，请重新发起审批")
            candidate = self.candidate(root, card, patch)
            clean_target(card["project_id"], card["base_sha"], card["target_branch"])
            card.update(status="merging", merge_commit_sha=candidate, updated_at=workbench.now())
            self.save(card)  # Persist intent before Git; restart NEVER reruns merge.
            try:
                git(root, ["-c", "merge.verifySignatures=false", "merge", "--ff-only", "--no-edit", "--no-autostash", "--no-overwrite-ignore", candidate])
                current = git(root, ["rev-parse", "HEAD"]).decode().strip()
                if current != candidate:
                    raise HTTPException(409, "合入结果需要人工确认，未自动重试")
            except Exception:
                card.update(status="needs_attention", updated_at=workbench.now(), error="合入未确认；请检查项目，系统不会自动重试或回滚用户改动")
                self.save(card)
                raise
            card.update(status="merged", updated_at=workbench.now())
            self.save(card)
            return card


manager = ApprovalManager()


@router.get("")
async def list_approvals(project_id: str, room_id: str):
    return await asyncio.to_thread(manager.list, project_id, room_id)


@router.post("", status_code=201, dependencies=[Depends(local_mutation)])
async def create_approval(request: ApprovalRequest):
    return await asyncio.to_thread(manager.create, request)


@router.post("/{identifier}/decision", dependencies=[Depends(local_mutation)])
async def decide_approval(identifier: str, request: DecisionRequest):
    return await asyncio.to_thread(manager.decide, identifier, request)
