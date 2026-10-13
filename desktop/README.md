# ModelDesk 本地桌面窗口

ModelDesk 2.3.0 是 Apple Silicon macOS 应用。它为统一模型工作台提供独立窗口、菜单、单实例行为、项目/文件/图片选择、文件夹导入和启动失败重试入口。页面与模型协作服务来自本机已有的 `llm-council` / `llm-tools` 安装；这是本地安装包，不是自带所有 Python 与模型依赖的可分发产品。

## 使用

安装版本入口为 `/Applications/ModelDesk.app`，构建输出为 `/Users/chengneng/Project/Skills/llm-tools/releases/ModelDesk.app`。2.1.0 实际应用包已重建并安装，版本、ad-hoc 签名与发布/安装 ASAR 一致性检查通过，安装记录位于 `../data/modeldesk-install-2.1.0.json`。本轮浏览器 UI 已实际验收；2026-10-13 解锁后已在安装版实际打开主窗口、恢复 Council 历史成果并弹出单份 Markdown 原生保存对话框。随后 ScreenCaptureKit -3812 阻止继续自动操作，最后的保存点击由用户手动完成。单成果 Markdown 已在 Downloads 落盘，430 bytes，SHA256 与预期一致，正文逐字节等于本次实际 `council:synthesis` 导出，含“杉桥7392”和三条清单，详见 `../data/ux-native-validation.json`。本轮未验收整任务 JSON 的原生落盘或取消保存；不把用户手动保存表述为全流程自动保存。主进程会调用 `/usr/bin/python3` 执行 `llm-tools/scripts/services.py start`，等待认证网关、工作台 API 和网页就绪后打开 `http://localhost:5173/`。关闭窗口保留这些共享服务，ChatHub 和浏览器仍可使用。统一页首次进入快速开始，宽屏提供左侧导航、中间会话和可调整宽度的成果面板，窄屏用覆盖面板打开成果；草稿和当前会话保存在本机。

如果移动安装目录，启动时设置绝对路径 `MODEL_DESK_WORKSPACE` 指向新的 `llm-tools`。默认路径仍是当前已配置目录。依赖包括 macOS 的 Python 3、已安装的 Node/npm、现有虚拟环境、已配置的本地登录网关与已构建前端。桌面启动会为子进程补齐 `/usr/local/bin` 和 `/opt/homebrew/bin` 到 PATH。

## 2.2.0 安装与原生验收

当前安装版已更新为 2.2.0，用户退出旧版后安全替换应用包，保留旧版备份；版本、签名和 ASAR 一致性检查通过。实际安装版完成名称创建普通项目、原生图片选择器、原生文件夹选择器、两份文件勾选导入并设为共用、切换聊天后双模型正确读取资料。单份 GPT 成果通过原生保存窗口落盘，310 bytes，逐字节匹配所选模型的导出正文。记录见 `../data/project-input-acceptance/native-validation.json` 与 `../data/modeldesk-install-2.2.0.json`。本次 AX 原生操作可用，不能推断以前的 ScreenCaptureKit 故障永久解决；原生截图目前为 OS 缩略图，完整可读布局截图另见浏览器验收。

## 构建

从 `llm-council` 根目录执行：

```bash
npm --prefix desktop ci
# 推荐：同时重建生产页面与桌面应用
.venv/bin/python scripts/package_modeldesk.py

# 仅重建桌面壳；使用已经构建的页面
npm --prefix desktop run package
```

打包使用官方 Electron 44.7.0、Electron Packager 20.3.0 与系统 `iconutil`，输出到 `llm-tools/releases/ModelDesk.app`。本地应用采用 ad hoc 签名并验证签名；没有 Apple Developer ID 公证。打包只复制 `main.cjs`、`runtime.cjs`、`input-files.cjs`、`preload.cjs`、`status.html` 和最小 package.json；不会包含登录凭据或用户日志。

开发运行：`npm --prefix desktop start`；窗口导航策略检查：`npm --prefix desktop test`。

## 窗口权限

窗口启用 Chromium 沙箱和上下文隔离，关闭 Node 集成。沙箱 preload 暴露固定操作：`window.modelDesk.chooseProjectDirectory()` 返回用户选中的文件夹或 null，`chooseInputFiles()` 选择图片和文件，`chooseAttachmentDirectory()` 选择用于导入的文件夹，`openOriginalApp("chathub")` 打开已登记的原版扩展。文件选择结果受数量、字节和文件类型限制，文件夹排除凭据、缓存、符号链接与越界路径；没有任意路径读取、通用 IPC、命令或认证接口。主进程只接受当前工作台主框架的固定请求；启动页、其他窗口和子框架会被拒绝。内部窗口只导航到本机 5173 工作台或固定启动页。外部无用户名密码的 HTTP(S) 链接交给系统浏览器；其他协议拒绝打开。失败页只显示可恢复提示，不展示后台原始日志或认证内容。

历史记录的 Markdown / JSON 导出保留在应用中下载：只允许本机 8001 的 UUID 任务 `/export?format=md|json`、带受限 `artifact_id` 的单份 Markdown，以及固定平台数据备份路由调用 [Electron `downloadURL`](https://www.electronjs.org/docs/latest/api/web-contents#contentsdownloadurlurl-options)，页面不会跳转到浏览器。下载使用原生保存对话框选择位置，不自动设置保存路径；其他 API 路径没有应用内导航权限。当前任务成果可以复制、切换预览或放大；HTML/脚本成果显示源码，不在窗口里执行。完整原版 MultiBot 仅允许固定本机 8501 页面作为子框架，不扩大主窗口导航范围。

2.2.0 还允许受限项目或任务 UUID 的附件内容下载，图片预览与其他任意 API 导航仍遵守各自规则。项目的当前资料与任务冻结副本使用独立地址，旧任务内容不会因为资料后续变化而被替换。

## 2.3.0 协作任务

新版本提供跨框架步骤、审阅后继续、明确重试、项目任务历史和成果查看。`/api/harness/runs/{UUID}/export?format=md|json` 使用原生保存窗口；仅接受固定 loopback 地址、有效 UUID 和单个合法格式参数。未知路径、额外参数、远程地址和路径规范化绕过均不进入原生下载通道。
