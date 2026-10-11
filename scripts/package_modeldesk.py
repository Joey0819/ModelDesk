"""Rebuild the production page and the actual local macOS application."""
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    environment = os.environ.copy()
    environment["PATH"] = os.pathsep.join([
        "/usr/local/bin", "/opt/homebrew/bin", "/usr/bin", "/bin",
        environment.get("PATH", ""),
    ])
    npm = shutil.which("npm", path=environment["PATH"])
    if not npm:
        raise SystemExit("Node.js/npm is required to rebuild ModelDesk")
    for folder in ["frontend", "desktop"]:
        subprocess.run([npm, "--prefix", folder, "run", "build" if folder == "frontend" else "package"], cwd=ROOT, env=environment, check=True)
    bundle = ROOT.parent / "llm-tools/releases/ModelDesk.app"
    info = plistlib.loads((bundle / "Contents/Info.plist").read_bytes())
    assert info["CFBundleIdentifier"] == "local.modeldesk.workbench"
    assert (bundle / "Contents/MacOS/ModelDesk").is_file()
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(bundle)], check=True)
    report = ROOT / "data/packaging-validation.json"
    report.parent.mkdir(exist_ok=True, parents=True)
    report.write_text(json.dumps({"bundle": str(bundle), "version": info["CFBundleShortVersionString"], "production_frontend_built": True, "signature_verified": True}, indent=2))
    print("Verified application:", bundle)


if __name__ == "__main__":
    main()
