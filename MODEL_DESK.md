# ModelDesk 统一工作台

一个页面使用已有 ChatGPT 与 Google / Antigravity 登录，运行多模型聊天、Council、CAMEL 和 CrewAI。网页地址保持 http://localhost:5173/，当前电脑安装入口为 `/Applications/ModelDesk.app`，构建产物位于相邻目录 `../llm-tools/releases/ModelDesk.app`。

## 使用

打开 `ModelDesk.app` 自动启动本机共享服务。也可运行相邻目录 `../llm-tools/start.command`，通过浏览器使用同一页面。

1. 左侧选择多模型聊天、匿名评审、角色对话或任务协作。
2. 选择模型；默认 `gpt-6.1-sol` 与 `gemini-3-flash`。未经实测的网关模型会单独标识。
3. 输入问题并发送。多模型聊天并排显示流式回答；Council 显示独立回答、匿名排名和主席综合；CAMEL 显示两个角色的多轮讨论；CrewAI 显示起草与审查任务。
4. 运行中可停止任务，从历史打开或导出 Markdown / JSON。中断的框架任务可展开运行记录，完整结果是否生成会明确显示。

多模型聊天延续近期上下文；Council、CAMEL 与 CrewAI 每次输入启动一项独立任务。需要承接前文时，将关键背景一起写入任务内容。

关闭桌面窗口保留共享服务，Chrome ChatHub 仍可使用同一登录网关。停止全部服务请运行 `python3 ../llm-tools/scripts/services.py stop`。

## 安装和认证范围

这份 macOS 应用面向当前电脑，复用本机已安装的 Python 环境、模型网关和认证文件；移动到另一台电脑需要安装这些依赖。应用包不包含登录凭据。

浏览器仅接收模型名称、任务和公开的认证状态。OAuth 凭据保留在 `../llm-tools/.private/auth/`；API 不输出 token 或账号邮箱。Claude 订阅 OAuth 未接入本工作台，可继续使用官方客户端。

数据保存至本仓库被忽略的 `data/workbench/`。服务仅绑定 loopback。任务通过固定参数调用已有框架，停止操作仅终止对应任务的自有进程。服务重启会恢复已完成历史，并标记未完成任务中断。

## 开发与验证

```bash
# 现有后端和框架环境已安装；首次安装桌面构建依赖
npm --prefix desktop ci

# 后端生命周期、流式恢复、导出、前端与桌面导航检查，不调用外部模型
.venv/bin/python scripts/verify_modeldesk.py

# 四种模式真实调用、取消、SSE 重放和上下文核查
.venv/bin/python scripts/verify_workbench.py

# 重建生产页面和实际 macOS 应用包
.venv/bin/python scripts/package_modeldesk.py
```

构建使用 Electron，启用 sandbox、context isolation，并限制应用窗口导航到本地工作台。桌面包使用本机 ad hoc 签名；没有申请开发者公证。

## 复用的项目

[LLM Council](https://github.com/karpathy/llm-council) 提供三阶段匿名评审；[CAMEL](https://github.com/camel-ai/camel) 提供角色对话；[CrewAI](https://github.com/crewAIInc/crewAI) 提供任务协作；[CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) 提供本机 OAuth 模型网关。并排聊天接入同一网关，原 ChatHub 扩展继续可用。

界面的统一切换方式参考用户指定的 [CC Switch](https://github.com/farion1231/cc-switch)。
