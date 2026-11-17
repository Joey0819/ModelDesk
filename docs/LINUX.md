# ModelDesk Linux 部署指南

本文档介绍在 Linux x64 系统上安装、运行和维护 ModelDesk 的完整流程。

## 系统要求

- **操作系统**: Linux x86_64（Ubuntu 22.04+、Debian 12+、Fedora 38+ 或同等发行版）
- **Node.js**: 18.x 或更高版本
- **Python**: 3.11+（推荐使用 `uv` 管理虚拟环境）
- **可选**: nginx（反向代理）、systemd（服务管理）

## 已知限制

| 功能 | macOS | Linux | 说明 |
|------|-------|-------|------|
| Muse 原生桥接 | ✅ | ❌ | 依赖 macOS Accessibility API，Linux 无等效实现 |
| Tailscale 手机入口 | ✅ | ✅ | 需要单独安装 Tailscale CLI |
| Electron 桌面客户端 | ✅ (arm64) | ✅ (x64) | Linux 打包使用 `npm run package:linux` |
| Backend API | ✅ | ✅ | 完全兼容 |
| 前端 Workbench | ✅ | ✅ | 完全兼容 |

## 快速安装

### 一键安装脚本

```bash
cd /home/noldus/modeldesk
bash scripts/install-linux.sh
```

脚本会幂等地完成以下步骤：

1. 检测 `node`、`npm`、`uv`（或 pip fallback）
2. 同步 Python 依赖（`uv sync` 或 `pip install`）
3. 构建前端（`npm ci && npm run build`）
4. 生成 systemd user unit 并启动服务
5. 配置 nginx 反向代理（若已安装且有 sudo 权限）

### 手动安装

#### 1. Python 环境

```bash
# 使用 uv（推荐）
uv sync

# 或使用 pip
python3 -m venv .venv
.venv/bin/pip install -e .
```

#### 2. 前端

```bash
npm ci --prefix frontend
npm run build --prefix frontend
```

#### 3. 启动后端

```bash
# 开发模式
.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8001 --reload

# 生产模式（使用 systemd）
systemctl --user start modeldesk
```

## 运行方式

### systemd 服务管理

```bash
# 查看状态
systemctl --user status modeldesk

# 查看日志
journalctl --user -u modeldesk -f

# 重启
systemctl --user restart modeldesk

# 停止
systemctl --user stop modeldesk

# 禁用开机自启
systemctl --user disable modeldesk
```

### 直接运行

```bash
.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8001
```

### 访问地址

- **API**: http://127.0.0.1:8001
- **前端**: http://127.0.0.1:5173（开发模式）或通过 nginx 代理
- **健康检查**: http://127.0.0.1:8001/api/readiness

## Electron 桌面客户端

```bash
# 开发模式
npm --prefix desktop start

# 打包 Linux x64
npm --prefix desktop run package:linux
```

打包产物位于 `desktop/dist/package/`。

## 故障排查

### 服务无法启动

```bash
# 查看详细错误
journalctl --user -u modeldesk -n 50 --no-pager

# 检查端口占用
ss -tlnp | grep 8001

# 验证 Python 环境
.venv/bin/python -c "import backend; print('OK')"
```

### Muse 相关警告

Linux 上 Muse 原生桥接不可用是预期行为。readiness 端点会返回：

```json
{
  "id": "muse",
  "state": "unavailable",
  "reason": "Muse 原生桥接仅在 macOS 上可用，Linux 平台不支持此功能。"
}
```

这不影响其他功能的正常使用。

### Tailscale 未检测到

确保 Tailscale CLI 在 PATH 中：

```bash
which tailscale || echo "未找到 tailscale"

# Ubuntu/Debian
sudo apt install tailscale

# 通用
curl -fsSL https://tailscale.com/install.sh | sh
```

### nginx 配置失败

安装脚本在无法获取 sudo 时会打印手动指令。按提示执行即可。

## 配置文件位置

| 文件 | 路径 | 说明 |
|------|------|------|
| systemd unit | `~/.config/systemd/user/modeldesk.service` | 服务定义 |
| nginx conf | `/etc/nginx/conf.d/modeldesk.conf` | 反向代理 |
| 数据目录 | `./data/` | 会话、任务、媒体存储 |
| 日志 | journald | 通过 `journalctl --user` 查看 |

## 更新

重新运行安装脚本即可更新：

```bash
git pull origin feat/linux-support
bash scripts/install-linux.sh
```

脚本会自动备份并替换变更的配置文件。
