"""Local, recoverable platform resources and provenance-preserving retrieval."""
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import threading
import uuid

import jieba
from rank_bm25 import BM25Plus
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

jieba.setLogLevel(40)
KINDS = {"prompts", "knowledge", "memories", "agents", "flows", "mcp"}
TOOL_IDS = {"math", "text_stats", "web_fetch", "web_search", "knowledge_search", "memory_read", "memory_save", "project_read", "mcp_call", "markdown_to_text", "statistics"}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class Resource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    content: str = Field(default="", max_length=2_000_000)
    category: str = Field(default="自定义", max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=20)
    mode: str = Field(default="chat", max_length=40)
    project_id: str | None = None
    scope: str = Field(default="global", min_length=1, max_length=100)
    role: str = Field(default="", max_length=100)
    goal: str = Field(default="", max_length=2000)
    backstory: str = Field(default="", max_length=2000)
    model: str = Field(default="", max_length=200)
    tools: list[str] = Field(default_factory=list, max_length=24)
    description: str = Field(default="", max_length=4000)
    models: list[str] = Field(default_factory=list, max_length=4)
    rounds: int = Field(default=2, ge=1, le=6)
    workflow: dict | None = None
    url: str = Field(default="", max_length=2048)
    transport: str = "streamable_http"
    enabled: bool = True
    allowed_tools: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("name")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("名称不能为空")
        return value.strip()

    @field_validator("project_id")
    @classmethod
    def project_uuid(cls, value):
        return str(uuid.UUID(value)) if value else None

    @field_validator("tags", "allowed_tools")
    @classmethod
    def bounded_items(cls, value):
        if any(not item.strip() or len(item) > 100 for item in value):
            raise ValueError("标签或工具名称无效")
        return list(dict.fromkeys(value))


class PlatformStore:
    def __init__(self, path=None):
        self.path = Path(path or Path(__file__).resolve().parents[1] / "data/platform/resources.json")
        self.lock = threading.RLock()

    def read(self):
        if not self.path.exists():
            return {kind: {} for kind in KINDS}
        try:
            value = json.loads(self.path.read_text())
            return {kind: value.get(kind, {}) for kind in KINDS}
        except (ValueError, OSError) as error:
            raise HTTPException(500, "平台资源文件无法读取，原文件已保留。") from error

    def save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        temp.chmod(0o600)
        temp.replace(self.path)

    def list(self, kind, archived=False, project_id=None, scope="all"):
        self.check_kind(kind)
        with self.lock:
            items = [copy.deepcopy(item) for item in self.read()[kind].values() if bool(item.get("archived")) == archived]
        if scope not in {"all", "global", "project"} or (scope == "project" and not project_id):
            raise HTTPException(422, "资源范围无效；项目范围需要选择项目")
        if scope == "global":
            items = [item for item in items if not item.get("project_id")]
        elif scope == "project":
            items = [item for item in items if item.get("project_id") == project_id]
        elif project_id:
            items = [item for item in items if item.get("project_id") in {None, project_id}]
        return sorted(items, key=lambda item: item.get("updated_at", ""), reverse=True)

    @staticmethod
    def check_kind(kind):
        if kind not in KINDS:
            raise HTTPException(404, "资源类型不存在")

    def get(self, kind, identifier, allow_archived=False):
        self.check_kind(kind)
        try:
            identifier = str(uuid.UUID(identifier))
        except (ValueError, TypeError) as error:
            raise HTTPException(422, "资源 ID 无效") from error
        with self.lock:
            item = self.read()[kind].get(identifier)
            if not item or (item.get("archived") and not allow_archived):
                raise HTTPException(404, "资源不存在或已归档")
            return copy.deepcopy(item)

    def put(self, kind, resource, identifier=None, metadata=None):
        self.check_kind(kind)
        validated = validate_resource(kind, resource)
        with self.lock:
            data = self.read()
            existing = self.get(kind, identifier) if identifier else None
            identifier = existing["id"] if existing else str(uuid.uuid4())
            item = {**validated, "id": identifier, "created_at": existing["created_at"] if existing else timestamp(),
                    "updated_at": timestamp(), "archived": False}
            if metadata:
                item["provenance"] = metadata
            elif existing and existing.get("provenance"):
                item["provenance"] = existing["provenance"]
            if kind == "knowledge":
                item["sha256"] = hashlib.sha256(item["content"].encode()).hexdigest()
                item["chunks"] = chunks(item["content"])
            data[kind][identifier] = item
            self.save(data)
            return copy.deepcopy(item)

    def archive(self, kind, identifier, archived=True):
        with self.lock:
            item = self.get(kind, identifier, allow_archived=True)
            data = self.read()
            item.update(archived=archived, updated_at=timestamp())
            data[kind][item["id"]] = item
            self.save(data)
            return {"id": item["id"], "archived": archived, "recoverable": True}

    def import_resources(self, validated, conflict):
        """Preserve UUID references in portable backups; commit one atomic file."""
        with self.lock:
            data = self.read()
            imported, skipped, mapping = 0, 0, {}
            prepared = []
            for kind, body, old_id in validated:
                identifier = str(uuid.UUID(old_id)) if old_id else str(uuid.uuid4())
                existing = data[kind].get(identifier)
                same = next((item for item in data[kind].values() if item["name"] == body["name"] and item["content"] == body["content"] and item.get("project_id") == body.get("project_id")), None)
                if (existing or same) and conflict == "skip":
                    if old_id:
                        mapping[old_id] = (existing or same)["id"]
                    skipped += 1
                    continue
                if existing:
                    identifier = str(uuid.uuid4())
                if old_id:
                    mapping[old_id] = identifier
                prepared.append((kind, copy.deepcopy(body), identifier, old_id))
            for kind, body, identifier, old_id in prepared:
                if body.get("workflow"):
                    for field in ["knowledge_ids", "mcp_server_ids"]:
                        body["workflow"][field] = [mapping.get(value, value) for value in body["workflow"].get(field, [])]
                if conflict == "rename" and any(item["name"] == body["name"] for item in data[kind].values()):
                    body["name"] = body["name"][:180] + "（导入）"
                item = {**body, "id": identifier, "created_at": timestamp(), "updated_at": timestamp(), "archived": False,
                        "provenance": {"source": "data_import", "original_id": old_id}}
                if kind == "knowledge":
                    item.update(sha256=hashlib.sha256(item["content"].encode()).hexdigest(), chunks=chunks(item["content"]))
                data[kind][identifier] = item
                imported += 1
            self.save(data)
            return imported, skipped

    def search(self, kind, query, identifiers=None, scope=None, limit=5, project_id=None):
        if kind not in {"knowledge", "memories"}:
            raise HTTPException(422, "此资源不支持检索")
        items = self.list(kind)
        items = [item for item in items if item.get("project_id") in {None, project_id}]
        if identifiers is not None:
            records = [self.get(kind, identifier) for identifier in identifiers]
            if any(item.get("project_id") not in {None, project_id} for item in records):
                raise HTTPException(403, "所选资源不属于当前项目")
            selected = {item["id"] for item in records}
            items = [item for item in items if item["id"] in selected]
        if scope:
            items = [item for item in items if item["scope"] == scope]
        candidates = []
        for item in items:
            for number, segment in enumerate(item.get("chunks") or chunks(item["content"])):
                candidates.append({"id": item["id"], "name": item["name"], "chunk": number + 1,
                                   "start_line": segment["start_line"], "end_line": segment["end_line"],
                                   "content": segment["content"], "sha256": item.get("sha256"),
                                   "scope": item["scope"], "provenance": item.get("provenance")})
        if not candidates:
            return []
        if not query.strip():
            return candidates[:limit]
        query_tokens = tokenize(query)
        corpus = [tokenize(item["content"] + " " + item["name"]) for item in candidates]
        scores = BM25Plus(corpus).get_scores(query_tokens)
        ranked = [({**candidate, "score": float(score)}, set(words)) for candidate, words, score in zip(candidates, corpus, scores)]
        ranked = [item for item, words in ranked if set(query_tokens) & words]
        return sorted(ranked, key=lambda item: item["score"], reverse=True)[:limit]


