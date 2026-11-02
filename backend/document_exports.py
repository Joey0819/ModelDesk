"""Render observed artifacts as editable Word, paginated PDF, or offline HTML.

Content is parsed with markdown-it-py, never executed. Media must belong to the
selected job and pass the durable store's hash/ownership checks. PDF rendering
uses a separate, network-denied Chromium context, not the user's browser.
"""
import asyncio
import base64
from dataclasses import dataclass, field
import hashlib
import html
import io
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlsplit

from fastapi import HTTPException
from markdown_it import MarkdownIt

from .artifacts import selected_artifact
from . import delivery_media

FORMATS = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf", "html": "text/html; charset=utf-8",
}
LAYOUTS = {
    "report": {"name": "正式报告", "font": 11, "line": 1.5, "margin": 22},
    "article": {"name": "图文文章", "font": 12, "line": 1.75, "margin": 24},
    "compact": {"name": "紧凑简报", "font": 10, "line": 1.35, "margin": 16},
}
MAX_CONTENT = 200_000
MAX_RENDER_BYTES = 80_000_000
_pdf_slots = asyncio.Semaphore(2)


def _parser():
    return MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])


def _body_tokens(content, title):
    """Omit only a redundant opening title, leaving source text/hash intact."""
    tokens = _parser().parse(content)
    if (len(tokens) < 3 or tokens[0].type != "heading_open"
            or tokens[1].type != "inline" or tokens[2].type != "heading_close"):
        return tokens
    children = tokens[1].children or []
    # Preserve headings carrying links/images, which contain more than a title.
    allowed = {"text", "code_inline", "strong_open", "strong_close", "em_open", "em_close", "s_open", "s_close", "softbreak", "hardbreak"}
    if any(token.type not in allowed for token in children):
        return tokens
    heading = "".join(" " if token.type in {"softbreak", "hardbreak"} else token.content for token in children)
    def normalize(value):
        return " ".join(unicodedata.normalize("NFKC", value).split())
    return tokens[3:] if normalize(heading) == normalize(title) else tokens


def _safe_link(value):
    try:
        parsed = urlsplit(value)
        return (parsed.scheme in {"https", "http"} and bool(parsed.hostname)
                and not parsed.username and not parsed.password
                and not any(ord(char) < 32 for char in value))
    except (TypeError, ValueError):
        return False


@dataclass
class DocumentSource:
    job_id: str
    title: str
    source: str
    content: str
    partial: bool
    note: str
    tokens: list
    source_sha256: str = ""
    images: dict = field(default_factory=dict)
    videos: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    @property
    def status(self):
        return "部分输出；整体任务尚未完成" if self.partial else "已保存的实际输出"


def prepare_document(job, artifact_id, include_video=False):
    artifact = selected_artifact(job, artifact_id)
    content = artifact["content"]
    if len(content) > MAX_CONTENT or len(artifact["title"]) > 1000:
        raise HTTPException(422, "文档正文超过 200000 字符或标题超过 1000 字符")
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff]", content + artifact["title"]):
        raise HTTPException(422, "文档包含不支持的控制字符，请先修正文档正文")
    # Do not place an HTML/code artifact into an executable document context.
    if artifact["code"]:
        fence = "`" * max(3, max((len(item) for item in re.findall(r"`+", content)), default=0) + 1)
        content = f"{fence}\n{content}\n{fence}"
    document = DocumentSource(str(job["id"]), artifact["title"], artifact["source"], content,
                              artifact["partial"], artifact.get("note", ""), [],
                              source_sha256=hashlib.sha256(artifact["content"].encode()).hexdigest())
    if artifact_id == "group:delivery":
        delivery = (job.get("result") or {}).get("delivery") or {}
        references = delivery.get("media") or []
        if not isinstance(references, list) or len(references) > delivery_media.MAX_JOB_ASSETS:
            raise HTTPException(409, "交付媒体清单无效")
        entries, seen, total = [], set(), 0
        for reference in references:
            if not isinstance(reference, dict) or reference.get("id") in seen:
                raise HTTPException(409, "交付媒体清单无效或重复")
            entry, raw = delivery_media.store.read(job, reference.get("id"))
            for key in ("id", "job_id", "project_id", "kind", "mime_type", "name", "sha256", "size", "url", "download_url"):
                if reference.get(key) != entry[key]:
                    raise HTTPException(409, "交付媒体与已验证成果不匹配")
            seen.add(entry["id"])
            entries.append(entry)
            if entry["kind"] == "image" or include_video:
                total += len(raw)
                if total > 50_000_000:
                    raise HTTPException(422, "文档内媒体总大小超过 50 MB，请使用图文成果包")
                target = document.images if entry['kind'] == 'image' else document.videos
                target["media/" + entry["name"]] = {"entry": entry, "raw": raw}
            else:
                document.warnings.append("视频不嵌入打印文档，请从成果面板或图文成果包查看原视频：" + (entry["caption"] or entry["name"]))
        document.content = delivery_media._portable_markdown(content, entries, delivery.get("media_reuse_origins"))
    document.tokens = _body_tokens(document.content, document.title)
    for block in document.tokens:
        for token in block.children or []:
            if token.type == "image" and token.attrGet("src") not in document.images:
                document.warnings.append("未嵌入未经本任务验证的图片：" + (token.content or "无说明图片")[:200])
    document.warnings = list(dict.fromkeys(document.warnings))
    return document


