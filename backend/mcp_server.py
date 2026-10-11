"""A real local MCP server exposing useful deterministic analysis tools."""
import math
import statistics

from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse

server = FastMCP("ModelDesk 本机分析工具", host="127.0.0.1", port=8371, stateless_http=True, json_response=True)


@server.tool()
def descriptive_statistics(values: list[float]) -> dict:
    """Compute count, mean, median, standard deviation and extrema."""
    if not 1 <= len(values) <= 10000 or not all(math.isfinite(value) for value in values):
        raise ValueError("请提供 1–10000 个有限数值")
    result = {"count": len(values), "mean": statistics.mean(values), "median": statistics.median(values),
              "stdev": statistics.stdev(values) if len(values) > 1 else 0, "min": min(values), "max": max(values)}
    if not all(math.isfinite(value) for value in result.values()):
        raise ValueError("统计结果超出有限范围")
    return result


@server.tool()
def text_statistics(text: str) -> dict:
    """Count text characters, whitespace-delimited words, and lines."""
    if len(text) > 100000:
        raise ValueError("文本过长")
    return {"characters": len(text), "words": len(text.split()), "lines": len(text.splitlines())}


@server.custom_route("/health", methods=["GET"])
async def health(_request):
    return JSONResponse({"status": "ok", "service": "modeldesk-local-mcp"})


if __name__ == "__main__":
    server.run(transport="streamable-http")
