#!/usr/bin/env bash
# 启动 ModelDesk 桌面壳。Linux 使用 X11 渲染。
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ELECTRON="$APP_DIR/desktop/node_modules/.bin/electron"
[ -x "$ELECTRON" ] || { echo "缺少 Electron，请先在 desktop/ 执行 npm install" >&2; exit 1; }
if [[ "$(uname -s)" == Linux ]]; then
  exec "$ELECTRON" "$APP_DIR/desktop" --ozone-platform=x11 "$@"
fi
exec "$ELECTRON" "$APP_DIR/desktop" "$@"