def _css(layout):
    spec = LAYOUTS[layout]
    return f"""
@page{{size:A4;margin:{spec['margin']}mm}}
*{{box-sizing:border-box}}body{{font:{spec['font']}pt/{spec['line']} Arial,'Arial Unicode MS','Noto Sans CJK SC','Microsoft YaHei','PingFang SC',sans-serif;color:#202936;background:white;margin:0;overflow-wrap:anywhere}}
main{{max-width:820px;margin:auto;padding:36px}}h1{{font-size:26pt;line-height:1.25;color:#111827;margin:0 0 14pt}}h2{{font-size:18pt}}h3{{font-size:14pt}}h1,h2,h3,h4,h5,h6{{break-after:avoid}}p{{orphans:3;widows:3;margin:0 0 10pt}}.meta,.caption,figcaption{{font-size:9pt;color:#667085}}.meta{{padding-bottom:14pt;border-bottom:1px solid #dce1e8;margin-bottom:20pt}}.notice{{padding:10pt;border:1px solid #dce1e8;background:#f7f8fa;white-space:pre-wrap;margin:12pt 0}}.partial{{color:#864b1c;border-color:#dfc1a3}}img{{display:block;max-width:100%;max-height:215mm;width:auto;height:auto;object-fit:contain;margin:12pt auto;break-inside:avoid}}figure{{margin:16pt 0}}a{{color:#334e91;overflow-wrap:anywhere}}pre{{white-space:pre-wrap;word-break:break-word;background:#f4f6f8;padding:10pt;font:9pt/1.5 ui-monospace,monospace}}code{{font-size:.9em}}blockquote{{margin:10pt 0;padding-left:14pt;border-left:3px solid #b7c4d3;color:#435164}}table{{width:100%;border-collapse:collapse;table-layout:fixed;margin:14pt 0;font-size:.9em}}td,th{{border:1px solid #cdd5df;padding:7pt;overflow-wrap:anywhere;vertical-align:top}}th{{background:#f0f3f7;text-align:left}}thead{{display:table-header-group}}tr{{break-inside:avoid}}ul,ol{{padding-left:24pt}}hr{{border:0;border-top:1px solid #dce1e8;margin:18pt 0}}
@media print{{main{{max-width:none;padding:0}}a{{color:inherit;text-decoration:none}}}}
@media(max-width:600px){{main{{padding:20px}}h1{{font-size:23pt}}}}
video{{display:block;width:100%;height:auto;max-height:80vh;background:#111;border-radius:8px}}
"""


