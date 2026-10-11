# 本地 OAuth 运行

先运行相邻目录 `../llm-tools/start.command`，打开 http://localhost:5173。

启动器通过环境变量传入 OpenAI 兼容网关 `http://127.0.0.1:8317/v1`，以及两个已验证的模型 `gpt-6.1-sol,gemini-3-flash`。主席为 `gpt-6.1-sol`，标题模型为 `gpt-6-luna`。服务仅绑定 loopback。

支持 `LLM_API_BASE`、`LLM_API_KEY`、`COUNCIL_MODELS`（逗号分隔）、`CHAIRMAN_MODEL`、`TITLE_MODEL`；未指定时保留上游 OpenRouter 配置。`VITE_API_BASE` 可在前端构建时指定后端地址。

`LLM_API_KEY=local-oauth` 是本地 SDK 占位值，网关使用已有 ChatGPT 与 Google / Antigravity OAuth 登录。认证文件不放入本仓库。完整启动、停止、重新登录说明见 `../llm-tools/README.md`。

后端可用 `.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8001` 启动；生产前端用 `npm --prefix frontend run build` 重建。
