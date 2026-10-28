"""Private, durable bytes for generated delivery media and portable exports.

Only trusted delivery executors can write here. Provider URLs are transient:
the public manifest contains scoped local URLs and verified content hashes.
"""

import asyncio
import copy
from datetime import datetime, timezone
import hashlib
import html
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import threading
from urllib.parse import quote, urljoin, urlsplit
import uuid
import warnings
import zipfile

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response


ROOT = Path(__file__).resolve().parents[1]
MAX_IMAGE_BYTES = 25_000_000
MAX_VIDEO_BYTES = 150_000_000
MAX_JOB_BYTES = 200_000_000
MAX_JOB_ASSETS = 12
MAX_IMAGE_PIXELS = 40_000_000
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
router = APIRouter(prefix="/api/media", tags=["delivery-media"])


def _identifier(value):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError()
    except (ValueError, AttributeError, TypeError) as error:
        raise HTTPException(422, "媒体标识必须是规范的 UUID") from error
    return value


def _identity(job):
    project_id = job.get("project_id")
    return _identifier(job.get("id")), _identifier(project_id) if project_id is not None else None


def _urls(job_id, project_id, asset_id):
    content = f"/api/media/jobs/{job_id}/assets/{asset_id}/content"
    if project_id is not None:
        content += "?project_id=" + quote(project_id)
    return content, content + ("&" if "?" in content else "?") + "download=1"


def _text(value, limit, label, *, required=False):
    if not isinstance(value, str) or len(value) > limit or "\x00" in value or (required and not value.strip()):
        raise HTTPException(422, f"媒体{label}无效")
    return value


def _kind_limit(kind):
    if kind not in {"image", "video"}:
        raise HTTPException(422, "只接受真实图片或视频")
    return MAX_IMAGE_BYTES if kind == "image" else MAX_VIDEO_BYTES


def _validate_image(raw):
    from PIL import Image

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in {"PNG", "JPEG", "WEBP"} or getattr(image, "n_frames", 1) != 1:
                    raise ValueError("unsupported image")
                if max(image.size) > 16384 or image.width * image.height > MAX_IMAGE_PIXELS:
                    raise ValueError("image dimensions exceed bounds")
                fmt = image.format
                image.verify()
            with Image.open(io.BytesIO(raw)) as image:
                image.load()
        return {"PNG": ("image/png", ".png"), "JPEG": ("image/jpeg", ".jpg"), "WEBP": ("image/webp", ".webp")}[fmt]
    except Exception as error:
        raise HTTPException(422, "图片不是可安全解码的单帧 PNG、JPEG 或 WebP") from error


def _validate_video(raw):
    mp4 = len(raw) >= 32 and raw[4:8] == b"ftyp" and 16 <= int.from_bytes(raw[:4], "big") <= min(len(raw), 4096)
    webm = len(raw) >= 32 and raw.startswith(b"\x1aE\xdf\xa3") and b"webm" in raw[:4096]
    if not mp4 and not webm:
        raise HTTPException(422, "视频不是有效的 MP4 或 WebM 容器")
    executable = shutil.which("ffprobe")
    if executable:
        try:
            checked = subprocess.run(
                [executable, "-v", "error", "-max_alloc", "67108864", "-protocol_whitelist", "pipe", "-probesize", "20000000",
                 "-analyzeduration", "10000000", "-select_streams", "v:0", "-show_entries",
                 "stream=codec_type,width,height:format=format_name", "-of", "json", "-i", "pipe:0"],
                input=raw, capture_output=True, timeout=15, check=True,
            )
            if len(checked.stdout) > 65536:
                raise ValueError("probe output exceeds bound")
            probe = json.loads(checked.stdout)
            streams = probe.get("streams") or []
            formats = set((probe.get("format") or {}).get("format_name", "").split(","))
            if (not streams or not any(item.get("codec_type") == "video" and item.get("width", 0) > 0
                                       and item.get("height", 0) > 0 for item in streams)
                    or not formats.intersection({"mov", "mp4", "webm", "matroska"})):
                raise ValueError("no actual video stream")
        except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError) as error:
            raise HTTPException(422, "视频校验失败：未取得有效的视频流") from error
    else:
        # A header alone is never an executable result. At minimum require a
        # media-data box / Matroska segment as well when ffprobe is unavailable.
        if (mp4 and not (b"mdat" in raw and b"moov" in raw)) or (webm and b"\x18\x53\x80\x67" not in raw):
            raise HTTPException(422, "视频容器没有实际媒体数据")
    return ("video/mp4", ".mp4") if mp4 else ("video/webm", ".webm")