def html_document(document, layout="report"):
    if layout not in LAYOUTS:
        raise HTTPException(422, "不支持的文档版式")
    parser = _parser()
    used = set()

    def image_rule(renderer, tokens, index, options, env):
        token = tokens[index]
        image = document.images.get(token.attrGet("src"))
        if not image:
            return '<span class="notice">[图片未嵌入：' + html.escape(token.content or "未验证来源") + "]</span>"
        used.add(token.attrGet("src"))
        entry = image["entry"]
        return ('<img alt="' + html.escape(token.content or entry["caption"], quote=True)
                + '" src="data:' + entry["mime_type"] + ';base64,' + base64.b64encode(image["raw"]).decode() + '">')

    def link_open(renderer, tokens, index, options, env):
        target = tokens[index].attrGet("href") or ""
        video = document.videos.get(target)
        if video:
            env.setdefault('links', []).append(True)
            return '<a href="#video-' + html.escape(video['entry']['id'], quote=True) + '">'
        safe = _safe_link(target)
        env.setdefault("links", []).append(safe)
        return '<a href="' + html.escape(target, quote=True) + '" rel="noopener noreferrer">' if safe else "<span>"

    def link_close(renderer, tokens, index, options, env):
        return "</a>" if env["links"].pop() else "</span>"

    parser.add_render_rule("image", image_rule)
    parser.add_render_rule("link_open", link_open)
    parser.add_render_rule("link_close", link_close)
    body = parser.renderer.render(document.tokens, parser.options, {})
    for path, image in document.images.items():
        if path in used:
            continue
        entry = image["entry"]
        body += '<figure><img alt="' + html.escape(entry["caption"], quote=True) + '" src="data:' + entry["mime_type"] + ';base64,' + base64.b64encode(image["raw"]).decode() + '"><figcaption>' + html.escape(entry["caption"]) + '</figcaption></figure>'
    for video in document.videos.values():
        entry = video['entry']
        body += ('<figure id="video-' + html.escape(entry['id'], quote=True) + '"><video controls preload="metadata" playsinline'
                 + ' aria-label="' + html.escape(entry['caption'] or entry['name'], quote=True) + '" src="data:'
                 + entry['mime_type'] + ';base64,' + base64.b64encode(video['raw']).decode() + '"></video><figcaption>'
                 + html.escape(entry['caption'] or entry['name']) + '</figcaption></figure>')
    note = document.note + ("\n" if document.note and document.warnings else "") + "\n".join(document.warnings)
    result = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
              '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; media-src data:; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'; object-src \'none\'">'
              '<title>' + html.escape(document.title) + '</title><style>' + _css(layout) + '</style></head><body><main><h1>' + html.escape(document.title)
              + '</h1><p class="meta">' + html.escape(document.status + " · " + document.source) + '<br>任务记录：' + html.escape(document.job_id) + '</p>'
              + ('<aside class="notice partial">部分输出，尚未完整满足任务要求。</aside>' if document.partial else '') + body
              + ('<aside class="notice">' + html.escape(note) + '</aside>' if note else '') + '</main></body></html>').encode()
    if len(result) > MAX_RENDER_BYTES:
        raise HTTPException(422, "文档渲染内容超过大小上限")
    return result


