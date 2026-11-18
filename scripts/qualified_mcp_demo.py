"""Loopback MCP reference planner and bounded plain-text document renderer.

Run from the project root: python -m scripts.qualified_mcp_demo --port 8769
Register http://127.0.0.1:8769/mcp with Streamable HTTP transport. In the team
editor, configure plan_document as an MCP ruler and render_document as an MCP
text document worker, both with the prompt argument; also select an editor model.
This deterministic reference handles a team with one delivery worker and no other
required participants. It does not write files or call another service.
"""
import argparse
import base64
import hashlib
import json

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from backend.harness.contracts import Plan, Requirement, Step


class TextDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str
    content_base64: str
    sha256: str


def plan_document(prompt: str) -> Plan:
    """Return a single textual step for the team's configured MCP document worker."""
    goal_and_roster = prompt.split("用户目标：\n", 1)[1]
    _, roster_and_context = goal_and_roster.rsplit("\n成员：\n", 1)
    roster, _ = json.JSONDecoder().raw_decode(roster_and_context)
    worker = next(member for member in roster if member.get("mcp_document_worker") is True)
    return Plan(title="任务简报", requirements=[Requirement(id="brief", description="提供包含用户目标与后续步骤的可读简报")],
                steps=[Step(id="write", member_id=worker["id"], instruction="生成简要任务简报。",
                            expected_output="可下载的 UTF-8 任务简报")])


def render_document(prompt: str) -> TextDocument:
    """Render the supplied goal as an actual downloadable UTF-8 document."""
    goal = prompt.split("用户目标：\n", 1)[1].split("\n成员职责：\n", 1)[0].strip()[:1200]
    content = (f"任务简报\n========\n\n任务目标\n{goal}\n\n后续步骤\n"
               "1. 确认任务范围、负责人和完成时间。\n2. 收集资料与实际成果。\n3. 审阅成果，记录未解决事项。\n").encode()
    return TextDocument(filename="task-brief.txt", content_base64=base64.b64encode(content).decode("ascii"),
                        sha256=hashlib.sha256(content).hexdigest())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default=8769, type=int)
    args = parser.parse_args()
    server = FastMCP("ModelDesk qualified team demo", host="127.0.0.1", port=args.port)
    server.add_tool(plan_document, structured_output=True)
    server.add_tool(render_document, structured_output=True)
    server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