def _read_file(directory_fd, name, limit):
    descriptor = os.open(name, FILE_FLAGS, dir_fd=directory_fd)
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_size > limit or details.st_nlink != 1:
            raise ValueError("unsafe media file")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(limit + 1)
        if len(raw) > limit or len(raw) != details.st_size:
            raise ValueError("media size changed")
        return raw
    finally:
        os.close(descriptor)


def _atomic_write(directory_fd, name, raw):
    temporary = "." + str(uuid.uuid4()) + ".tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(descriptor)
        os.fchmod(descriptor, 0o600)
        os.replace(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass


class DeliveryMediaStore:
    def __init__(self, directory=None):
        self.directory = Path(directory or ROOT / "data/delivery-media").absolute()
        if self.directory == Path(self.directory.anchor) or ".." in self.directory.parts:
            raise ValueError("媒体目录不能是文件系统根目录或包含路径跳转")
        self.lock = threading.RLock()

    def _root(self, *, create=False):
        # Walk using directory descriptors: rejecting every symbolic link also
        # prevents an ancestor or a concurrent rename from escaping the root.
        descriptor = os.open(self.directory.anchor, DIRECTORY_FLAGS)
        try:
            for part in self.directory.parts[1:]:
                try:
                    following = os.open(part, DIRECTORY_FLAGS, dir_fd=descriptor)
                except FileNotFoundError:
                    if not create:
                        raise
                    try:
                        os.mkdir(part, 0o700, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                    following = os.open(part, DIRECTORY_FLAGS, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = following
            if create:
                os.fchmod(descriptor, 0o700)
            return descriptor
        except Exception:
            os.close(descriptor)
            raise

    def _job(self, job_id, *, create=False):
        root_fd = self._root(create=create)
        try:
            if create:
                try:
                    os.mkdir(job_id, 0o700, dir_fd=root_fd)
                except FileExistsError:
                    pass
            descriptor = os.open(job_id, DIRECTORY_FLAGS, dir_fd=root_fd)
            if create:
                os.fchmod(descriptor, 0o700)
            return descriptor
        finally:
            os.close(root_fd)

    @staticmethod
    def _manifest(asset_fd, job_id, project_id, asset_id):
        entry = json.loads(_read_file(asset_fd, "manifest.json", 16384))
        if not isinstance(entry, dict) or (entry.get("id"), entry.get("job_id"), entry.get("project_id")) != (asset_id, job_id, project_id):
            raise ValueError("media ownership mismatch")
        allowed = {"id", "job_id", "project_id", "kind", "mime_type", "name", "url", "download_url", "sha256", "size",
                   "provider", "model", "slot_id", "caption", "created_at", "source_task_id"}
        if set(entry) - allowed:
            raise ValueError("unexpected media metadata")
        expected = {"image/png": ("image", ".png"), "image/jpeg": ("image", ".jpg"), "image/webp": ("image", ".webp"),
                    "video/mp4": ("video", ".mp4"), "video/webm": ("video", ".webm")}.get(entry.get("mime_type"))
        if not expected or entry.get("kind") != expected[0] or entry.get("name") != asset_id + expected[1]:
            raise ValueError("media type mismatch")
        if (type(entry.get("size")) is not int or not 0 < entry["size"] <= _kind_limit(entry["kind"])
                or not re.fullmatch(r"[a-f0-9]{64}", str(entry.get("sha256", "")))):
            raise ValueError("invalid media manifest")
        content_url, download_url = _urls(job_id, project_id, asset_id)
        if entry.get("url") != content_url or entry.get("download_url") != download_url:
            raise ValueError("media URL mismatch")
        for key, maximum in (("provider", 100), ("model", 200), ("slot_id", 100), ("caption", 2000)):
            _text(entry.get(key), maximum, key)
        if "source_task_id" in entry:
            _text(entry["source_task_id"], 200, "来源任务")
        return entry

    def save_bytes(self, job, raw, *, kind, provider, model="", slot_id="", caption="", source_task_id=None, mime_type=None):
        job_id, project_id = _identity(job)
        if kind == "auto":
            if not isinstance(raw, bytes):
                raise HTTPException(422, "媒体内容必须是实际字节")
            if raw.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")) or (raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"):
                kind = "image"
            elif raw[4:8] == b"ftyp" or raw.startswith(b"\x1aE\xdf\xa3"):
                kind = "video"
            else:
                raise HTTPException(422, "原生结果没有可验证的真实图片或视频")
        limit = _kind_limit(kind)
        if not isinstance(raw, bytes) or not 0 < len(raw) <= limit:
            raise HTTPException(422, "媒体为空或超过大小上限（图片 25 MB，视频 150 MB）")
        provider = _text(provider, 100, "来源", required=True)
        model = _text(model, 200, "模型")
        slot_id = _text(slot_id, 100, "位置")
        caption = _text(caption, 2000, "说明")
        if source_task_id is not None:
            _text(source_task_id, 200, "来源任务")
        detected_mime, suffix = _validate_image(raw) if kind == "image" else _validate_video(raw)
        if mime_type and mime_type.split(";", 1)[0].strip().lower() != detected_mime:
            raise HTTPException(422, "声明的媒体类型与实际文件不一致")
        asset_id = str(uuid.uuid4())
        url, download_url = _urls(job_id, project_id, asset_id)
        entry = {"id": asset_id, "job_id": job_id, "project_id": project_id, "kind": kind, "mime_type": detected_mime,
                 "name": asset_id + suffix, "url": url, "download_url": download_url,
                 "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw), "provider": provider,
                 "model": model, "slot_id": slot_id, "caption": caption,
                 "created_at": datetime.now(timezone.utc).isoformat()}
        if source_task_id is not None:
            entry["source_task_id"] = source_task_id
        with self.lock:
            job_fd = None
            asset_fd = None
            created = False
            committed = False
            staging_id = ".pending-" + asset_id
            try:
                job_fd = self._job(job_id, create=True)
                total, count = 0, 0
                for existing_id in os.listdir(job_fd):
                    # A process killed before the directory rename can leave
                    # private staging bytes, never a partially readable asset.
                    if re.fullmatch(r"\.pending-[a-f0-9-]{36}", existing_id):
                        continue
                    _identifier(existing_id)
                    existing_fd = os.open(existing_id, DIRECTORY_FLAGS, dir_fd=job_fd)
                    try:
                        previous = self._manifest(existing_fd, job_id, project_id, existing_id)
                        count += 1
                        total += previous["size"]
                    finally:
                        os.close(existing_fd)
                if count >= MAX_JOB_ASSETS or total + len(raw) > MAX_JOB_BYTES:
                    raise HTTPException(422, "每个任务最多保存 12 份媒体、合计 200 MB")
                os.mkdir(staging_id, 0o700, dir_fd=job_fd)
                created = True
                asset_fd = os.open(staging_id, DIRECTORY_FLAGS, dir_fd=job_fd)
                _atomic_write(asset_fd, entry["name"], raw)
                _atomic_write(asset_fd, "manifest.json", json.dumps(entry, ensure_ascii=False).encode())
                os.rename(staging_id, asset_id, src_dir_fd=job_fd, dst_dir_fd=job_fd)
                committed = True
                os.fsync(job_fd)
            except HTTPException:
                raise
            except (OSError, ValueError, TypeError) as error:
                raise HTTPException(409, "媒体存储校验失败，未覆盖现有成果") from error
            finally:
                if created and asset_fd is not None and not committed:
                    for name in os.listdir(asset_fd):
                        os.unlink(name, dir_fd=asset_fd)
                    os.rmdir(staging_id, dir_fd=job_fd)
                if asset_fd is not None:
                    os.close(asset_fd)
                if job_fd is not None:
                    os.close(job_fd)
        return dict(entry)

    def read(self, job, asset_id):
        job_id, project_id = _identity(job)
        asset_id = _identifier(asset_id)
        job_fd = None
        asset_fd = None
        try:
            job_fd = self._job(job_id)
            asset_fd = os.open(asset_id, DIRECTORY_FLAGS, dir_fd=job_fd)
            entry = self._manifest(asset_fd, job_id, project_id, asset_id)
            raw = _read_file(asset_fd, entry["name"], _kind_limit(entry["kind"]))
            if len(raw) != entry["size"] or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
                raise ValueError("media content changed")
            return dict(entry), raw
        except FileNotFoundError as error:
            raise HTTPException(404, "任务媒体不存在") from error
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise HTTPException(409, "媒体完整性或归属校验失败") from error
        finally:
            if asset_fd is not None:
                os.close(asset_fd)
            if job_fd is not None:
                os.close(job_fd)

    async def import_url(self, job, url, **save_kwargs):
        from .super_platform import public_target

        _identity(job)
        maximum = MAX_VIDEO_BYTES if save_kwargs.get("kind") == "auto" else _kind_limit(save_kwargs.get("kind"))
        try:
            async with asyncio.timeout(90):
                for _ in range(4):
                    if not isinstance(url, str) or len(url) > 16384 or urlsplit(url).scheme != "https":
                        raise HTTPException(422, "媒体下载只接受不含凭据的公开 HTTPS 地址")
                    parsed, hostname, addresses = await public_target(url)
                    if parsed.port not in {None, 443}:
                        raise HTTPException(422, "媒体下载只接受标准 HTTPS 端口")
                    pinned = httpx.URL(url).copy_with(host=str(addresses[0]))
                    host = (f"[{hostname}]" if ":" in hostname else hostname) + (":443" if parsed.port else "")
                    async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10), follow_redirects=False, trust_env=False) as client:
                        async with client.stream("GET", pinned,
                                                 headers={"Host": host, "User-Agent": "ModelDesk/media", "Accept-Encoding": "identity"},
                                                 extensions={"sni_hostname": hostname}) as response:
                            if response.is_redirect:
                                location = response.headers.get("location")
                                if not location:
                                    raise HTTPException(422, "媒体重定向缺少目标")
                                url = urljoin(url, location)
                                continue
                            response.raise_for_status()
                            if response.headers.get("content-encoding", "identity").strip().lower() not in {"", "identity"}:
                                raise HTTPException(422, "媒体下载不接受额外 HTTP 压缩编码")
                            declared = response.headers.get("content-length")
                            if declared is not None and (not declared.isdecimal() or int(declared) > maximum):
                                raise HTTPException(422, "媒体下载超过大小上限")
                            raw = bytearray()
                            async for chunk in response.aiter_bytes(chunk_size=65536):
                                if len(raw) + len(chunk) > maximum:
                                    raise HTTPException(422, "媒体下载超过大小上限")
                                raw.extend(chunk)
                            return await asyncio.to_thread(self.save_bytes, job, bytes(raw), **save_kwargs)
                raise HTTPException(422, "媒体下载重定向次数过多")
        except HTTPException:
            raise
        except (httpx.HTTPError, TimeoutError, ValueError, OSError) as error:
            raise HTTPException(502, "媒体下载失败，未保存为有效成果") from error


