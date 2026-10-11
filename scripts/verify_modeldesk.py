"""Run the focused backend, UI-state, lint, and desktop checks before packaging."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    environment = os.environ.copy()
    environment["PATH"] = os.pathsep.join([
        "/usr/local/bin", "/opt/homebrew/bin", "/usr/bin", "/bin",
        environment.get("PATH", ""),
    ])
    npm = shutil.which("npm", path=environment["PATH"])
    if not npm:
        raise SystemExit("Node.js/npm is required to verify ModelDesk")
    commands = [
        [sys.executable, "-m", "unittest", "discover", "-s", "backend", "-p", "test_*.py", "-v"],
        [npm, "--prefix", "frontend", "test"],
        [npm, "--prefix", "frontend", "run", "lint"],
        [npm, "--prefix", "desktop", "test"],
    ]
    for command in commands:
        subprocess.run(command, cwd=ROOT, env=environment, check=True)


if __name__ == "__main__":
    main()
