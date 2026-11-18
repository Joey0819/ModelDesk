# ModelDesk Linux 部署指南

ModelDesk 的工作台、API 和 Electron 窗口支持 Linux x86_64（Beta）。工作台和 API 默认只监听本机回环地址。Muse 原生桥接依赖 macOS，使用模型、远程 Agent 或飞书时需要配置对应连接。

## 系统要求

- Linux x86_64，如 Ubuntu 22.04+ 或 Debian 12+
- Python 3.12+ 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)。uv 可以安装项目需要的 Python 版本。
- Frontend 需要 Node.js 20.19+（20.x）或 22.12+，Electron 打包需要 Node.js 22.12+。均需 npm。
- 一键安装需要运行中的 systemd 用户会话。其他环境可以手动启动。

## 从 GitHub 安装

```bash
git clone https://github.com/Joey0819/ModelDesk.git
cd ModelDesk
bash scripts/install-linux.sh
```

安装脚本按锁文件同步 Python 依赖，安装并构建前端，然后启动两个用户级服务。`modeldesk.service` 运行 API，`modeldesk-ui.service` 提供构建后的工作台页面。确认 API 和页面均能响应后，访问 **http://127.0.0.1:5173/**。

一键安装使用端口 8001 和 5173。如果已有服务占用这两个端口，例如旧安装使用的 nginx，请先选择由哪个服务提供该端口。用户服务配置写入 `${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/`，旧配置有变动时会备份。使用已有静态服务时，可按下面的手动步骤只启动 API。

### 管理用户服务

```bash
systemctl --user status modeldesk.service modeldesk-ui.service
journalctl --user -u modeldesk -u modeldesk-ui -f
systemctl --user restart modeldesk.service
systemctl --user stop modeldesk.service
systemctl --user disable modeldesk.service
```

工作台服务随 API 服务启动、停止和重启。重新运行安装脚本会更新依赖和页面。

## 手动启动

在仓库根目录准备依赖并构建前端。

```bash
uv sync --locked
npm --prefix frontend ci
npm --prefix frontend run build
```

在两个终端中分别启动 API 和工作台。两个命令都在仓库根目录运行。

```bash
# 终端 1
.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8001

# 终端 2
.venv/bin/python -m http.server 5173 --bind 127.0.0.1 --directory frontend/dist
```

- 工作台 http://127.0.0.1:5173/
- API 健康检查 http://127.0.0.1:8001/
- Agent 就绪检查 http://127.0.0.1:8001/api/readiness

前端开发可以将第二个命令换成 `npm --prefix frontend run dev -- --host 127.0.0.1 --port 5173 --strictPort`。手机访问使用工作台内的私有手机入口，需另行配置 Tailscale。

## Electron 桌面窗口

先通过用户服务或手动命令启动工作台和 API，然后选择从源码打开窗口或打包。打包需在 Linux x86_64 上进行。

```bash
npm --prefix desktop ci

# 从源码打开桌面窗口
npm --prefix desktop start

# 构建 Linux x64 桌面包并启动
npm --prefix desktop run package:linux
./desktop/dist/package/ModelDesk-linux-x64/modeldesk
```

桌面包连接上述本机服务。安装依赖、启动服务、配置模型连接与登录在宿主机完成。

## 更新

```bash
git pull --ff-only
bash scripts/install-linux.sh
```

任务数据保存在仓库的 `data/` 目录。需要更新桌面包时重新执行打包命令。
