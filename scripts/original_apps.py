"""Install/control fixed upstream local apps without handling provider credentials."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
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


def chrome_profile_bases():
    """Chrome user-data roots for this platform, most specific first."""
    home = Path.home()
    if sys.platform == "darwin":
        return [home / "Library/Application Support/Google/Chrome"]
    if sys.platform.startswith("win"):
        local = os.environ.get("LOCALAPPDATA")
        return [Path(local) / "Google/Chrome/User Data"] if local else []
    return [home / ".config/google-chrome", home / ".config/chromium"]


def chathub_entry():
    # Read only extension registration metadata, never Chrome cookies or logins.
    entries = []
    for base in chrome_profile_bases():
        if not base.is_dir():
            continue
        for filename in ["Secure Preferences", "Preferences"]:
            entries.extend(_chathub_from(sorted(base.glob("*/" + filename))))
    return entries[0] if entries else None


def _chathub_from(paths):
    for path in paths:
        try:
            settings = json.loads(path.read_text()).get("extensions", {}).get("settings", {})
        except (OSError, ValueError):
            continue
        for identifier, entry in settings.items():
            registered = entry.get("path")
            if re.fullmatch(r"[a-p]{32}", identifier) and registered and Path(registered).resolve() == CHATHUB_DIST.resolve():
                return ["chrome-extension://" + identifier + "/app.html"]
    return []


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


def _python_executable():
    """Resolve the best available Python 3 interpreter."""
    return sys.executable or shutil.which("python3") or "/usr/bin/python3"


def _open_extension(entry):
    """Open a chrome-extension:// URL in the platform's default handler."""
    if sys.platform == "darwin":
        command = ["/usr/bin/open", "-a", "Google Chrome", entry]
    elif sys.platform.startswith("win"):
        command = ["cmd", "/c", "start", "", entry]
    else:
        command = [shutil.which("xdg-open") or "xdg-open", entry]
    subprocess.run(command, check=True, timeout=10)


def start(identifier):
    if identifier == "chathub":
        entry = chathub_entry()
        if not entry:
            raise RuntimeError("未发现 ChatHub 扩展，请在 Chrome 加载现有扩展")
        _open_extension(entry)
    elif identifier == "multibot":
        if not (VENDOR / "app.py").is_file() or not PYTHON.exists():
            raise RuntimeError("请先安装 MultiBot 原版独立环境")
        if healthy() and not owned_multibot():
            raise RuntimeError("8501 端口已被其他服务占用")
        services_script = TOOLS / "scripts/services.py"
        if not services_script.is_file():
            raise RuntimeError(f"未找到上游服务控制器: {services_script}")
        subprocess.run([_python_executable(), str(services_script), "start", "--service", "multibot-original"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)
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
