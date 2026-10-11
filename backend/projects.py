"""Registered local projects and immutable, bounded, read-only review snapshots."""
import asyncio
import hashlib
import json
import os
import re
import stat
import subprocess
import threading
import uuid
from pathlib import Path, PurePosixPath
from typing import Literal
import httpx

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from . import workbench

router = APIRouter(prefix="/api/workbench/projects", tags=["projects"])
SCOPES = Literal["working", "staged", "last_commit", "branch", "selected"]
MAX_FILES = 20
MAX_CONTEXT = 60000
MAX_FILE_BYTES = 512000
EXCLUDED = {".git", ".private", "node_modules", "__pycache__", ".cache", ".venv", "venv", "dist", "build", "target", ".idea", ".DS_Store", ".npmrc", ".pypirc", ".netrc", ".ssh", ".aws", ".azure", ".gcloud"}
SECRET_NAME = re.compile(r"(^\.env($|\.)|credential|oauth|(^|[-_.])secrets?($|[-_.])|(^|[-_.])tokens?($|[-_.])|^auth\.json$|^id_(rsa|ed25519)|\.(pem|p12|key|keystore)$)", re.I)
SECRET_CONTENT = re.compile(r"-----BEGIN (?:[A-Z ]*PRIVATE KEY)|\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}|\bGOCSPX-[\w-]+|\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{15,}|(?im:(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password)\s*[:=]\s*['\"][^'\"\n]{8,}['\"])")
UNQUOTED_SECRET = re.compile(r"(?im)^\s*(?:-\s*)?[\"']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|authorization)[\"']?\s*[:=]\s*(?!null\b|none\b|false\b|true\b|['\"]['\"])(?:[^\s#,\n]+)")
KNOWN_TOKEN = re.compile(r"\bAIza[\w-]{30,}|\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}|\bxox[baprs]-[A-Za-z0-9-]{10,}|\bhf_[A-Za-z0-9]{20,}")
JSON_SECRET = re.compile(r"(?i)[\"'](?:authorization|password|passwd|api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|aws_secret_access_key|_authToken)[\"']\s*[:=]\s*[\"'][^\"'\n]+[\"']")
DESCRIPTIONS = {"working": "工作区相对 HEAD 的改动（包含暂存及未跟踪文件）", "staged": "仅暂存区相对 HEAD 的改动", "last_commit": "最近一次提交相对父提交的改动", "branch": "当前 HEAD 相对所选基准共同祖先的提交改动（不含工作区改动）", "selected": "所选文件当前磁盘内容"}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def checked_path(root, value):
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise ValueError("文件名无效")
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts or value.startswith("-"):
        raise ValueError("文件必须位于已登记项目内")
    target = root.joinpath(*relative.parts)
    if not target.resolve().is_relative_to(root):
        raise ValueError("符号链接不能指向项目外部")
    return target


def excluded(value):
    return any(part in EXCLUDED or part.startswith(".venv") or SECRET_NAME.search(part) for part in PurePosixPath(value).parts)