store = DeliveryMediaStore()


async def import_url(job, url, **save_kwargs):
    return await store.import_url(job, url, **save_kwargs)


def _scoped_job(job_id, project_id):
    from . import workbench

    _identifier(job_id)
    if project_id is not None:
        _identifier(project_id)
    job = workbench.manager.get(job_id)
    if job.get("project_id") != project_id:
        raise HTTPException(404, "媒体任务不属于此项目")
    return job


@router.get("/jobs/{job_id}/assets/{asset_id}/content")
async def media_content(request: Request, job_id: str, asset_id: str, project_id: str | None = Query(None), download: bool = Query(False)):
    job = _scoped_job(job_id, project_id)
    entry, raw = await asyncio.to_thread(store.read, job, asset_id)
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
               "Content-Security-Policy": "default-src 'none'; sandbox", "ETag": '"' + entry["sha256"] + '"', "Accept-Ranges": "bytes"}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{entry["name"]}"'
    selected = request.headers.get("range")
    if selected and request.headers.get("if-range", headers["ETag"]) == headers["ETag"]:
        match = re.fullmatch(r"bytes=(\d{0,12})-(\d{0,12})", selected)
        start, end = 0, len(raw) - 1
        valid = bool(match and any(match.groups()))
        if valid:
            if match[1]:
                start = int(match[1])
                end = min(int(match[2]), end) if match[2] else end
                valid = start <= end
            else:
                length = int(match[2])
                start = max(0, len(raw) - length)
                valid = length > 0
        if not valid:
            return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{len(raw)}"})
        headers["Content-Range"] = f"bytes {start}-{end}/{len(raw)}"
        return Response(content=raw[start:end + 1], status_code=206, media_type=entry["mime_type"], headers=headers)
    return Response(content=raw, media_type=entry["mime_type"], headers=headers)


