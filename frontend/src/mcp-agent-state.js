export const mcpConfigurationKey = (member, projectId) => JSON.stringify([projectId || null, member.mcp_server_id || null, member.mcp_tool || null, member.mcp_prompt_field || 'prompt']);

export async function fetchMcpDiscovery(api, id) {
  try { return { ...await api.discoverMcp(id), id, status: 'ready' }; }
  catch (error) { return { id, status: 'error', tools: [], error: error?.message || '无法读取工具结构，请重试。' }; }
}

export function mcpAllowedTools(discovery, serverId) {
  return discovery?.status === 'ready' && discovery.id === serverId && Array.isArray(discovery.tools)
    ? discovery.tools.filter((tool) => tool.allowed === true && typeof tool.name === 'string') : [];
}

export function mcpStringFields(schema) {
  return schema && typeof schema === 'object' && !Array.isArray(schema) && schema.type === 'object' && schema.properties && typeof schema.properties === 'object'
    ? Object.entries(schema.properties).filter(([, value]) => value?.type === 'string').map(([key]) => key) : [];
}

export function mcpConfigurationIssue(member, server, discovery) {
  if (!member.mcp_server_id) return '请选择已注册的 MCP 服务。';
  if (!server?.enabled) return '所选服务未启用或不属于当前项目，请重新选择。';
  if (discovery?.id !== server.id || discovery.status === 'loading') return '正在检查当前服务的工具结构，请稍候。';
  if (discovery.status !== 'ready') return discovery.error || '工具结构尚未确认，请重新检查。';
  if (!member.mcp_tool) return '请选择当前服务允许执行的工具。';
  const tool = mcpAllowedTools(discovery, server.id).find((item) => item.name === member.mcp_tool);
  if (!tool) return '所选工具已撤销权限或未由当前服务提供，请重新选择。';
  const schema = tool.inputSchema;
  const field = member.mcp_prompt_field || 'prompt';
  if (!mcpStringFields(schema).includes(field) || !/^[A-Za-z][A-Za-z0-9_]*$/.test(field)) return '请选择工具实际支持的字符串任务参数。';
  if (schema.required !== undefined && !Array.isArray(schema.required)) return '工具参数结构无效，请检查服务。';
  const otherRequired = (schema.required || []).filter((name) => name !== field);
  if (otherRequired.length) return `此工具还要求参数 ${otherRequired.join('、')}。当前 Agent 接收一个完整任务字符串，请选择单参数工具，或在服务端提供对应封装。`;
  return '';
}

export function mcpSaveIssues(members, checks, projectId) {
  return (members || []).filter((member) => member.kind === 'mcp').flatMap((member) => {
    const checked = checks[member.id];
    const issue = checked?.key === mcpConfigurationKey(member, projectId) ? checked.issue : '当前 MCP 配置尚未检查，请等待检查完成。';
    return issue ? [`${member.name || 'MCP Agent'}：${issue}`] : [];
  });
}