def docx_document(document, layout="report"):
    from docx import Document
    from docx.shared import Inches, Mm, Pt, RGBColor
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from PIL import Image

    if layout not in LAYOUTS:
        raise HTTPException(422, "不支持的文档版式")
    spec = LAYOUTS[layout]
    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Mm(210), Mm(297)
    section.top_margin = section.bottom_margin = section.left_margin = section.right_margin = Mm(spec["margin"])
    for name in ["Normal", "Title", "Subtitle", "Caption", "List Bullet", "List Number", *[f"Heading {i}" for i in range(1, 7)]]:
        style = doc.styles[name]
        style.font.name = "Arial"
        style._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Arial Unicode MS")
        for attribute in ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme"):
            style._element.rPr.rFonts.attrib.pop(qn("w:" + attribute), None)
        for border in style._element.xpath("./w:pPr/w:pBdr"):
            border.getparent().remove(border)
        style.font.color.rgb = RGBColor.from_string("202936")
    normal = doc.styles["Normal"]
    normal.font.size = Pt(spec["font"])
    normal.paragraph_format.line_spacing = spec["line"]
    normal.paragraph_format.space_after = Pt(8)
    doc.styles["Title"].font.size = Pt(26)
    doc.styles["Title"].font.bold = True
    doc.styles["Title"].font.color.rgb = RGBColor(0, 0, 0)
    for level, size in [(1, 19), (2, 16), (3, 13)]:
        doc.styles[f"Heading {level}"].font.size = Pt(size)
    doc.core_properties.title = document.title
    doc.core_properties.author = "ModelDesk"
    doc.core_properties.subject = document.source
    doc.core_properties.comments = "任务记录：" + document.job_id
    doc.add_paragraph(document.title, "Title")
    meta = doc.add_paragraph(document.status + " · " + document.source + "\n任务记录：" + document.job_id)
    for run in meta.runs:
        run.font.size = Pt(9)
        run.font.color.rgb = RGBColor.from_string("667085")
    if document.partial:
        doc.add_paragraph("部分输出，尚未完整满足任务要求。")
    footer = section.footer.paragraphs[0]
    footer.alignment = 2
    footer.add_run("ModelDesk · ").font.size = Pt(9)
    field = OxmlElement("w:fldSimple"); field.set(qn("w:instr"), "PAGE")
    footer._p.append(field)
    width_inches = (210 - 2 * spec["margin"]) / 25.4
    used = set()

    def insert_image(paragraph, path, alt="", width_limit=None):
        image = document.images.get(path)
        if not image:
            paragraph.add_run("[图片未嵌入：" + (alt or "未验证来源") + "]")
            return
        used.add(path)
        # Word cannot read WebP. Normalize verified single-frame pixels to PNG.
        with Image.open(io.BytesIO(image["raw"])) as im:
            png = io.BytesIO()
            im.save(png, format="PNG")
            ratio = min(1 / 96, (width_limit or width_inches) / im.width, 7.5 / im.height)
            drawing = paragraph.add_run().add_picture(io.BytesIO(png.getvalue()), width=Inches(im.width * ratio), height=Inches(im.height * ratio))
            drawing._inline.docPr.set("descr", alt or image["entry"]["caption"])

    def inline(paragraph, children, width_limit=None):
        bold = italic = strike = False
        link = None
        for token in children or []:
            if token.type in {"strong_open", "strong_close"}: bold = token.type.endswith("open"); continue
            if token.type in {"em_open", "em_close"}: italic = token.type.endswith("open"); continue
            if token.type in {"s_open", "s_close"}: strike = token.type.endswith("open"); continue
            if token.type == "link_open": link = token.attrGet("href"); continue
            if token.type == "link_close": link = None; continue
            if token.type == "image": insert_image(paragraph, token.attrGet("src"), token.content, width_limit); continue
            if token.type in {"softbreak", "hardbreak"}: paragraph.add_run("\n"); continue
            if token.type not in {"text", "code_inline", "html_inline"}: continue
            run = paragraph.add_run(token.content)
            run.bold, run.italic, run.font.strike = bold, italic, strike
            if token.type == "code_inline":
                run.font.name = "Courier New"
                run.font.size = Pt(max(8, spec["font"] - 1))
            if link and _safe_link(link):
                anchor = OxmlElement("w:hyperlink")
                anchor.set(qn("r:id"), paragraph.part.relate_to(link, RT.HYPERLINK, is_external=True))
                run.font.color.rgb = RGBColor.from_string("334E91")
                anchor.append(run._r)
                paragraph._p.append(anchor)

    tokens = document.tokens
    index, current, list_stack, quote_depth = 0, None, [], 0
    while index < len(tokens):
        token = tokens[index]
        if token.type == "table_open":
            rows, row, cell = [], None, None
            index += 1
            while index < len(tokens) and tokens[index].type != "table_close":
                item = tokens[index]
                if item.type == "tr_open": row = []; rows.append(row)
                elif item.type in {"td_open", "th_open"}: cell = []; row.append(cell)
                elif item.type == "inline" and cell is not None: cell.extend(item.children or [])
                index += 1
            if rows:
                cols = max(len(row) for row in rows)
                if cols > 30 or len(rows) > 2000:
                    raise HTTPException(422, "文档表格最多 30 列和 2000 行")
                table = doc.add_table(rows=len(rows), cols=cols)
                table.style = "Table Grid"
                table.autofit = False
                for ri, row in enumerate(rows):
                    for ci, children in enumerate(row):
                        inline(table.cell(ri, ci).paragraphs[0], children, max(.25, width_inches / cols - .15))
                        if ri == 0:
                            for run in table.cell(ri, ci).paragraphs[0].runs: run.bold = True
                header = OxmlElement("w:tblHeader")
                table.rows[0]._tr.get_or_add_trPr().append(header)
            current = None
        elif token.type == "heading_open": current = doc.add_heading(level=min(6, int(token.tag[1:])))
        elif token.type == "paragraph_open":
            current = doc.add_paragraph()
            if list_stack:
                info = list_stack[-1]
                prefix = "• " if info["kind"] == "bullet_list_open" else str(info["counter"]) + ". "
                if info["first"]:
                    current.add_run(prefix)
                    info["first"] = False
                current.paragraph_format.left_indent = Inches(.22 * len(list_stack))
            if quote_depth:
                current.paragraph_format.left_indent = Inches(.2 * quote_depth)
        elif token.type == "inline":
            if current is None: current = doc.add_paragraph()
            inline(current, token.children)
        elif token.type in {"paragraph_close", "heading_close"}: current = None
        elif token.type in {"bullet_list_open", "ordered_list_open"}:
            list_stack.append({"kind": token.type, "counter": int(token.attrGet("start") or 1) - 1, "first": False})
        elif token.type == "list_item_open" and list_stack:
            list_stack[-1]["counter"] += 1; list_stack[-1]["first"] = True
        elif token.type in {"bullet_list_close", "ordered_list_close"}:
            list_stack.pop()
        elif token.type == "blockquote_open": quote_depth += 1
        elif token.type == "blockquote_close": quote_depth -= 1
        elif token.type in {"fence", "code_block"}:
            current = doc.add_paragraph()
            run = current.add_run(token.content.rstrip("\n")); run.font.name = "Courier New"; run.font.size = Pt(9)
            current = None
        elif token.type == "hr": doc.add_paragraph("—" * 16)
        index += 1
    for path, image in document.images.items():
        if path not in used:
            insert_image(doc.add_paragraph(), path)
            if image["entry"]["caption"]: doc.add_paragraph(image["entry"]["caption"], "Caption")
    notes = [document.note, *document.warnings]
    if any(notes):
        doc.add_heading("交付说明", level=2)
        for note in filter(None, notes): doc.add_paragraph(note)
    output = io.BytesIO(); doc.save(output)
    return output.getvalue()