def git(root, arguments, check=True):
    # Never execute fsmonitor hooks, external diffs/text conversion, or ask for auth.
    env = {**{key: value for key, value in os.environ.items() if not key.startswith("GIT_")}, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    prefix = ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-c", "diff.external=", "-C", str(root)]
    # Working-tree diff can invoke clean/process filters even with no-ext-diff.
    # Read only their config key names and override every filter in this process.
    configured = subprocess.run([*prefix, "config", "--name-only", "--get-regexp", r"^filter\."], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=8, env=env)
    names = set()
    for key in configured.stdout.decode("utf-8", errors="replace").splitlines():
        match = re.fullmatch(r"filter\.(.+)\.(?:clean|process|smudge|required)", key)
        if match:
            names.add(match[1])
    for name in sorted(names):
        for setting, value in [("clean", ""), ("process", ""), ("smudge", ""), ("required", "false")]:
            prefix.extend(["-c", f"filter.{name}.{setting}={value}"])
    result = subprocess.run([*prefix, *arguments], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=8, env=env)
    if check and result.returncode:
        raise ValueError("Git 读取失败；请检查提交或基准分支是否存在。")
    return result


def git_info(root):
    probe = git(root, ["rev-parse", "--show-toplevel"], check=False)
    if probe.returncode or Path(probe.stdout.decode().strip()).resolve() != root:
        return {"available": False, "branch": None, "head": None, "status": []}
    head = git(root, ["rev-parse", "--verify", "HEAD"], check=False)
    branch = git(root, ["symbolic-ref", "--short", "-q", "HEAD"], check=False)
    status = git(root, ["status", "--porcelain=v1", "-z", "--untracked-files=all"]).stdout.decode("utf-8", errors="replace").split("\x00")
    changes = []
    index = 0
    while index < len(status):
        entry = status[index]
        index += 1
        if not entry:
            continue
        item = {"path": entry[3:], "status": entry[:2].strip() or "modified"}
        if "R" in entry[:2] or "C" in entry[:2]:
            if index < len(status):
                item["old_path"] = status[index]
                index += 1
        changes.append(item)
    return {"available": True, "branch": branch.stdout.decode().strip() or "(detached)", "head": head.stdout.decode().strip() if not head.returncode else None, "status": changes[:1000]}


def read_bytes(root, value, revision=None):
    target = checked_path(root, value)
    if excluded(value):
        return None, "认证文件或缓存已排除"
    if revision is None:
        if not target.exists():
            return None, None
        if not target.is_file():
            return None, "不是普通文件"
        if target.stat().st_size > MAX_FILE_BYTES:
            return None, "文件超过 512 KB 限制"
        # Bind each directory component; a concurrent symlink replacement must
        # never redirect a snapshot read outside the registered project.
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            parts = PurePosixPath(value).parts
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            file_descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptor)
            with os.fdopen(file_descriptor, "rb") as source:
                metadata = os.fstat(source.fileno())
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_FILE_BYTES:
                    return None, "不是普通文本文件或超过 512 KB 限制"
                raw = source.read(MAX_FILE_BYTES + 1)
                if len(raw) > MAX_FILE_BYTES:
                    return None, "文件超过 512 KB 限制"
        finally:
            os.close(descriptor)
    else:
        raw_result = git(root, ["show", f"{revision}:{value}"], check=False)
        if raw_result.returncode:
            return None, None
        raw = raw_result.stdout
        if len(raw) > MAX_FILE_BYTES:
            return None, "文件超过 512 KB 限制"
    if b"\x00" in raw:
        return None, "二进制文件已排除"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, "非 UTF-8 文件已排除"
    if SECRET_CONTENT.search(text) or UNQUOTED_SECRET.search(text) or KNOWN_TOKEN.search(text) or JSON_SECRET.search(text):
        return None, "可能包含认证信息，整份文件已排除"
    return text, None


def excerpt(text, anchor, remaining):
    if text is None:
        return None, remaining, False
    lines = text.splitlines()
    start = max(0, min(len(lines) - 1, anchor - 26)) if lines else 0
    chosen = []
    size = 0
    for line in lines[start:start + 250]:
        if size + len(line) + 1 > min(18000, remaining):
            break
        chosen.append(line)
        size += len(line) + 1
    if lines and not chosen:
        return None, remaining, True
    return {"start_line": start + 1, "end_line": start + len(chosen), "text": "\n".join(chosen), "total_lines": len(lines), "sha256": hashlib.sha256(text.encode()).hexdigest()}, remaining - size, start > 0 or start + len(chosen) < len(lines)