def _portable_markdown(content, assets):
    for entry in assets:
        # Longest URL first: /content is a prefix of the download URL.
        for url in (entry["download_url"], entry["url"]):
            content = content.replace(url, "media/" + entry["name"])
    return content


def _article_preview(content, assets):
    from markdown_it import MarkdownIt

    by_path = {"media/" + entry["name"]: entry for entry in assets}
    used = set()

    def media_element(entry):
        path = "media/" + entry["name"]
        caption = html.escape(entry["caption"] or entry["name"], quote=True)
        return (f'<img src="{path}" alt="{caption}" loading="lazy">' if entry["kind"] == "image"
                 else f'<video src="{path}" controls preload="metadata"></video>')

    def render_image(renderer, tokens, index, options, env):
        token = tokens[index]
        entry = by_path.get(token.attrGet("src"))
        if not entry:
            return html.escape(token.content)
        used.add(entry["id"])
        return media_element(entry)

    def render_link_open(renderer, tokens, index, options, env):
        path = tokens[index].attrGet("href")
        entry = by_path.get(path)
        env.setdefault("local_links", []).append(bool(entry))
        # Offline documents only link to verified files in their own bundle.
        return f'<a href="{html.escape(path, quote=True)}" download>' if entry else "<span>"

    def render_link_close(renderer, tokens, index, options, env):
        return "</a>" if env["local_links"].pop() else "</span>"

    # Use the maintained parser for tables/lists/code, with HTML disabled.
    # Links are downloads, not another copy of the image they point to.
    parser = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])
    parser.add_render_rule("image", render_image)
    parser.add_render_rule("link_open", render_link_open)
    parser.add_render_rule("link_close", render_link_close)
    rendered = [parser.render(content)]
    rendered.extend('<figure>' + media_element(entry) + '<figcaption>'
                    + html.escape(entry["caption"] or entry["name"]) + '</figcaption></figure>'
                    for entry in assets if entry["id"] not in used)
    return "".join(rendered)


