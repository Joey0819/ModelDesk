"""Run an explicitly reviewed native SDK script in an owned local process."""
import argparse
import json
import os
from pathlib import Path

os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = json.loads(args.input.read_text())
    if not data.get("approved") or data.get("sdk") not in {"camel", "crewai"}:
        raise RuntimeError("原生脚本尚未明确确认")
    namespace = {"PROMPT": data["prompt"], "MODELS": data["models"], "GATEWAY": "http://127.0.0.1:8317/v1",
                 "CONTEXT": data.get("context", ""), "RESULT": None, "__name__": "__modeldesk_sdk__"}
    print(f"原生 {data['sdk']} 脚本开始：使用本机 Python 权限", flush=True)
    exec(compile(data["source"], "<ModelDesk reviewed SDK script>", "exec"), namespace)
    result = namespace["RESULT"]
    if result is None:
        raise RuntimeError("脚本需要将可 JSON 序列化的输出赋给 RESULT")
    result = result if isinstance(result, dict) else {"answer": result}
    result.update(sdk=data["sdk"], native_script=True, human_approved=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    args.output.chmod(0o600)
    print("原生 SDK 脚本完成，结果已保存。", flush=True)


if __name__ == "__main__":
    main()
