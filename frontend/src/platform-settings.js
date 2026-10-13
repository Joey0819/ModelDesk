export const PLATFORM_SECTIONS = {
  overview: ['快速开始', 'dashboard'], agents: ['智能体与流程', 'tasks'], knowledge: ['知识库', 'library'], memories: ['记忆', 'memory'], prompts: ['提示词库', 'chat'], tools: ['工具与 MCP', 'settings'], migration: ['数据迁移', 'export'], originals: ['原版应用', 'layers'],
};
export const EMPTY_CONTEXT = { knowledge_ids: [], memory_ids: [], memory_scope: 'default', remember: false, project_id: null, snapshot_id: null, mcp_server_ids: [], tools: [], attachment_ids: [], include_project_assets: true };
export function jobContext(value) {
  return { ...EMPTY_CONTEXT, ...value };
}
export function contextErrors(value) {
  const context = jobContext(value);
  const errors = [];
  if (context.snapshot_id && !context.project_id) errors.push('代码快照需要所属项目。');
  if (context.attachment_ids?.length && !context.project_id) errors.push('附件需要所属项目。');
  if (!context.memory_scope?.trim()) errors.push('记忆范围不能为空。');
  return errors;
}
