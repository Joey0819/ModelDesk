import { useEffect, useRef, useState } from 'react';
import { platformApi } from '../api';
import { fetchContextSource } from '../context-source-state';
import { fetchMcpDiscovery, mcpAllowedTools, mcpConfigurationIssue, mcpConfigurationKey, mcpStringFields } from '../mcp-agent-state';

export default function McpAgentFields({ member, index, projectId, onChange, onValidation }) {
  const [servers, setServers] = useState({ status: 'loading', items: [], error: '' });
  const [discovered, setDiscovered] = useState(null);
  const [revision, setRevision] = useState(0);
  const generation = useRef(0);
  useEffect(() => {
    let active = true;
    fetchContextSource(platformApi, 'mcp', projectId).then((value) => { if (active) setServers(value); });
    return () => { active = false; };
  }, [projectId, revision]);
  const server = servers.items.find((item) => item.id === member.mcp_server_id);
  const serverId = server?.enabled ? server.id : null;
  useEffect(() => {
    if (!serverId) return;
    let active = true;
    const token = ++generation.current;
    fetchMcpDiscovery(platformApi, serverId).then((result) => { if (active && generation.current === token) setDiscovered(result); });
    return () => { active = false; };
  }, [serverId, revision]);
  const discovery = discovered?.id === serverId ? discovered : { id: serverId, status: 'loading', tools: [] };
  const tools = mcpAllowedTools(discovery, serverId);
  const schema = tools.find((item) => item.name === member.mcp_tool)?.inputSchema;
  const fields = mcpStringFields(schema);
  const issue = servers.status === 'loading' ? '正在读取 MCP 服务…' : servers.status === 'error' ? `MCP 服务读取失败：${servers.error}` : mcpConfigurationIssue(member, server, discovery);
  const key = mcpConfigurationKey(member, projectId);
  useEffect(() => { onValidation?.(member.id, key, issue); }, [member.id, key, issue, onValidation]);
  const refresh = () => { generation.current++; setServers({ status: 'loading', items: [], error: '' }); setDiscovered(null); setRevision((value) => value + 1); };
  const selectTool = (name) => {
    const options = mcpStringFields(tools.find((tool) => tool.name === name)?.inputSchema);
    const field = options.includes(member.mcp_prompt_field || 'prompt') ? member.mcp_prompt_field || 'prompt' : options[0] || 'prompt';
    onChange({ mcp_tool: name || null, mcp_prompt_field: field });
  };
  return <div className="mission-member-model-field">
    <label><span>MCP Agent 服务</span><select aria-label={`成员 ${index + 1} MCP 服务`} value={member.mcp_server_id || ''} onChange={(event) => { generation.current++; setDiscovered(null); onChange({ mcp_server_id: event.target.value || null, mcp_tool: null, mcp_prompt_field: 'prompt' }); }}>
      <option value="">选择已注册的 MCP 服务</option>
      {member.mcp_server_id && !server?.enabled && <option value={member.mcp_server_id}>已保存的服务暂不可用</option>}
      {servers.items.filter((item) => item.enabled).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
    </select></label>
    <label><span>当前获准执行工具</span><select aria-label={`成员 ${index + 1} MCP 工具`} value={member.mcp_tool || ''} disabled={discovery.status !== 'ready'} onChange={(event) => selectTool(event.target.value)}>
      <option value="">选择工具</option>{member.mcp_tool && !tools.some((tool) => tool.name === member.mcp_tool) && <option value={member.mcp_tool}>{member.mcp_tool} · 尚未确认可用</option>}
      {tools.map((tool) => <option key={tool.name} value={tool.name}>{tool.name}</option>)}
    </select></label>
    <label><span>接收完整任务的字符串参数</span><select aria-label={`成员 ${index + 1} MCP 任务参数`} value={member.mcp_prompt_field || 'prompt'} disabled={!schema} onChange={(event) => onChange({ mcp_prompt_field: event.target.value })}>{!fields.includes(member.mcp_prompt_field || 'prompt') && <option value={member.mcp_prompt_field || 'prompt'}>{member.mcp_prompt_field || 'prompt'} · 需要核对</option>}{fields.map((field) => <option key={field} value={field}>{field}</option>)}</select></label>
    <button type="button" className="subtle-button" onClick={refresh}>重新检查服务与工具</button>
    {issue ? <small role={servers.status === 'loading' || discovery.status === 'loading' ? 'status' : 'alert'}>{issue}</small> : <small role="status">工具与任务参数结构已确认。保存配置不会派发任务；执行前仍会重新核对权限和结构。</small>}
    <small>在“项目资源 → 工具与 MCP”注册服务并允许工具。自动协作会把本轮要求、上游成果和已选 Skills 交给所选工具，实际结果交给后续成员。单次等待最多 120 秒；结果未确认的调用不会重复发送。</small>
  </div>;
}
