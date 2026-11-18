"""Project safely packaged prose HTML into the existing Markdown pipeline."""
import re
import string

from lxml import etree, html
from markdown_it import MarkdownIt


_REPORT = re.compile(r"报告|简报|文章|纪要|总结|说明书|\b(?:report|brief|article|summary|memo|write-up|documentation)\b", re.I)
_SOURCE = re.compile(
    r"(?:html|网页|页面|web\s*(?:page|site))\s*(?:源代码|源码|代码(?:示例)?|source\b|code\b)"
    r"|(?:源代码|源码|source\s+code\b)\s*(?:的\s*|for\s+(?:an?\s+)?)?(?:html\b|网页|页面)"
    r"|(?:html|网页)\s*(?:示例|模板)|\b(?:standalone|interactive)\s+(?:web\s*)?(?:app|site|page)\b"
    r"|\b(?:build|implement|code|develop)\s+(?:an?\s+|the\s+)?(?:website|web\s*app|html\s+(?:app|page|template))\b"
    r"|(?:实现|开发)\s*(?:一个\s*)?(?:网站|Web\s*应用|HTML\s*(?:页面|应用|模板))"
    r"|(?:交互式|可直接运行的)\s*(?:网页|网站|页面|HTML)", re.I,
)
_DOCUMENT = re.compile(r"(?:<!doctype\s+html\b[^>]*>\s*)?<html(?:\s|>).*?</html\s*>\Z", re.I | re.S)
_PUNCTUATION = re.compile(f"[{re.escape(string.punctuation)}]")
_BLOCKS = {"address", "article", "aside", "div", "figure", "figcaption", "footer", "header", "main", "p", "section"}
_DROP = {"audio", "canvas", "embed", "form", "head", "iframe", "math", "noscript", "object", "script", "style", "svg", "template"}
_FENCE_PARSER = MarkdownIt("commonmark", {"html": False})


def _plain(value):
    return _PUNCTUATION.sub(lambda match: "\\" + match[0], re.sub(r"[ \t\r\n\f]+", " ", value or ""))


def _code(value, block=False):
    width = max((len(match[0]) for match in re.finditer(r"`+", value)), default=0) + 1
    fence = "`" * (max(3, width) if block else width)
    return f"\n\n{fence}\n{value.strip(chr(10))}\n{fence}\n\n" if block else f"{fence} {value} {fence}"


def _media_urls(job, delivery):
    from .delivery_media import _urls

    # The generated link is always rebuilt from this job's own manifest. Normal
    # ownership and byte verification still happen in the renderer/download API.
    identifier = r"[0-9a-f-]{36}"
    job_id = job.get("id") or ""
    project_id = job.get("project_id")
    if not re.fullmatch(identifier, job_id) or (project_id and not re.fullmatch(identifier, project_id)):
        return {}
    return {url: asset["kind"] for asset in (delivery.get("media") or [])
            if isinstance(asset, dict) and asset.get("kind") in {"image", "video"}
            and asset.get("job_id") == job_id and asset.get("project_id") == project_id
            and re.fullmatch(identifier, asset.get("id") or "")
            for url in _urls(job_id, project_id, asset["id"])}


def _markdown(node, media):
    tag = node.tag
    if not isinstance(tag, str) or tag in _DROP or "hidden" in node.attrib:
        return ""
    if tag in {"pre", "code"}:
        return _code(node.text_content(), block=tag == "pre")
    if tag == "br":
        return "  \n"
    if tag == "hr":
        return "\n\n---\n\n"
    if tag in {"img", "video"}:
        url = node.get("src") or ""
        caption = _plain(re.sub(r"[\[\]]", " ", node.get("alt") or node.get("title") or ("图片" if tag == "img" else "视频")))
        if media.get(url) == "image" and tag == "img":
            return f"![{caption}]({url})"
        if media.get(url) == "video" and tag == "video":
            return f"[{caption}]({url})"
        return caption
    if tag == "table":
        rows = [[" ".join(_markdown(cell, media).split()) for cell in row if cell.tag in {"td", "th"}]
                for row in node.xpath("./tr | ./thead/tr | ./tbody/tr | ./tfoot/tr")]
        rows = [row for row in rows if row]
        if not rows:
            return ""
        width = max(map(len, rows))
        # A single wide row in a large, ragged table should not multiply the output size.
        if width * len(rows) > 10_000:
            return "\n\n" + "\n".join("- " + " · ".join(row) for row in rows) + "\n\n"
        lines = ["| " + " | ".join(row + [""] * (width - len(row))) + " |" for row in rows]
        lines.insert(1, "| " + " | ".join(["---"] * width) + " |")
        return "\n\n" + "\n".join(lines) + "\n\n"
    if tag in {"ul", "ol"}:
        lines = []
        for i, child in enumerate(node.findall("li"), 1):
            mark = f"{i}. " if tag == "ol" else "- "
            body = _markdown(child, media).strip().splitlines()
            if body:
                lines.append(mark + body[0])
                lines.extend(" " * len(mark) + line if line else "" for line in body[1:])
        return "\n\n" + "\n".join(lines) + "\n\n"

    content = _plain(node.text) + "".join(_markdown(child, media) + _plain(child.tail) for child in node)
    if re.fullmatch(r"h[1-6]", tag):
        return f"\n\n{'#' * int(tag[1])} {' '.join(content.split())}\n\n"
    if tag in {"strong", "b", "em", "i"} and content.strip():
        mark = "**" if tag in {"strong", "b"} else "*"
        return f"{mark}{content.strip()}{mark}"
    if tag == "a" and node.get("href") in media and content.strip():
        return f"[{content.strip()}]({node.get('href')})"
    if tag == "blockquote" and content.strip():
        return "\n\n" + "\n".join("> " + line for line in content.strip().splitlines()) + "\n\n"
    if tag in _BLOCKS:
        return f"\n\n{content.strip()}\n\n"
    return content


def report_markdown(job, delivery, content):
    """Convert a whole-document prose report and leave other artifacts intact."""
    if not isinstance(content, str) or len(content) > 200_000:
        return content
    prompt = (delivery.get("task_spec") or {}).get("goal") or job.get("prompt") or ""
    title = delivery.get("title") or ""
    if not _REPORT.search(title + "\n" + prompt) or _SOURCE.search(title + "\n" + prompt):
        return content
    candidate = content.strip().lstrip("\ufeff")
    if candidate.startswith(("```", "~~~")):
        tokens = _FENCE_PARSER.parse(candidate)
        if len(tokens) != 1 or tokens[0].type != "fence" or tokens[0].info.strip().lower() not in {"", "html", "htm"}:
            return content
        candidate = tokens[0].content.strip()
    if not _DOCUMENT.fullmatch(candidate):
        return content
    try:
        document = html.document_fromstring(candidate, parser=html.HTMLParser(no_network=True))
        if document.body is not None and document.body.xpath(".//p | .//li | .//td | .//h1 | .//h2"):
            markdown = _markdown(document.body, _media_urls(job, delivery)).strip()
            if markdown:
                return markdown
    except (etree.LxmlError, ValueError):
        pass
    return content


def add_report_preview(job):
    """Annotate the API's independent job copy, leaving saved evidence intact."""
    delivery = (job.get("result") or {}).get("delivery")
    if job.get("mode") == "group" and delivery:
        content = delivery.get("content")
        preview = report_markdown(job, delivery, content)
        if preview != content:
            delivery["preview_content"] = preview
    return job
