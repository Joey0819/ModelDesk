#!/usr/bin/env bash
# 启动 ModelDesk 桌面壳。Electron 在 Wayland 会话中需走 X11 (ozone) 才能正常渲染。
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ELECTRON="$APP_DIR/desktop/node_modules/.bin/electron"
[ -x "$ELECTRON" ] || { echo "缺少 Electron，请先在 desktop/ 执行 npm install" >&2; exit 1; }
export MODEL_DESK_WORKSPACE="$APP_DIR"
exec "$ELECTRON" "$APP_DIR/desktop" --ozone-platform=x11 "$@"
