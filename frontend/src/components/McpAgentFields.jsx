import { useEffect, useState } from 'react';
import { platformApi } from '../api';

export default function McpAgentFields({ member, index, projectId, onChange }) {
  const [servers, setServers] = useState([]);
  const [discovered, setDiscovered] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    platformApi.list('mcp', false, projectId ? { project_id: projectId } : { scope: 'global' })
      .then((items) => { if (active) setServers((Array.isArray(items) ? items : items.items || []).filter((item) => item.enabled && (!item.project_id || item.project_id === projectId))); })
      .catch((failure) => { if (active) setError(failure.message); });
    return () => { active = false; };
  }, [projectId]);
  const server = servers.find((item) => item.id === member.mcp_server_id);
  const tools = discovered?.id === member.mcp_server_id ? discovered.tools.filter((item) => item.allowed) : [];
  const schema = tools.find((item) => item.name === member.mcp_tool)?.inputSchema;
  const fields = Object.entries(schema?.properties || {}).filter(([, value]) => value?.type === 'string').map(([key]) => key);
  const discover = async () => {
    if (!server || busy) return;
    setBusy(true); setError('');
    try { setDiscovered(await platformApi.discoverMcp(server.id)); }
    catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  };
  return <div className="mission-member-model-field">
    <label><span>MCP Agent 服务</span><select aria-label={`成员 ${index + 1} MCP 服务`} value={member.mcp_server_id || ''} onChange={(event) => { setDiscovered(null); onChange({ mcp_server_id: event.target.value || null, mcp_tool: null, mcp_prompt_field: 'prompt' }); }}>
      <option value="">选择已注册的 MCP 服务</option>
      {member.mcp_server_id && !server && <option value={member.mcp_server_id}>已保存的服务暂不可用</option>}
      {servers.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
    </select></label>
    <label><span>获准执行工具</span><select aria-label={`成员 ${index + 1} MCP 工具`} value={member.mcp_tool || ''} onChange={(event) => onChange({ mcp_tool: event.target.value || null })}>
      <option value="">选择工具</option>{member.mcp_tool && !(server?.allowed_tools || []).includes(member.mcp_tool) && <option value={member.mcp_tool}>{member.mcp_tool} · 尚未允许</option>}
      {(server?.allowed_tools || []).map((name) => <option key={name} value={name}>{name}</option>)}
    </select></label>
    <label><span>接收完整任务的字符串参数</span><input aria-label={`成员 ${index + 1} MCP 任务参数`} value={member.mcp_prompt_field || 'prompt'} maxLength={100} onChange={(event) => onChange({ mcp_prompt_field: event.target.value })} placeholder="例如 prompt、task 或 text" /></label>
    <button type="button" className="subtle-button" disabled={!server || busy} onClick={discover}>{busy ? '正在检查…' : '检查 Agent 工具'}</button>
    {schema && <small>可用字符串参数：{fields.join('、') || '无'}。其他必填参数：{(Array.isArray(schema.required) ? schema.required : []).filter((key) => key !== (member.mcp_prompt_field || 'prompt')).join('、') || '无'}。</small>}
    <small>先在“项目资源 → 工具与 MCP”注册服务并允许工具。自动协作会把本轮要求、上游成果和已选 Skills 交给这个工具，将实际文本结果交给后续成员。单次等待最多 120 秒，未确认的调用不重复发送；外部链接不视为已验证文件。</small>
    {error && <small role="alert">{error}</small>}
  </div>;
}
