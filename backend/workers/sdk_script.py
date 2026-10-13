"""Run an explicitly reviewed native SDK script in an owned local process."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.vision import GATEWAY, prepare_vision, user_content, vision_transports, crew_vision_llm, public_summary, SafeWorkerStream

os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")


def sdk_namespace(data):
    """Give reviewed scripts explicit image content and observable SDK factories."""
    parts = prepare_vision(data)
    report = data["_vision_report"]
    report["content_helpers"] = 0

    def manifest_items(value):
        return value if isinstance(value, list) else (value or {}).get("items", [])

    current = manifest_items(data.get("attachment_manifest"))
    historical = manifest_items(data.get("history_attachment_manifest"))
    selected_attachments = []
    seen = set()
    for item in [*current, *historical]:
        identity = (item.get("id"), item.get("source_job_id"))
        if identity not in seen:
            selected_attachments.append(item); seen.add(identity)

    def content(text=None):
        report["content_helpers"] += 1
        return user_content(data["prompt"] if text is None else text, parts)

    def selected(name):
        name = name or data["models"][0]
        if name not in data["models"]:
            raise ValueError("原生附件示范只能使用本次已选择的模型")
        return name

    def camel_model(name=None, max_tokens=1024):
        from camel.models import ModelFactory
        from camel.types import ModelPlatformType
        from backend.workers.workflow import camel_clients
        name = selected(name)
        client, async_client = camel_clients(name, image_parts=parts, vision_report=report)
        return ModelFactory.create(model_platform=ModelPlatformType.OPENAI_COMPATIBLE_MODEL,
                                   model_type=name, url=GATEWAY, api_key="local-oauth",
                                   model_config_dict={"max_tokens": max_tokens}, client=client,
                                   async_client=async_client, max_retries=2, timeout=180)

    def crew_llm(name=None, max_tokens=1024):
        return crew_vision_llm(selected(name), parts, report, max_tokens=max_tokens)

    def vision_client():
        import httpx
        from openai import OpenAI
        sync_transport, _ = vision_transports(parts, report)
        return OpenAI(base_url=GATEWAY, api_key="local-oauth", max_retries=2, timeout=180,
                      http_client=httpx.Client(transport=sync_transport, trust_env=False))

    namespace = {"PROMPT": data["prompt"], "MODELS": data["models"], "GATEWAY": GATEWAY,
                 "CONTEXT": data.get("context", ""), "ATTACHMENTS": selected_attachments,
                 "IMAGE_PARTS": parts, "ATTACHMENT_CONTENT": content, "CAMEL_MODEL": camel_model,
                 "CREWAI_LLM": crew_llm, "VISION_CLIENT": vision_client,
                 "RESULT": None, "__name__": "__modeldesk_sdk__"}
    return namespace, report


def main():
    sys.stdout = SafeWorkerStream(sys.stdout)
    sys.stderr = SafeWorkerStream(sys.stderr)
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = json.loads(args.input.read_text())
    if not data.get("approved") or data.get("sdk") not in {"camel", "crewai"}:
        raise RuntimeError("原生脚本尚未明确确认")
    namespace, vision = sdk_namespace(data)
    print(f"原生 {data['sdk']} 脚本开始：使用本机 Python 权限", flush=True)
    exec(compile(data["source"], "<ModelDesk reviewed SDK script>", "exec"), namespace)
    result = namespace["RESULT"]
    if result is None:
        raise RuntimeError("脚本需要将可 JSON 序列化的输出赋给 RESULT")
    result = result if isinstance(result, dict) else {"answer": result}
    vision["usage"] = ("model_requests_observed" if vision["image_requests"] else
                       "content_prepared_model_use_unverified" if vision["content_helpers"] and vision["images_available"] else
                       "images_selected_model_use_unverified" if vision["images_available"] else "no_images_selected")
    result["vision"] = vision
    result.update(sdk=data["sdk"], native_script=True, human_approved=True)
    args.output.write_text(json.dumps(public_summary(result), ensure_ascii=False, indent=2, allow_nan=False))
    args.output.chmod(0o600)
    print("原生 SDK 脚本完成，结果已保存。", flush=True)


if __name__ == "__main__":
    main()
