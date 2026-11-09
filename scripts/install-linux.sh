#!/usr/bin/env bash
# ModelDesk Linux 安装脚本 — 幂等，可重复执行
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_NAME="modeldesk"
SYSTEMD_UNIT="$HOME/.config/systemd/user/${SERVICE_NAME}.service"
NGINX_CONF="/etc/nginx/conf.d/modeldesk.conf"
PYTHON="${PROJECT_DIR}/.venv/bin/python"
UVICORN="${PROJECT_DIR}/.venv/bin/uvicorn"

log() { printf '\033[1;32m[ModelDesk]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[ModelDesk]\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m[ModelDesk]\033[0m %s\n' "$*" >&2; }

# ── 1. 依赖检测 ────────────────────────────────────────────────────────
command -v node >/dev/null 2>&1 || { err "未找到 node，请先安装 Node.js 18+"; exit 1; }
command -v npm >/dev/null 2>&1 || { err "未找到 npm"; exit 1; }

if ! command -v uv >/dev/null 2>&1; then
  warn "未找到 uv，尝试使用 pip 作为 fallback"
  USE_UV=false
else
  USE_UV=true
fi

# ── 2. Python 虚拟环境 ─────────────────────────────────────────────────
PYPROJECT="${PROJECT_DIR}/pyproject.toml"
if [ "$USE_UV" = true ]; then
  if [ ! -d "${PROJECT_DIR}/.venv" ] || [ "${PYPROJECT}" -nt "${PROJECT_DIR}/.venv" ]; then
    log "同步 Python 依赖（uv sync）…"
    cd "$PROJECT_DIR" && uv sync
  else
    log "Python 虚拟环境已是最新，跳过。"
  fi
else
  if [ ! -d "${PROJECT_DIR}/.venv" ]; then
    log "创建 Python 虚拟环境（pip fallback）…"
    python3 -m venv "${PROJECT_DIR}/.venv"
    "${PROJECT_DIR}/.venv/bin/pip" install -e "${PROJECT_DIR}"
  else
    log "Python 虚拟环境已存在，跳过。"
  fi
fi

# ── 3. Frontend 构建 ───────────────────────────────────────────────────
FRONTEND_DIR="${PROJECT_DIR}/frontend"
if [ -d "$FRONTEND_DIR" ]; then
  log "安装并构建前端…"
  npm ci --prefix "$FRONTEND_DIR"
  npm run build --prefix "$FRONTEND_DIR"
else
  warn "未找到 frontend 目录，跳过前端构建。"
fi

# ── 4. systemd user unit ──────────────────────────────────────────────
UNIT_DIR="$(dirname "$SYSTEMD_UNIT")"
mkdir -p "$UNIT_DIR"

generate_unit() {
  cat > "${SYSTEMD_UNIT}.tmp" <<EOF
[Unit]
Description=ModelDesk Backend API
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${PROJECT_DIR}
ExecStart=${UVICORN} backend.main:app --host 127.0.0.1 --port 8001
Restart=on-failure
RestartSec=5
Environment=PATH=${PROJECT_DIR}/.venv/bin:/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=default.target
EOF
}

generate_unit

if [ -f "$SYSTEMD_UNIT" ]; then
  if diff -q "$SYSTEMD_UNIT" "${SYSTEMD_UNIT}.tmp" >/dev/null 2>&1; then
    log "systemd unit 无变化，跳过。"
    rm -f "${SYSTEMD_UNIT}.tmp"
  else
    warn "systemd unit 已变更，备份旧文件并替换。"
    cp "$SYSTEMD_UNIT" "${SYSTEMD_UNIT}.bak.$(date +%Y%m%d%H%M%S)"
    mv "${SYSTEMD_UNIT}.tmp" "$SYSTEMD_UNIT"
  fi
else
  mv "${SYSTEMD_UNIT}.tmp" "$SYSTEMD_UNIT"
  log "已生成 systemd user unit: $SYSTEMD_UNIT"
fi

systemctl --user daemon-reload
log "重新加载 systemd 配置…"

# 启动或重启服务
if systemctl --user is-active "$SERVICE_NAME" >/dev/null 2>&1; then
  log "重启 ModelDesk 服务…"
  systemctl --user restart "$SERVICE_NAME"
else
  log "启用并启动 ModelDesk 服务…"
  systemctl --user enable "$SERVICE_NAME"
  systemctl --user start "$SERVICE_NAME"
fi

# ── 5. nginx 反向代理（可选）──────────────────────────────────────────
generate_nginx() {
  cat <<'NGINX'
# ModelDesk nginx reverse proxy configuration
server {
    listen 80;
    server_name modeldesk.local;

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
NGINX
}

if command -v nginx >/dev/null 2>&1; then
  NGINX_TMP=$(mktemp)
  generate_nginx > "$NGINX_TMP"

  if [ -f "$NGINX_CONF" ]; then
    if diff -q "$NGINX_CONF" "$NGINX_TMP" >/dev/null 2>&1; then
      log "nginx 配置无变化，跳过。"
      rm -f "$NGINX_TMP"
    else
      if sudo -n true 2>/dev/null; then
        warn "nginx 配置已变更，备份旧文件并替换。"
        sudo cp "$NGINX_CONF" "${NGINX_CONF}.bak.$(date +%Y%m%d%H%M%S)"
        sudo cp "$NGINX_TMP" "$NGINX_CONF"
        sudo nginx -t && sudo systemctl reload nginx
        log "nginx 配置已更新并重载。"
      else
        warn "无法自动写入 nginx 配置（需要 sudo），请手动执行："
        echo "  sudo cp '$NGINX_TMP' '$NGINX_CONF'"
        echo "  sudo nginx -t && sudo systemctl reload nginx"
      fi
      rm -f "$NGINX_TMP"
    fi
  else
    if sudo -n true 2>/dev/null; then
      sudo cp "$NGINX_TMP" "$NGINX_CONF"
      sudo nginx -t && sudo systemctl reload nginx
      log "已生成 nginx 配置: $NGINX_CONF"
    else
      warn "无法自动写入 nginx 配置（需要 sudo），请手动执行："
      echo "  sudo cp '$NGINX_TMP' '$NGINX_CONF'"
      echo "  sudo nginx -t && sudo systemctl reload nginx"
    fi
    rm -f "$NGINX_TMP"
  fi
else
  warn "未检测到 nginx，跳过反向代理配置。如需外部访问，请手动安装 nginx 并配置。"
fi

# ── 6. 完成 ──────────────────────────────────────────────────────────
log "安装完成！"
echo ""
echo "  服务状态:  systemctl --user status $SERVICE_NAME"
echo "  查看日志:  journalctl --user -u $SERVICE_NAME -f"
echo "  停止服务:  systemctl --user stop $SERVICE_NAME"
echo "  禁用服务:  systemctl --user disable $SERVICE_NAME"
echo "  API 地址:  http://127.0.0.1:8001"
echo ""
