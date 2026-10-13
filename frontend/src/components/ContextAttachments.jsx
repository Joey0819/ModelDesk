import { useEffect, useState } from 'react';
import { platformApi, workbenchApi } from '../api';
import { Icon } from './WorkbenchResults';
import { EMPTY_CONTEXT } from '../platform-settings';
import { scopedResources, useProjects } from '../project-context';
const rows = (value) => Array.isArray(value) ? value : value.items || [];

export default function ContextAttachments({ value, onChange, jobs, disabled, mode, onOpenProjects }) {
  const [tools, setTools] = useState([]);
  const [knowledge, setKnowledge] = useState([]);
  const [memories, setMemories] = useState([]);
  const { activeProject } = useProjects();
  const [servers, setServers] = useState([]);
  const [snapshots, setSnapshots] = useState([]);
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState('');
  const context = { ...EMPTY_CONTEXT, ...value };
  useEffect(() => {
    let disposed = false;
    Promise.allSettled([platformApi.list('knowledge', false, { scope: 'all', project_id: context.project_id }), platformApi.list('memories', false, { scope: 'all', project_id: context.project_id }), platformApi.list('mcp', false, { scope: 'all', project_id: context.project_id }), platformApi.tools()]).then((results) => {
      if (disposed) return;
      const setters = [setKnowledge, setMemories, setServers, setTools];
      results.forEach((result, index) => { if (result.status === 'fulfilled') setters[index](index === 3 ? rows(result.value) : scopedResources(rows(result.value).filter((item) => !item.archived), context.project_id)); });
    });
    return () => { disposed = true; };
  }, [context.project_id]);
  useEffect(() => {
    if (!context.project_id) return;
    let disposed = false;
    workbenchApi.listSnapshots(context.project_id).then((list) => { if (!disposed) setSnapshots(rows(list)); }).catch(() => { if (!disposed) setSnapshots(jobs.filter((job) => job.project_id === context.project_id && job.snapshot_id).map((job) => ({ id: job.snapshot_id, scope: job.coverage?.scope || 'review', created_at: job.created_at }))); });
    return () => { disposed = true; };
  }, [context.project_id, jobs]);
  const update = (patch) => onChange({ ...context, ...patch });
  const toggle = (key, id) => update({ [key]: context[key].includes(id) ? context[key].filter((item) => item !== id) : [...context[key], id] });
  const showSnapshot = async () => { if (!context.project_id || !context.snapshot_id) return; try { setPreview(await workbenchApi.getSnapshot(context.project_id, context.snapshot_id)); setError(''); } catch (failure) { setError(failure.message); } };
  const attached = context.knowledge_ids.length + context.memory_ids.length + (context.snapshot_id ? 1 : 0);
  return <details className="context-attachments"><summary><Icon name="layers" size={16} /><strong>知识、记忆与项目上下文</strong><span>{attached ? `已选择 ${attached} 项来源` : '尚未附加来源'}</span><button type="button" disabled={disabled} onClick={(event) => { event.preventDefault(); event.stopPropagation(); onChange({ ...EMPTY_CONTEXT, project_id: context.project_id, attachment_ids: context.attachment_ids, include_project_assets: context.include_project_assets, memory_scope: context.memory_scope }); }}>清空选择</button></summary><fieldset disabled={disabled}><div className="context-source-grid"><section><h4>知识库</h4>{knowledge.length ? knowledge.map((item) => <label key={item.id}><input type="checkbox" checked={context.knowledge_ids.includes(item.id)} onChange={() => toggle('knowledge_ids', item.id)} /><span>{item.name}</span><small>{item.tags?.join('、')}</small></label>) : <p>知识库中还没有文档，可在平台资源中添加。</p>}</section><section><h4>持久记忆</h4>{memories.length ? memories.map((item) => <label key={item.id}><input type="checkbox" checked={context.memory_ids.includes(item.id)} onChange={() => toggle('memory_ids', item.id)} /><span>{item.name}</span><small>{item.scope || 'default'}</small></label>) : <p>没有可选择的记忆。</p>}<label className="context-remember"><input type="checkbox" checked={context.remember} onChange={(event) => update({ remember: event.target.checked })} />将本次结果保存为记忆</label><label className="context-scope"><span>记忆范围</span><input value={context.memory_scope} onChange={(event) => update({ memory_scope: event.target.value })} placeholder="例如：本项目名称" /></label></section></div><div className="project-context-selector"><div><label><span>当前共享项目</span><strong>{activeProject?.name || "未选择项目"}</strong><small>项目在页面顶部选择；代码快照可选。</small></label>{context.project_id && <label><span>已预览代码快照</span><select value={context.snapshot_id || ''} onChange={(event) => { update({ snapshot_id: event.target.value || null }); setPreview(null); }}><option value="">选择已有快照</option>{[...new Map(snapshots.map((snapshot) => [snapshot.id, snapshot])).values()].map((snapshot) => <option key={snapshot.id} value={snapshot.id}>{snapshot.scope} · {snapshot.id.slice(0, 8)}{snapshot.created_at && ` · ${new Date(snapshot.created_at).toLocaleDateString('zh-CN')}`}</option>)}</select></label>}</div><div><button type="button" className="subtle-button" onClick={onOpenProjects}>预览当前项目代码</button><button type="button" className="subtle-button" disabled={!context.snapshot_id} onClick={showSnapshot}>查看实际代码来源</button></div></div>{preview && <div className="context-snapshot-preview"><strong>快照 {preview.id.slice(0, 8)}</strong><span>{preview.files?.length || 0} 个纳入文件；{preview.omitted?.length || 0} 个排除项</span><ul>{(preview.files || []).map((file) => <li key={file.path}>{file.path}{file.current && `（当前第 ${file.current.start_line}–${file.current.end_line} 行）`}</li>)}</ul><p>{preview.coverage?.disclaimer}</p></div>}{servers.length > 0 && <section className="context-mcp"><h4>可供角色调用的 MCP 服务</h4>{servers.map((server) => <label key={server.id}><input type="checkbox" checked={context.mcp_server_ids.includes(server.id)} disabled={!server.enabled} onChange={() => toggle('mcp_server_ids', server.id)} />{server.name}<small>{server.enabled ? '已启用' : '未启用'}</small></label>)}</section>}{['chat', 'group'].includes(mode) && <section className="context-chat-tools"><h4>允许模型调用的工具</h4><div>{tools.map((tool) => <label key={tool.id}><input type="checkbox" checked={context.tools.includes(tool.id)} onChange={() => toggle('tools', tool.id)} />{tool.name}</label>)}</div></section>}{error && <p className="output-error">{error}</p>}<p className="context-boundary">仅附加本次明确选择的来源。运行结果保留引用与工具记录，便于核查实际使用的材料。</p></fieldset></details>;
}
