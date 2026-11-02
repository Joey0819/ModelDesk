"""Preflight local, verified delivery bytes before any Feishu result is sent."""
import hashlib

from fastapi import HTTPException

from .feishu_sdk import FeishuAPIError

# ModelDesk's conservative channel limit; files above it stay in ModelDesk.
MAX_FILE_BYTES = 20_000_000


def verified_artifacts(job, delivery):
    """Return a fully checked batch; never fetch model-authored URLs or paths."""
    from .document_delivery import store as documents
    from .delivery_media import store as media
    outputs, seen = [], set()
    digest = hashlib.sha256(delivery.get("content", "").encode()).hexdigest()
    try:
        for category, entries, storage in [("document", delivery.get("documents", []), documents),
                                           ("media", delivery.get("media", []), media)]:
            for entry in entries:
                identifier = category + ":" + entry["id"]
                if identifier in seen:
                    continue
                manifest, raw = storage.read(job, entry["id"])
                if (manifest["sha256"] != entry["sha256"] or manifest["size"] != entry["size"]
                        or hashlib.sha256(raw).hexdigest() != manifest["sha256"]):
                    raise FeishuAPIError("成果文件发生变化，已停止飞书回传；请在 ModelDesk 检查原始成果")
                if category == "document":
                    if manifest["content_sha256"] != digest or manifest["artifact_id"] != "group:delivery":
                        raise FeishuAPIError("交付文档不属于当前正文版本，已停止飞书回传")
                else:
                    if manifest["kind"] != entry["kind"]:
                        raise FeishuAPIError("成果媒体类型不匹配，已停止飞书回传")
                    manifest = {**manifest, "filename": manifest["name"],
                                "format": "mp4" if manifest["mime_type"] == "video/mp4" else "stream"}
                if not 0 < len(raw) <= MAX_FILE_BYTES:
                    raise FeishuAPIError("有成果文件超过 ModelDesk 飞书通道的单文件 20 MB 上限；本轮文件尚未回传，完整成果仍保存在 ModelDesk")
                seen.add(identifier)
                outputs.append((identifier, manifest, raw))
    except (HTTPException, KeyError, TypeError, OSError) as error:
        raise FeishuAPIError("成果文件无法通过本机归属与完整性校验，已停止飞书回传；请在 ModelDesk 检查") from error
    return outputs
