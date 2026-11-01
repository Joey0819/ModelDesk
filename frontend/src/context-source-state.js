import { scopedResources } from './project-context.js';

export const CONTEXT_SOURCES = { knowledge: '知识库', memories: '持久记忆', mcp: 'MCP 服务', tools: '模型工具' };
export const resourceRows = (value) => Array.isArray(value) ? value : Array.isArray(value?.items) ? value.items : [];
export const initialSources = () => Object.fromEntries(Object.keys(CONTEXT_SOURCES).map((kind) => [kind, { status: 'loading', items: [], error: '' }]));

export async function fetchContextSource(api, kind, projectId) {
  try {
    const data = kind === 'tools' ? await api.tools() : await api.list(kind, false, projectId ? { scope: 'all', project_id: projectId } : { scope: 'global' });
    const items = resourceRows(data).filter((item) => item && !item.archived);
    return { status: 'ready', items: kind === 'tools' ? items : scopedResources(items, projectId), error: '' };
  } catch (error) {
    return { status: 'error', items: [], error: error?.message || `${CONTEXT_SOURCES[kind]}暂时无法读取` };
  }
}

export function missingSourceSelections(selected = [], state) {
  const known = new Set((state?.items || []).map((item) => item.id));
  return selected.filter((id) => !known.has(id)).map((id) => ({ id,
    reason: state?.status === 'ready' ? '已归档、已移除或不属于当前项目' : '已选择，暂时无法确认来源',
  }));
}
