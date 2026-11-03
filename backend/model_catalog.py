"""Catalog presentation metadata, separate from inference routing and evidence."""
import re

from .member_capabilities import image_model


def model_capability(identifier):
    if image_model(identifier):
        return "image"
    name = identifier.split(":")[-1].split("/")[-1].lower()
    if re.search(r"embedding|rerank|whisper|(?:^|-)tts(?:-|$)|transcri|realtime|sora|veo-", name):
        return "other"
    return "text"


def subscription_entry(item):
    identifier = item if isinstance(item, str) else item["id"]
    owner = item.get("owned_by", "") if isinstance(item, dict) else ""
    # The gateway may serve Claude and GPT-OSS through Google. Names are not
    # reliable evidence of the connection that actually serves a model.
    provider = {"openai": "codex", "antigravity": "antigravity"}.get(owner, "other")
    if not owner:
        provider = "codex" if identifier.startswith(("gpt-", "o3", "o4")) else "antigravity" if identifier.startswith("gemini-") else "other"
    return {"id": identifier, "provider": provider, "source": "gateway",
            "provider_label": {"codex": "ChatGPT 网关", "antigravity": "Google / Antigravity 网关"}.get(provider, "订阅网关"),
            "capability": model_capability(identifier)}
