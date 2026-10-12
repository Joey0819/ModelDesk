export const PLATFORM_SECTIONS = {
  overview: ['快速开始', 'dashboard'], agents: ['智能体与流程', 'tasks'], knowledge: ['知识库', 'library'], memories: ['记忆', 'memory'], prompts: ['提示词库', 'chat'], tools: ['工具与 MCP', 'settings'], migration: ['数据迁移', 'export'], originals: ['原版应用', 'layers'],
};
export const EMPTY_CONTEXT = { knowledge_ids: [], memory_ids: [], memory_scope: 'default', remember: false, project_id: null, snapshot_id: null, mcp_server_ids: [], tools: [] };
export function jobContext(value) {
  return { ...EMPTY_CONTEXT, ...value };
}
export function contextErrors(value) {
  const context = jobContext(value);
  const errors = [];
  if (Boolean(context.project_id) !== Boolean(context.snapshot_id)) errors.push('请同时选择代码项目与已预览快照。');
  if (!context.memory_scope?.trim()) errors.push('记忆范围不能为空。');
  return errors;
}
