"""Bounded MCP catalogs and complete, attributable tool results.

Uses the installed official SDK's typed messages; it does not fetch resource
links, resolve remote JSON Schemas, or dispatch tools while discovering them.
"""
import hashlib
import json

from jsonschema import validators
from mcp.types import PaginatedRequestParams
from referencing import Registry

MAX_CATALOG_PAGES = 32
MAX_CATALOG_TOOLS = 1024
MAX_CATALOG_CHARS = 2_000_000


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


async def list_all_tools(session):
    """Read opaque cursors within this session, failing instead of partial lists."""
    tools, names, cursors = [], set(), set()
    cursor, size = None, 0
    for _ in range(MAX_CATALOG_PAGES):
        page = (await session.list_tools(params=PaginatedRequestParams(cursor=cursor))
                if cursor is not None else await session.list_tools())
        for tool in page.tools:
            if tool.name in names:
                raise ValueError("MCP 工具目录出现重复名称，无法确认唯一工具；未调用任何工具")
            names.add(tool.name)
            tools.append(tool)
            size += len(canonical_json(tool.model_dump(mode="json")))
            if len(tools) > MAX_CATALOG_TOOLS or size > MAX_CATALOG_CHARS:
                raise ValueError("MCP 工具目录超过安全读取上限；未返回不完整目录或调用工具")
        cursor = getattr(page, "nextCursor", None)
        if cursor is None or cursor == "":
            return tools
        if not isinstance(cursor, str) or len(cursor) > 4096 or cursor in cursors:
            raise ValueError("MCP 工具分页游标无效或循环；未返回不完整目录或调用工具")
        cursors.add(cursor)
    raise ValueError("MCP 工具目录分页过多；未返回不完整目录或调用工具")


def validate_output_schema(schema, result=None):
    if schema is None:
        return
    if not isinstance(schema, dict):
        raise ValueError("MCP 工具的输出结构无效，未确认成果")
    try:
        validator = validators.validator_for(schema)
        validator.check_schema(schema)
        if result is not None:
            if result.structuredContent is None:
                raise ValueError("missing structured result")
            validator(schema, registry=Registry()).validate(result.structuredContent)
    except Exception as error:
        # The validator's default exception includes server data; do not leak it.
        raise ValueError("MCP 工具的结构化成果未通过输出结构校验；不作为已完成成果，也不自动重发") from error


def result_provenance(value):
    text = [item["text"] for item in value.get("content", [])
            if item.get("type") == "text" and isinstance(item.get("text"), str)]
    structured = value.get("structuredContent")
    return {
        "text_content_sha256": hashlib.sha256(canonical_json(text).encode()).hexdigest(),
        "structured_content_sha256": (hashlib.sha256(canonical_json(structured).encode()).hexdigest()
                                      if structured is not None else None),
        "unverified_content_types": sorted({item.get("type", "unknown") for item in value.get("content", [])
                                            if item.get("type") != "text"}),
    }


def complete_result_text(value):
    texts = [item["text"] for item in value.get("content", [])
             if item.get("type") == "text" and isinstance(item.get("text"), str)]
    structured = value.get("structuredContent")
    if structured is not None:
        serialized = canonical_json(structured)
        mirrored = False
        for text in texts:
            try:
                # JSON equality must distinguish true from 1, including nested data.
                mirrored = canonical_json(json.loads(text)) == serialized
            except (ValueError, TypeError):
                continue
            if mirrored:
                break
        if not mirrored:
            texts.append("MCP 结构化结果（服务端原始数据）：\n" + serialized)
    return "\n\n".join(texts).strip()