class ProjectStore:
    def __init__(self, directory=None):
        self.directory = Path(directory or workbench.PROJECT / "data/projects")
        self.lock = threading.RLock()

    def initialize(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        (self.directory / "snapshots").mkdir(exist_ok=True, mode=0o700)

    def read_registry(self):
        self.initialize()
        path = self.directory / "registry.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def save(self, path, data):
        self.initialize()
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        temporary.chmod(0o600)
        temporary.replace(path)

    def register(self, name, path, project_id=None):
        root = Path(path).expanduser()
        if not root.is_absolute() or not root.is_dir():
            raise ValueError("请选择已存在的本地项目绝对路径")
        root = root.resolve()
        with self.lock:
            registry = self.read_registry()
            if project_id and project_id not in registry:
                raise HTTPException(404, "项目未登记")
            if any(item["path"] == str(root) and item["id"] != project_id for item in registry.values()):
                raise ValueError("该项目路径已登记")
            identifier = project_id or str(uuid.uuid4())
            item = {"id": identifier, "name": name.strip(), "path": str(root), "created_at": registry.get(identifier, {}).get("created_at", workbench.now()), "updated_at": workbench.now()}
            registry[identifier] = item
            self.save(self.directory / "registry.json", registry)
            return item

    def get(self, identifier):
        with self.lock:
            registry = self.read_registry()
            if identifier not in registry:
                raise HTTPException(404, "项目未登记")
            return registry[identifier]

    def remove(self, identifier):
        with self.lock:
            registry = self.read_registry()
            if identifier not in registry:
                raise HTTPException(404, "项目未登记")
            del registry[identifier]
            self.save(self.directory / "registry.json", registry)
        return {"removed": True, "files_deleted": False}

    def detail(self, identifier):
        project = self.get(identifier)
        root = Path(project["path"])
        if not root.is_dir():
            return {**project, "git": {"available": False, "branch": None, "head": None, "status": []}, "files": [], "error": "登记目录已移动或不可访问"}
        info = git_info(root)
        if info["available"]:
            names = git(root, ["ls-files", "--cached", "--others", "--exclude-standard", "-z"]).stdout.decode("utf-8", errors="replace").split("\x00")
        else:
            names = []
            for base, directories, filenames in os.walk(root, followlinks=False):
                directories[:] = [name for name in directories if not excluded(name)]
                names.extend(str((Path(base) / name).relative_to(root)) for name in filenames)
                if len(names) > 3000:
                    break
        status = {item["path"]: item["status"] for item in info["status"]}
        files, omitted = [], []
        for name in sorted(set(filter(None, names))):
            try:
                checked_path(root, name)
                if excluded(name):
                    omitted.append({"path": name, "reason": "认证文件或缓存已排除"})
                    continue
            except ValueError as exc:
                omitted.append({"path": name, "reason": str(exc)})
                continue
            files.append({"path": name, "status": status.get(name, "tracked" if info["available"] else "file")})
        return {**project, "git": info, "files": files[:1000], "omitted": omitted[:1000], "truncated": len(files) > 1000}

    def build_snapshot(self, identifier, request, persist=True):
        project = self.get(identifier)
        root = Path(project["path"])
        if not root.is_dir():
            raise ValueError("项目目录不可访问")
        info = git_info(root)
        if request.scope != "selected" and (not info["available"] or not info["head"]):
            raise ValueError("该范围需要已存在提交的 Git 项目；非 Git 项目可选择文件评审")
        base = info["head"]
        current = None
        arguments = ["diff", "--no-ext-diff", "--no-textconv"]
        if request.scope == "staged":
            current = ""
            arguments += ["--cached", "HEAD"]
        elif request.scope == "last_commit":
            parent = git(root, ["rev-parse", "--verify", "HEAD^"], check=False)
            base = parent.stdout.decode().strip() if not parent.returncode else None
            current = info["head"]
            arguments = ["diff", "--no-ext-diff", "--no-textconv", base, "HEAD"] if base else ["diff-tree", "--root", "--no-commit-id", "-r", "--no-ext-diff", "--no-textconv", "HEAD"]
        elif request.scope == "branch":
            if not request.base_ref or request.base_ref.startswith("-") or not re.fullmatch(r"[\w./-]{1,200}", request.base_ref):
                raise ValueError("请选择合法且已存在的基准分支")
            base = git(root, ["merge-base", request.base_ref, "HEAD"]).stdout.decode().strip()
            current = info["head"]
            arguments += [base, "HEAD"]
        elif request.scope == "working":
            arguments += ["HEAD"]
        candidates = []
        if request.scope == "selected":
            if not request.files:
                raise ValueError("请至少选择一个文件")
            base = None
            candidates = [{"path": name, "status": "selected", "base_path": name} for name in dict.fromkeys(request.files)]
        else:
            entries = git(root, [*arguments, "--name-status", "-z"]).stdout.decode("utf-8", errors="replace").split("\x00")
            index = 0
            while index < len(entries) and entries[index]:
                status = entries[index]
                index += 1
                old_path = entries[index]
                index += 1
                new_path = old_path
                if status.startswith(("R", "C")):
                    new_path = entries[index]
                    index += 1
                candidates.append({"path": new_path, "base_path": old_path, "status": status})
            if request.scope == "working":
                untracked = git(root, ["ls-files", "--others", "--exclude-standard", "-z"]).stdout.decode("utf-8", errors="replace").split("\x00")
                candidates += [{"path": name, "base_path": name, "status": "??"} for name in untracked if name]
            if request.files:
                selected = set(request.files)
                known = {item["path"] for item in candidates}
                if not selected.issubset(known):
                    raise ValueError("所选文件不属于当前改动范围，请重新预览")
                candidates = [item for item in candidates if item["path"] in selected]
        files, omitted, truncated = [], [], []
        remaining = MAX_CONTEXT
        for index, candidate in enumerate(candidates):
            name = candidate["path"]
            try:
                checked_path(root, name)
                checked_path(root, candidate["base_path"])
                if index >= MAX_FILES or remaining < 500:
                    omitted.append({"path": name, "reason": "达到 20 文件或 60000 字符总预算"})
                    continue
                old_text, old_error = read_bytes(root, candidate["base_path"], base) if base else (None, None)
                new_text, new_error = read_bytes(root, name, current)
                reason = old_error or new_error
                if reason:
                    omitted.append({"path": name, "reason": reason})
                    continue
                if old_text is None and new_text is None:
                    omitted.append({"path": name, "reason": "没有可读取文本"})
                    continue
                old_anchor = new_anchor = 1
                if request.scope != "selected":
                    patch_arguments = [*arguments, "--unified=0", "--", name, candidate["base_path"]]
                    patch_text = git(root, patch_arguments).stdout.decode("utf-8", errors="replace")
                    hunk = re.search(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", patch_text)
                    if hunk:
                        old_anchor, new_anchor = max(1, int(hunk[1])), max(1, int(hunk[2]))
                old, remaining, old_trimmed = excerpt(old_text, old_anchor, remaining)
                new, remaining, new_trimmed = excerpt(new_text, new_anchor, remaining)
                if old is None and new is None:
                    omitted.append({"path": name, "reason": "没有剩余内容预算"})
                    continue
                if old_trimmed or new_trimmed:
                    truncated.append({"path": name, "reason": "仅保留首个改动附近至多 250 行/18000 字符；其他行未纳入", "sides": [side for side, trimmed in [("base", old_trimmed), ("current", new_trimmed)] if trimmed]})
                files.append({**candidate, "base": old, "current": new})
            except (ValueError, OSError) as exc:
                omitted.append({"path": name, "reason": str(exc)[:200]})
        coverage = {"scope": request.scope, "description": DESCRIPTIONS[request.scope], "files_in_scope": len(candidates), "files_included": len(files), "omitted_count": len(omitted), "truncated_count": len(truncated), "complete": not omitted and not truncated, "disclaimer": "只审查此次快照中展示的文本与行范围；模型发现未经实际运行、测试或人工核实。"}
        snapshot = {"id": str(uuid.uuid4()), "project_id": identifier, "project_name": project["name"], "path_hash": hashlib.sha256(str(root).encode()).hexdigest(), "scope": request.scope, "base_ref": request.base_ref, "head": info["head"], "files": files, "omitted": omitted, "truncated": truncated, "coverage": coverage, "request": request.model_dump()}
        snapshot["digest"] = digest(snapshot | {"id": None})
        snapshot["created_at"] = workbench.now()
        if persist:
            self.save(self.directory / "snapshots" / (snapshot["id"] + ".json"), snapshot)
        return snapshot

    def snapshot(self, project_id, snapshot_id, validate=False):
        project = self.get(project_id)
        try:
            identifier = str(uuid.UUID(snapshot_id))
        except ValueError:
            raise HTTPException(404, "快照不存在")
        path = self.directory / "snapshots" / (identifier + ".json")
        if not path.exists():
            raise HTTPException(404, "快照不存在")
        snapshot = json.loads(path.read_text())
        if snapshot["project_id"] != project_id or snapshot["path_hash"] != hashlib.sha256(project["path"].encode()).hexdigest():
            raise HTTPException(409, "快照不属于当前项目路径")
        body = {key: value for key, value in snapshot.items() if key not in {"digest", "created_at"}}
        body["id"] = None
        if digest(body) != snapshot["digest"]:
            raise HTTPException(409, "快照校验失败，请重新预览")
        if validate:
            rebuilt = self.build_snapshot(project_id, SnapshotRequest(**snapshot["request"]), persist=False)
            if rebuilt["digest"] != snapshot["digest"]:
                raise HTTPException(409, "项目内容或 HEAD 已变化，请重新预览后评审")
        return snapshot


class ProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    path: str = Field(min_length=1, max_length=4096)

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value):
        if not value.strip():
            raise ValueError("请输入项目名称")
        return value.strip()


class SnapshotRequest(BaseModel):
    scope: SCOPES = "working"
    base_ref: str | None = Field(default=None, max_length=200)
    files: list[str] = Field(default_factory=list, max_length=1000)


class ReviewRequest(BaseModel):
    snapshot_id: str
    engine: Literal["chat", "council", "camel", "crewai"] = "council"
    models: list[str] = Field(min_length=1, max_length=4)
    chairman: str | None = None
    rounds: int = Field(default=2, ge=1, le=6)
    prompt: str = Field(default="检查所选范围中的真实缺陷，给出可核对的文件与行号。", max_length=12000)


store = ProjectStore()


@router.get("")
async def list_projects():
    def read():
        return [{**project, "git": git_info(Path(project["path"])) if Path(project["path"]).is_dir() else {"available": False, "branch": None, "head": None, "status": []}} for project in store.read_registry().values()]
    return await asyncio.to_thread(read)


@router.post("", status_code=201)
async def register_project(request: ProjectRequest):
    try:
        return await asyncio.to_thread(store.register, request.name, request.path)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/{project_id}")
async def project_detail(project_id: str):
    return await asyncio.to_thread(store.detail, project_id)


@router.put("/{project_id}")
async def update_project(project_id: str, request: ProjectRequest):
    try:
        return await asyncio.to_thread(store.register, request.name, request.path, project_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/{project_id}")
async def remove_project(project_id: str):
    return await asyncio.to_thread(store.remove, project_id)


@router.post("/{project_id}/snapshots", status_code=201)
async def preview_snapshot(project_id: str, request: SnapshotRequest):
    try:
        return await asyncio.to_thread(store.build_snapshot, project_id, request)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/{project_id}/snapshots/{snapshot_id}")
async def get_snapshot(project_id: str, snapshot_id: str):
    return await asyncio.to_thread(store.snapshot, project_id, snapshot_id)


@router.get("/{project_id}/snapshots")
async def list_snapshots(project_id: str):
    store.get(project_id)
    items = []
    for path in (store.directory / "snapshots").glob("*.json"):
        try:
            value = json.loads(path.read_text())
            if value.get("project_id") == project_id:
                items.append({key: value.get(key) for key in ["id", "scope", "created_at", "coverage", "digest", "head"]})
        except (ValueError, OSError):
            continue
    return sorted(items, key=lambda item: item["created_at"] or "", reverse=True)[:100]


@router.post("/{project_id}/reviews", status_code=202)
async def create_review(project_id: str, request: ReviewRequest):
    snapshot = await asyncio.to_thread(store.snapshot, project_id, request.snapshot_id, True)
    if not snapshot["files"]:
        raise HTTPException(422, "快照没有可评审文本，请更换范围或文件")
    if request.engine != "chat" and len(request.models) < 2:
        raise HTTPException(422, "此评审引擎至少需要两个模型")
    if request.engine in {"camel", "crewai"} and len(request.models) != 2:
        raise HTTPException(422, "角色评审引擎需要恰好两个模型")
    try:
        catalog = await workbench.model_catalog()
        selected = request.models + ([request.chairman] if request.chairman else [])
        if any(model not in catalog for model in selected):
            raise ValueError("选择的模型不在本地网关列表中")
        if request.engine in {"camel", "crewai"} and not (workbench.TOOLS / (".venv-" + request.engine) / "bin/python").exists():
            raise ValueError("该评审框架尚未安装")
        job_request = workbench.JobRequest(mode="review", prompt=request.prompt or "检查代码中的真实缺陷。", models=request.models, chairman=request.chairman, rounds=request.rounds, project_id=project_id, snapshot_id=request.snapshot_id, engine=request.engine)
    except (ValueError, httpx.HTTPError) as exc:
        raise HTTPException(422, workbench.public_error(exc)) from exc
    job = workbench.manager.create(job_request)
    existing = workbench.manager.get(job["id"])
    existing.update(project_name=snapshot["project_name"], snapshot_digest=snapshot["digest"], coverage=snapshot["coverage"])
    workbench.manager.save(existing)
    return existing