def _offline_html(title, content, assets, status, limitations):
    article = _article_preview(content, assets)
    notes = "".join("<li>" + html.escape(str(item)) + "</li>" for item in limitations)
    return ("<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\">"
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src \'self\'; media-src \'self\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">'
            "<title>" + html.escape(title) + "</title><style>body{font:17px/1.8 system-ui,sans-serif;color:#20252c;background:#f7f8fa;margin:0}"
            "main{max-width:900px;margin:40px auto;padding:32px;background:white;border-radius:16px}h1{line-height:1.3}"
            "pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}figure{margin:30px 0}img,video{max-width:100%;height:auto;border-radius:12px}"
            "figcaption,.status{color:#667085;font-size:14px}ul,ol{padding-left:24px}"
            "table{display:block;max-width:100%;overflow-x:auto;border-collapse:collapse;font-size:15px}"
            "th,td{border:1px solid #dce1ea;padding:10px;text-align:left}th{background:#f3f5f8}"
            "a{color:#4957c9}blockquote{border-left:3px solid #adb5c7;margin-left:0;padding-left:20px}</style><main><h1>" + html.escape(title) + "</h1>"
            '<p class="status">ModelDesk · ' + html.escape(status) + "</p>" + article
            + ("<ul>" + notes + "</ul>" if notes else "") + "</main></html>").encode()


