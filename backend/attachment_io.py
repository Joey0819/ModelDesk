"""Standard-library-only reader for validated, owned job image copies."""
import base64
import hashlib
import os
from pathlib import Path
import stat
import uuid

INPUT_ROOT = Path(__file__).resolve().parents[1] / "data/workbench/attachment-inputs"
MAX_IMAGES = 8
MAX_IMAGE_BYTES = 10_000_000
MAX_TOTAL_IMAGE_BYTES = 40_000_000
MIMES = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}


def verified_image_bytes(item):
    path = Path(item["path"])
    relative = path.relative_to(INPUT_ROOT)
    if len(relative.parts) != 2 or str(uuid.UUID(relative.parts[0])) != item["job_id"] or str(uuid.UUID(path.stem)) != item["id"]:
        raise ValueError("图片不是当前任务的有效附件副本")
    if path.suffix not in MIMES or item["mime"] != MIMES[path.suffix]:
        raise ValueError("图片类型不匹配")
    if any(parent.is_symlink() for parent in [path, path.parent, INPUT_ROOT]) or not path.resolve().is_relative_to(INPUT_ROOT.resolve()):
        raise ValueError("附件副本不能使用符号链接")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("图片副本必须是普通文件")
        raw = stream.read(MAX_IMAGE_BYTES + 1)
    if len(raw) > MAX_IMAGE_BYTES or hashlib.sha256(raw).hexdigest() != item["sha256"]:
        raise ValueError("图片副本校验失败")
    if not ((item["mime"] == "image/png" and raw.startswith(b"\x89PNG\r\n\x1a\n")) or
            (item["mime"] == "image/jpeg" and raw.startswith(b"\xff\xd8\xff")) or
            (item["mime"] == "image/webp" and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP")):
        raise ValueError("图片内容格式不匹配")
    return raw


def image_content_parts(job):
    items = job.get("_attachments") or []
    if len(items) > MAX_IMAGES:
        raise ValueError("每轮最多使用 8 张图片")
    parts, total = [], 0
    for item in items:
        raw = verified_image_bytes(item)
        total += len(raw)
        if total > MAX_TOTAL_IMAGE_BYTES:
            raise ValueError("本轮图片总量超过 40 MB")
        parts.append({"type": "image_url", "image_url": {"url": "data:" + item["mime"] + ";base64," + base64.b64encode(raw).decode("ascii"), "detail": "auto"}})
    return parts
