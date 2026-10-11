"""Real four-engine review acceptance over an explicitly synthetic local fixture.

No user's repository is transmitted. The fixture has one intentional empty-list
division bug; generated .env/binary data verifies exclusion without real secrets.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
API = "http://127.0.0.1:8001/api/workbench"


def request(path, body=None):
    encoded = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    call = urllib.request.Request(API + path, data=encoded, headers={"Content-Type": "application/json"} if encoded else {}, method="POST" if encoded else "GET")
    try:
        with urllib.request.urlopen(call, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: " + exc.read().decode()[:1000]) from exc


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engines", default="chat,council,camel,crewai")
    parser.add_argument("--workers", type=int, default=2, choices=[1, 2])
    args = parser.parse_args()
    fixture = ROOT / "data/project-fixture"
    fixture.mkdir(parents=True, exist_ok=True, mode=0o700)
    (fixture / "average.py").write_text('''"""Intentional acceptance fixture; not production code."""


def average(values):
    """Return an average; the empty-list guard is intentionally absent."""
    return sum(values) / len(values)
''')
    (fixture / ".env").write_text("DEMO_FAKE_CREDENTIAL=acceptance-only-not-a-real-secret\n")
    (fixture / "payload.bin").write_bytes(b"demo\x00binary")
    for path in fixture.iterdir():
        path.chmod(0o600)
    projects = request("/projects")
    project = next((item for item in projects if item["path"] == str(fixture.resolve())), None)
    if project is None:
        project = request("/projects", {"name": "代码评审验收示例（含故意缺陷）", "path": str(fixture)})
    snapshot = request(f"/projects/{project['id']}/snapshots", {"scope": "selected", "files": ["average.py", ".env", "payload.bin"]})
    assert [item["path"] for item in snapshot["files"]] == ["average.py"], snapshot["coverage"]
    assert {item["path"] for item in snapshot["omitted"]} == {".env", "payload.bin"}
    assert "acceptance-only-not-a-real-secret" not in json.dumps(snapshot)
    print("Fixture project and immutable snapshot ready", flush=True)

    def verify(engine):
        started = time.monotonic()
        job = request(f"/projects/{project['id']}/reviews", {"snapshot_id": snapshot["id"], "engine": engine, "models": ["gpt-6.1-sol", "gemini-3-flash"], "chairman": "gpt-6.1-sol", "rounds": 1, "prompt": "审查 average.py 的真实缺陷，重点核查空列表输入的行为。按 findings JSON schema 输出准确行号、对应原文代码证据和建议，不声称执行过代码。"})
        print(f"{engine} started: {job['id']}", flush=True)
        deadline = time.monotonic() + 900
        while job["status"] in {"queued", "running"} and time.monotonic() < deadline:
            time.sleep(0.5)
            job = request(f"/jobs/{job['id']}")
        findings = (job.get("result") or {}).get("findings", [])
        valid = [finding for finding in findings if finding["file"] == "average.py" and finding["side"] == "current" and finding["line_start"] <= 6 <= finding["line_end"] and finding["evidence"] in (fixture / "average.py").read_text()]
        record = {"engine": engine, "job_id": job["id"], "status": job["status"], "seconds": round(time.monotonic() - started, 2), "validated_fixture_findings": len(valid), "result": job.get("result"), "error": job.get("error")}
        print(f"{engine}: {record['status']}, valid fixture findings={len(valid)}", flush=True)
        return record

    engines = [engine.strip() for engine in args.engines.split(",") if engine.strip()]
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        results = list(executor.map(verify, engines))
    summary = {"fixture_only": True, "project_id": project["id"], "fixture_path": str(fixture), "snapshot_id": snapshot["id"], "coverage": snapshot["coverage"], "exclusion_verified": True, "models": ["gpt-6.1-sol", "gemini-3-flash"], "engines": results, "passed": all(item["status"] == "complete" and item["validated_fixture_findings"] > 0 for item in results)}
    destinations = [ROOT / "data/project-validation.json", ROOT.parent / "llm-tools/reports/project-review-validation.json"]
    for destination in destinations:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        destination.chmod(0o600)
    print("Project review acceptance:", "PASS" if summary["passed"] else "FAIL", flush=True)
    if not summary["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
