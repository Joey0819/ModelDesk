"""QA-only DOCX rendering with explicit host fonts and per-page evidence.

Production exports use python-docx and isolated Chromium, not LibreOffice.
This optional acceptance helper needs soffice, pdftoppm and project Python deps.
Example:
  .venv/bin/python scripts/verify_document_render.py report.docx --output data/qa
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unicodedata
from xml.sax.saxutils import escape

from docx import Document
from pypdf import PdfReader


def normalize(value):
    return "".join(unicodedata.normalize("NFKC", value).split())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--font-dir", type=Path, action="append", default=[])
    parser.add_argument("--soffice", default=shutil.which("soffice"))
    parser.add_argument("--pdftoppm", default=shutil.which("pdftoppm"))
    args = parser.parse_args()
    source, output = args.document.resolve(), args.output.resolve()
    if source.suffix.lower() != ".docx" or not source.is_file():
        parser.error("document must be an existing DOCX")
    if not args.soffice or not args.pdftoppm:
        parser.error("QA requires soffice and pdftoppm; pass their absolute paths")
    font_dirs = args.font_dir or [Path("/System/Library/Fonts"), Path("/Library/Fonts"),
                                 Path("/usr/share/fonts"), Path("/usr/local/share/fonts")]
    font_dirs = [path.resolve() for path in font_dirs if path.is_dir()]
    if not font_dirs:
        parser.error("No host fonts found; pass --font-dir with installed CJK fonts")
    output.mkdir(parents=True, exist_ok=True)
    cache = output / "font-cache"
    cache.mkdir(exist_ok=True)
    config = output / "fonts.conf"
    config.write_text('<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
                      + "".join("<dir>" + escape(str(path)) + "</dir>" for path in font_dirs)
                      + "<cachedir>" + escape(str(cache)) + "</cachedir></fontconfig>")
    environment = {**os.environ, "FONTCONFIG_FILE": str(config)}
    # A fresh conversion directory prevents an old PDF being mistaken for a
    # successful render after LibreOffice exits without producing a new file.
    source_bytes = source.read_bytes()
    with tempfile.TemporaryDirectory(prefix="modeldesk-word-qa-") as temporary:
        root = Path(temporary).resolve()
        frozen = root / source.name
        frozen.write_bytes(source_bytes)
        rendered = root / "rendered"
        rendered.mkdir()
        convert = [args.soffice, "-env:UserInstallation=" + (root / "profile").as_uri(),
                   "--headless", "--convert-to", "pdf", "--outdir", str(rendered), str(frozen)]
        result = subprocess.run(convert, env=environment, capture_output=True, text=True, timeout=120, check=True)
        fresh_pdf = rendered / (source.stem + ".pdf")
        if not fresh_pdf.is_file():
            raise RuntimeError("LibreOffice did not produce a PDF: " + result.stderr[-1000:])
        pdf = output / (source.stem + ".pdf")
        pdf.write_bytes(fresh_pdf.read_bytes())
    if source.read_bytes() != source_bytes:
        raise RuntimeError("Source DOCX changed during QA; rerun on the final file")
    reader = PdfReader(pdf)
    all_text = "\n".join(page.extract_text() for page in reader.pages)
    document = Document(source)
    chunks = [p.text for p in document.paragraphs]
    chunks.extend(p.text for table in document.tables for row in table.rows for cell in row.cells for p in cell.paragraphs)
    missing = [text for text in chunks if normalize(text) and normalize(text) not in normalize(all_text)]
    images = []
    for number in range(1, len(reader.pages) + 1):
        prefix = output / f"page-{number}"
        subprocess.run([args.pdftoppm, "-f", str(number), "-l", str(number), "-singlefile", "-scale-to", "1600",
                        "-png", str(pdf), str(prefix)], capture_output=True, timeout=60, check=True)
        images.append(str(prefix) + ".png")
    report = {"source": str(source), "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
              "pdf": str(pdf), "pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
              "pages": len(reader.pages), "page_images": images, "missing_source_paragraphs": missing,
              "text_readback_passed": not missing, "font_directories": [str(path) for path in font_dirs],
              "fontconfig_file": str(config), "soffice": args.soffice, "pdftoppm": args.pdftoppm,
              "requires_visual_review": True, "production_dependency": False,
              "renderer_log": (result.stdout + result.stderr)[-4000:]}
    (output / "qa-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    (output / "extracted-text.txt").write_text(all_text)
    print(json.dumps({"report": str(output / "qa-report.json"), "pages": len(reader.pages), "text_readback_passed": not missing}))
    if missing:
        raise SystemExit("Rendered text differs; inspect fonts and page images before accepting")


if __name__ == "__main__":
    main()
