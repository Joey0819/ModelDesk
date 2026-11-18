#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

fail() { printf '[ModelDesk] %s\n' "$*" >&2; exit 1; }
[[ "$PROJECT_DIR" != *$'\n'* && "$PROJECT_DIR" != *$'\r'* ]] || fail "项目路径不能包含换行。"

for dependency in node npm uv systemctl; do
  command -v "$dependency" >/dev/null 2>&1 || fail "缺少 $dependency，请先安装（见 docs/LINUX.md）。"
done
node -e 'const [major, minor] = process.versions.node.split(".").map(Number); if (!(major === 20 && minor >= 19 || major === 22 && minor >= 12 || major > 22)) process.exit(1);' \
  || fail "前端需要 Node.js 20.19+（20.x）或 22.12+。"
systemctl --user show-environment >/dev/null || fail "无法连接 systemd 用户会话，请参阅 docs/LINUX.md 的手动启动方式。"

cd "$PROJECT_DIR"
uv sync --locked
npm --prefix frontend ci
npm --prefix frontend run build
test -s frontend/dist/index.html || fail "未找到前端构建产物 frontend/dist/index.html。"

mkdir -p "$UNIT_DIR"

install_unit() {
  local name="$1" destination candidate
  destination="$UNIT_DIR/$name"
  candidate="$(mktemp "$destination.XXXXXX")"
  .venv/bin/python - "scripts/$name.template" "$candidate" "$PROJECT_DIR" <<'PY'
from pathlib import Path
import re
import sys

template, candidate = map(Path, sys.argv[1:3])
project = sys.argv[3].replace("%", "%%")
# systemd parses WorkingDirectory as a path and ExecStart/Environment as words.
quoted = project.replace("\\", "\\\\").replace('"', '\\"')
paths = {"__PROJECT_DIR__": project, "__QUOTED_PROJECT_DIR__": quoted}
candidate.write_text(re.sub(r"__(?:QUOTED_)?PROJECT_DIR__", lambda match: paths[match[0]], template.read_text()))
PY
  if cmp -s "$destination" "$candidate"; then
    rm "$candidate"
  else
    if [[ -f "$destination" ]]; then
      cp -p "$destination" "$destination.bak.$(date +%Y%m%d%H%M%S)"
    fi
    chmod 644 "$candidate"
    mv "$candidate" "$destination"
  fi
}

install_unit modeldesk.service
install_unit modeldesk-ui.service
systemctl --user daemon-reload
systemctl --user enable modeldesk.service
systemctl --user restart modeldesk.service modeldesk-ui.service

if ! .venv/bin/python - <<'PY'
import json
from pathlib import Path
from time import sleep
from urllib.request import ProxyHandler, build_opener

opener = build_opener(ProxyHandler({}))
index = Path("frontend/dist/index.html").read_bytes()
for _ in range(20):
    try:
        with opener.open("http://127.0.0.1:8001/", timeout=1) as api:
            if json.load(api) != {"status": "ok", "service": "ModelDesk API"}:
                raise ValueError("Unexpected API")
        with opener.open("http://127.0.0.1:5173/", timeout=1) as ui:
            if ui.read(len(index) + 1) == index:
                break
    except (OSError, ValueError):
        pass
    sleep(1)
else:
    raise SystemExit(1)
PY
then
  fail "本地 API 或工作台未就绪，请查看 journalctl --user -u modeldesk -u modeldesk-ui（使用 8001 和 5173 端口）。"
fi

printf '\n[ModelDesk] 安装完成\n  工作台 http://127.0.0.1:5173/\n  API http://127.0.0.1:8001/\n'