def validate_resource(kind, value):
    item = Resource.model_validate(value).model_dump(mode="json")
    if kind in {"knowledge", "memories", "prompts"} and not item["content"].strip():
        raise HTTPException(422, "请填写内容")
    if kind == "agents" and (not item["role"].strip() or not item["goal"].strip() or not item["model"]):
        raise HTTPException(422, "智能体需要角色、目标与模型")
    if not set(item["tools"]) <= TOOL_IDS:
        raise HTTPException(422, "存在未注册工具")
    if kind == "flows":
        from .workflow_config import validate_workflow
        if not item["workflow"] or not item["models"]:
            raise HTTPException(422, "流程需要参与模型、角色与任务配置")
        item["workflow"] = validate_workflow(item["workflow"], item["mode"], item["models"]).model_dump(mode="json")
    if kind == "mcp":
        from urllib.parse import urlsplit
        url = urlsplit(item["url"])
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.fragment:
            raise HTTPException(422, "请输入不含账户凭据的 HTTP(S) MCP 地址")
        if item["transport"] not in {"streamable_http", "sse"}:
            raise HTTPException(422, "仅支持 HTTP 与 SSE 传输")
    return item


def tokenize(value):
    return [token.lower() for token in jieba.lcut(value[:2_000_000]) if re.search(r"[\w\u4e00-\u9fff]", token)] or ["_"]


def chunks(content, size=1600):
    result, text, first, line = [], [], 1, 1
    length = 0
    for raw in content.splitlines(keepends=True):
        # Long individual lines are split as well; offsets retain source line.
        for offset in range(0, len(raw), size):
            part = raw[offset:offset + size]
            if text and length + len(part) > size:
                result.append({"start_line": first, "end_line": line - 1 if offset == 0 else line, "content": "".join(text)})
                text, length, first = [], 0, line
            text.append(part)
            length += len(part)
        line += 1
    if text:
        result.append({"start_line": first, "end_line": max(first, line - 1), "content": "".join(text)})
    return result


store = PlatformStore()
