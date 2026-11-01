"""Durable result memory and bounded, explicitly attributed working context."""
import hashlib
import json


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def result_memory(job):
    result = job.get("result") or {}
    delivery = result.get("delivery") if job.get("mode") == "group" else None
    record = {"schema": "modeldesk.result-memory.v1", "job_id": job["id"],
              "project_id": job.get("project_id"), "mode": job["mode"],
              "execution_status": job.get("status"), "delivery_status": (delivery or {}).get("status"),
              "limitations": (delivery or {}).get("limitations") or [],
              "sources": (delivery or {}).get("sources") or [],
              "request": job["prompt"]}
    if delivery:
        record.update(title=delivery.get("title"), content=delivery.get("content") or "",
                      acceptance=delivery.get("acceptance"), missing_outputs=delivery.get("missing_outputs"))
    else:
        record["result"] = result
    content = encode(record)
    # Let the existing 2 MB resource validator fail explicitly. Do not persist
    # truncated JSON, silently missing limitations, or a supposedly complete draft.
    provenance = {"job_id": job["id"], "models": job.get("models", []), "source": "explicit_remember",
                  "content_sha256": digest(content)}
    return content, provenance


def memory_source(item, excerpt=None):
    """Only parse our unchanged saved envelope; manually edited memory remains text."""
    content = item.get("content", "")
    meta = {"kind": "memory", "id": item["id"], "name": item["name"], "scope": item["scope"],
            "source_sha256": digest(content), "source_characters": len(content)}
    provenance = item.get("provenance") or {}
    if provenance.get("content_sha256") == meta["source_sha256"]:
        try:
            record = json.loads(content)
        except (ValueError, TypeError):
            record = {}
        if isinstance(record, dict) and record.get("schema") == "modeldesk.result-memory.v1":
            meta["result_state"] = {key: record.get(key) for key in (
                "job_id", "execution_status", "delivery_status", "limitations", "missing_outputs")}
    return {"reference": meta, "text": content if excerpt is None else excerpt}


def pack_context(sources, budget=8000):
    """Pack valid JSON excerpts and report exact coverage, including omitted sources."""
    pieces, references = [], []
    used = 0
    for source in sources:
        raw = source["text"]
        ref = dict(source["reference"])
        ref.setdefault("source_sha256", digest(raw))
        ref.setdefault("source_characters", len(raw))
        ref["available_characters"] = len(raw)
        # Retrieval chunks already have a source extent; source fingerprint and
        # the exact chunk's fingerprint are distinct and must not be conflated.
        ref["excerpt_sha256"] = digest(raw)
        limit = budget - used - (2 if pieces else 0)
        metadata = {key: value for key, value in ref.items() if key not in {"content", "coverage"}}
        def render(count):
            return encode({"source": metadata, "included_characters": count,
                           "excerpt_truncated": count < len(raw), "text": raw[:count]})
        low, high = 0, len(raw)
        while low < high:
            middle = (low + high + 1) // 2
            if len(render(middle)) <= limit:
                low = middle
            else:
                high = middle - 1
        included = len(render(low)) <= limit and (low > 0 or not raw)
        ref.update(included=included, included_characters=low if included else 0,
                   truncated=not included or low < len(raw))
        if included:
            piece = render(low)
            used += len(piece) + (2 if pieces else 0)
            pieces.append(piece)
            if "content" in ref:
                ref["content"] = raw[:low]
        else:
            ref.pop("content", None)
            ref["omission_reason"] = "context_budget"
        references.append(ref)
    return "\n\n".join(pieces), references, any(ref["truncated"] for ref in references)
