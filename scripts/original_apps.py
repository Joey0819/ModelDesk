"""Install/control fixed upstream local apps without handling provider credentials."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.request

PROJECT = Path(__file__).resolve().parents[1]
TOOLS = PROJECT.parent / "llm-tools"
VENDOR = TOOLS / "vendor/multibot-chat"
PYTHON = TOOLS / ".venv-multibot/bin/python"
SOURCE = "https://github.com/gptzm/multibot-chat"
APP_URL = "http://127.0.0.1:8501/"
CHATHUB_DIST = PROJECT.parent / "chathub/dist"


def healthy():
    try:
        with urllib.request.urlopen(APP_URL + "_stcore/health", timeout=1.5) as response:
            return response.status == 200
    except Exception:
        return False


def owned_multibot():
    try:
        pid = int((TOOLS / ".private/multibot-original.pid").read_text())
        if pid <= 1:
            return False
        command = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=3).stdout
        return str(PROJECT / "scripts/multibot_entry.py") in command and "streamlit" in command
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def chathub_entry():
    # Read only extension registration metadata, never Chrome cookies or logins.
    base = Path.home() / "Library/Application Support/Google/Chrome"
    for filename in ["Secure Preferences", "Preferences"]:
        for path in sorted(base.glob("*/" + filename)):
            try:
                settings = json.loads(path.read_text()).get("extensions", {}).get("settings", {})
            except (OSError, ValueError):
                continue
            for identifier, entry in settings.items():
                registered = entry.get("path")
                if re.fullmatch(r"[a-p]{32}", identifier) and registered and Path(registered).resolve() == CHATHUB_DIST.resolve():
                    return "chrome-extension://" + identifier + "/app.html"
    return None


def catalog():
    installed = (VENDOR / "app.py").is_file() and PYTHON.is_file()
    revision = None
    if (VENDOR / ".git").exists():
        result = subprocess.run(["git", "-C", str(VENDOR), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=3)
        if result.returncode == 0:
            revision = result.stdout.strip()
    extension = chathub_entry()
    reachable = healthy() if installed else False
    owned = owned_multibot() if reachable else False
    return {"apps": [
        {"id": "multibot", "label": "MultiBot Chat 原版", "installed": installed,
         "running": reachable and owned, "url": APP_URL, "embed": True,
         "source_url": SOURCE, "revision": revision, "license": "GPL-3.0",
         "entry_kind": "iframe", "account_mode": "独立本机账户", "error": "8501 端口被其他服务占用" if reachable and not owned else None if installed else "尚未安装原版独立环境"},
        {"id": "chathub", "label": "ChatHub 原版", "installed": extension is not None,
         "running": None, "url": extension, "embed": False,
         "source_url": "https://github.com/chathub-dev/chathub", "license": "GPL-3.0",
         "entry_kind": "chrome_extension", "account_mode": "现有 Chrome 扩展设置",
         "error": None if extension else "未发现当前 Chrome 已加载的 ChatHub 扩展"},
    ]}


def install():
    (TOOLS / "vendor").mkdir(exist_ok=True)
    if not VENDOR.exists():
        subprocess.run(["git", "clone", "--depth", "1", SOURCE + ".git", str(VENDOR)], check=True)
    if not PYTHON.exists():
        subprocess.run([str(PROJECT / ".venv/bin/python"), "-m", "venv", str(PYTHON.parent.parent)], check=True)
    subprocess.run([str(PYTHON), "-m", "pip", "install", "-r", str(VENDOR / "requirements.txt"), "-r", str(PROJECT / "scripts/original-requirements.txt")], check=True)


def start(identifier):
    if identifier == "chathub":
        entry = chathub_entry()
        if not entry:
            raise RuntimeError("未发现 ChatHub 扩展，请在 Chrome 加载现有扩展")
        subprocess.run(["/usr/bin/open", "-a", "Google Chrome", entry], check=True, timeout=10)
    elif identifier == "multibot":
        if not (VENDOR / "app.py").is_file() or not PYTHON.exists():
            raise RuntimeError("请先安装 MultiBot 原版独立环境")
        if healthy() and not owned_multibot():
            raise RuntimeError("8501 端口已被其他服务占用")
        subprocess.run(["/usr/bin/python3", str(TOOLS / "scripts/services.py"), "start", "--service", "multibot-original"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)
        if not healthy() or not owned_multibot():
            raise RuntimeError("原版服务未通过健康与进程归属检查")
    else:
        raise ValueError("未知原版应用")
    return next(item for item in catalog()["apps"] if item["id"] == identifier)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["status", "install", "start"])
    parser.add_argument("app", nargs="?", choices=["multibot", "chathub"])
    args = parser.parse_args()
    if args.action == "install":
        install()
    elif args.action == "start":
        if not args.app:
            parser.error("start requires app")
        print(json.dumps(start(args.app), ensure_ascii=False))
        return
    print(json.dumps(catalog(), ensure_ascii=False))


if __name__ == "__main__":
    main()