async def pdf_document(document, layout="report"):
    from playwright.async_api import async_playwright, Error as PlaywrightError
    markup = html_document(document, layout)
    async with _pdf_slots:
        try:
            async with asyncio.timeout(90), async_playwright() as playwright:
                browser = await playwright.chromium.launch()
                try:
                    context = await browser.new_context(java_script_enabled=False, service_workers="block", accept_downloads=False)
                    await context.route("**/*", lambda route: route.abort())
                    await context.route_web_socket("**/*", lambda socket: socket.close())
                    page = await context.new_page()
                    await page.set_content(markup.decode(), wait_until="load")
                    output = await page.pdf(format="A4", print_background=True, prefer_css_page_size=True,
                                            display_header_footer=True, header_template="<span></span>",
                                            footer_template='<div style="font:8px sans-serif;color:#687080;width:100%;text-align:center">ModelDesk · <span class="pageNumber"></span> / <span class="totalPages"></span></div>')
                finally:
                    await browser.close()
        except (PlaywrightError, TimeoutError, OSError) as error:
            raise HTTPException(503, "PDF 排版引擎不可用，请修复 Chromium 运行环境后重试；Word 和 HTML 仍可导出") from error
    if not output.startswith(b"%PDF-"):
        raise HTTPException(502, "PDF 渲染没有返回有效文件")
    return output


async def document_capabilities():
    from playwright.async_api import async_playwright
    async with async_playwright() as playwright:
        pdf_ready = Path(playwright.chromium.executable_path).is_file()
    return {"formats": [{"id": name, "available": name != "pdf" or pdf_ready} for name in FORMATS],
            "layouts": [{"id": name, "name": spec["name"]} for name, spec in LAYOUTS.items()]}


async def render_document(job_snapshot, artifact_id, format="docx", layout="report"):
    if format not in FORMATS or layout not in LAYOUTS:
        raise HTTPException(422, "请选择 Word、PDF 或 HTML，以及支持的文档版式")
    document = await asyncio.to_thread(prepare_document, job_snapshot, artifact_id, format == 'html')
    if format == "pdf":
        content = await pdf_document(document, layout)
    else:
        content = await asyncio.to_thread(docx_document if format == "docx" else html_document, document, layout)
    if len(content) > MAX_RENDER_BYTES:
        raise HTTPException(422, "导出文件超过 80 MB")
    return {"content": content, "media_type": FORMATS[format], "filename": f"ModelDesk-{document.job_id[:36]}.{format}",
            "sha256": hashlib.sha256(content).hexdigest(), "source_sha256": document.source_sha256,
            "warnings": document.warnings, "format": format, "layout": layout, "partial": document.partial,
            "pages": len(__import__("pypdf").PdfReader(io.BytesIO(content)).pages) if format == "pdf" else None}
