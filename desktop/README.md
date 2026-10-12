# ModelDesk 本地桌面窗口

ModelDesk 2.1.0 是 Apple Silicon macOS 应用。它为统一模型工作台提供独立窗口、菜单、单实例行为、项目文件夹选择和启动失败重试入口。页面与模型协作服务来自本机已有的 `llm-council` / `llm-tools` 安装；这是本地安装包，不是自带所有 Python 与模型依赖的可分发产品。

## 使用

安装版本入口为 `/Applications/ModelDesk.app`，构建输出为 `/Users/chengneng/Project/Skills/llm-tools/releases/ModelDesk.app`。2.1.0 实际应用包已重建并安装，版本、ad-hoc 签名与发布/安装 ASAR 一致性检查通过，安装记录位于 `../data/modeldesk-install-2.1.0.json`。本轮浏览器 UI 已实际验收；2026-10-13 解锁后已在安装版实际打开原生窗口、恢复 Council 历史成果并弹出单份 Markdown 原生保存对话框。随后 ScreenCaptureKit -3812 阻止继续自动操作；文件落盘仍待用户点击保存后核验，详见 `../data/ux-native-validation.json`。主进程会调用 `/usr/bin/python3` 执行 `llm-tools/scripts/services.py start`，等待认证网关、工作台 API 和网页就绪后打开 `http://localhost:5173/`。关闭窗口保留这些共享服务，ChatHub 和浏览器仍可使用。统一页首次进入快速开始，宽屏提供左侧导航、中间会话和可调整宽度的成果面板，窄屏用覆盖面板打开成果；草稿和当前会话保存在本机。

如果移动安装目录，启动时设置绝对路径 `MODEL_DESK_WORKSPACE` 指向新的 `llm-tools`。默认路径仍是当前已配置目录。依赖包括 macOS 的 Python 3、已安装的 Node/npm、现有虚拟环境、已配置的本地登录网关与已构建前端。桌面启动会为子进程补齐 `/usr/local/bin` 和 `/opt/homebrew/bin` 到 PATH。

## 构建

从 `llm-council` 根目录执行：

```bash
npm --prefix desktop ci
# 推荐：同时重建生产页面与桌面应用
.venv/bin/python scripts/package_modeldesk.py

# 仅重建桌面壳；使用已经构建的页面
npm --prefix desktop run package
```

打包使用官方 Electron 44.7.0、Electron Packager 20.3.0 与系统 `iconutil`，输出到 `llm-tools/releases/ModelDesk.app`。本地应用采用 ad hoc 签名并验证签名；没有 Apple Developer ID 公证。打包只复制 `main.cjs`、`runtime.cjs`、`preload.cjs`、`status.html` 和最小 package.json；不会包含登录凭据或用户日志。

开发运行：`npm --prefix desktop start`；窗口导航策略检查：`npm --prefix desktop test`。

## 窗口权限

窗口启用 Chromium 沙箱和上下文隔离，关闭 Node 集成。沙箱 preload 仅暴露两个固定操作：`window.modelDesk.chooseProjectDirectory()` 通过原生对话框返回用户选中的文件夹或 null；`window.modelDesk.openOriginalApp("chathub")` 打开已登记的原版 Chrome 扩展。接口不接受任意路径或 URL，也不暴露通用 IPC、文件系统、命令执行或认证接口。主进程只接受当前工作台主框架的这些固定操作请求；启动页、其他窗口和子框架会被拒绝。内部窗口只导航到本机 5173 工作台或固定启动页。外部无用户名密码的 HTTP(S) 链接交给系统浏览器；其他协议拒绝打开。失败页只显示可恢复提示，不展示后台原始日志或认证内容。

历史记录的 Markdown / JSON 导出保留在应用中下载：只允许本机 8001 的 UUID 任务 `/export?format=md|json`、带受限 `artifact_id` 的单份 Markdown，以及固定平台数据备份路由调用 [Electron `downloadURL`](https://www.electronjs.org/docs/latest/api/web-contents#contentsdownloadurlurl-options)，页面不会跳转到浏览器。下载使用原生保存对话框选择位置，不自动设置保存路径；其他 API 路径没有应用内导航权限。当前任务成果可以复制、切换预览或放大；HTML/脚本成果显示源码，不在窗口里执行。完整原版 MultiBot 仅允许固定本机 8501 页面作为子框架，不扩大主窗口导航范围。