def delivery_bundle(job):
    job_id, project_id = _identity(job)
    delivery = (job.get("result") or {}).get("delivery") or {}
    if not isinstance(delivery, dict):
        raise HTTPException(409, "交付成果结构无效")
    content = delivery.get("content") or ""
    title = delivery.get("title") or "ModelDesk 成果"
    references = delivery.get("media") or []
    if not isinstance(content, str) or len(content) > 200000 or not isinstance(title, str) or len(title) > 1000:
        raise HTTPException(409, "交付正文无效")
    if not isinstance(references, list) or len(references) > MAX_JOB_ASSETS:
        raise HTTPException(409, "交付媒体清单无效")
    if not content.strip() and not references:
        raise HTTPException(404, "尚无可导出的实际成果")
    assets, binaries, seen, total = [], [], set(), 0
    for reference in references:
        if not isinstance(reference, dict) or reference.get("id") in seen:
            raise HTTPException(409, "交付媒体清单无效或重复")
        entry, raw = store.read(job, reference.get("id"))
        for field in ("id", "job_id", "project_id", "kind", "mime_type", "name", "sha256", "size", "url", "download_url"):
            if reference.get(field) != entry[field]:
                raise HTTPException(409, "交付媒体清单与已验证成果不匹配")
        total += len(raw)
        if total > MAX_JOB_BYTES:
            raise HTTPException(409, "交付媒体总大小超过上限")
        assets.append(entry)
        binaries.append(raw)
        seen.add(entry["id"])
    article = _portable_markdown(content, assets)
    status = str(delivery.get("status") or job.get("status") or "partial")[:100]
    limitations = delivery.get("limitations") or []
    if not isinstance(limitations, list) or len(limitations) > 100:
        raise HTTPException(409, "交付限制说明无效")
    manifest = {"schema_version": 1, "job_id": job_id, "project_id": project_id, "title": title,
                "status": status, "media": assets, "article_sha256": hashlib.sha256(article.encode()).hexdigest(),
                "limitations": [str(item)[:2000] for item in limitations]}
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("index.html", _offline_html(title, article, assets, status, manifest["limitations"]))
        archive.writestr("article.md", article.encode())
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode())
        for entry, raw in zip(assets, binaries):
            archive.writestr("media/" + entry["name"], raw)
    return output.getvalue()


@router.get("/jobs/{job_id}/bundle")
async def bundle_content(job_id: str, project_id: str | None = Query(None)):
    job = _scoped_job(job_id, project_id)
    # Freeze on the event-loop thread before file I/O yields. A task finishing
    # during export must not pair old prose with a newer "complete" status.
    snapshot = {"id": job["id"], "project_id": job.get("project_id"), "status": job.get("status"),
                "result": {"delivery": copy.deepcopy((job.get("result") or {}).get("delivery"))}}
    raw = await asyncio.to_thread(delivery_bundle, snapshot)
    return Response(raw, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="ModelDesk-{job_id}.zip"',
                    "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/capabilities")
async def media_capabilities():
    from .media_providers import capabilities
    return await capabilities()
