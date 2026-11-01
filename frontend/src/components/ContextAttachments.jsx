import { useCallback, useEffect, useRef, useState } from 'react';
import { platformApi, workbenchApi } from '../api';
import { Icon } from './WorkbenchResults';
import { EMPTY_CONTEXT } from '../platform-settings';
import { useProjects } from '../project-context';
import { CONTEXT_SOURCES, fetchContextSource, initialSources, resourceRows } from '../context-source-state';
import { ContextSourceRows, MissingSelections, SourceStatus } from './ContextSourceRows';
import './ContextSourceRows.css';

export default function ContextAttachments(props) {
  // A new project receives fresh source/snapshot state immediately. Pending
  // requests from the previous project cannot populate this instance.
  return <ScopedContextAttachments key={props.value?.project_id || 'global'} {...props} />;
}

function ScopedContextAttachments({ value, onChange, jobs = [], disabled, mode, onOpenProjects }) {
  const { activeProject } = useProjects();
  const context = { ...EMPTY_CONTEXT, ...value };
  const [sources, setSources] = useState(initialSources);
  const [snapshotState, setSnapshotState] = useState({ status: context.project_id ? 'loading' : 'ready', items: [], error: '' });
  const [snapshotRevision, setSnapshotRevision] = useState(0);
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState('');
  const alive = useRef(true);
  const requests = useRef({});
  const previewGeneration = useRef(0);
  const loadSource = useCallback(async (kind) => {
    const token = (requests.current[kind] || 0) + 1;
    requests.current[kind] = token;
    const result = await fetchContextSource(platformApi, kind, context.project_id);
    if (alive.current && requests.current[kind] === token) setSources((previous) => ({ ...previous, [kind]: result }));
  }, [context.project_id]);
  useEffect(() => {
    alive.current = true;
    Object.keys(CONTEXT_SOURCES).forEach(loadSource);
    return () => { alive.current = false; };
  }, [loadSource]);
  const retrySource = (kind) => { setSources((previous) => ({ ...previous, [kind]: { status: 'loading', items: [], error: '' } })); loadSource(kind); };
  useEffect(() => {
    if (!context.project_id) return;
    let active = true;
    workbenchApi.listSnapshots(context.project_id)
      .then((list) => { if (active) setSnapshotState({ status: 'ready', items: resourceRows(list), error: '' }); })
      .catch((failure) => { if (active) setSnapshotState({ status: 'error', items: [], error: failure.message }); });
    return () => { active = false; };
  }, [context.project_id, jobs, snapshotRevision]);
  const loadSnapshots = () => { setSnapshotState({ status: 'loading', items: [], error: '' }); setSnapshotRevision((previous) => previous + 1); };
  const update = (patch) => onChange({ ...context, ...patch });
  const toggle = (key, id) => update({ [key]: context[key].includes(id) ? context[key].filter((item) => item !== id) : [...context[key], id] });
  const showSnapshot = async () => { if (!context.project_id || !context.snapshot_id) return; const token = ++previewGeneration.current; try { const result = await workbenchApi.getSnapshot(context.project_id, context.snapshot_id); if (alive.current && previewGeneration.current === token) { setPreview(result); setError(''); } } catch (failure) { if (alive.current && previewGeneration.current === token) setError(failure.message); } };
  const attached = context.knowledge_ids.length + context.memory_ids.length + (context.snapshot_id ? 1 : 0);
  return <details className="context-attachments">
    <summary><Icon name="layers" size={16} /><strong>知识、记忆与项目上下文</strong><span>{attached ? `已选择 ${attached} 项来源` : '尚未附加来源'}</span><button type="button" disabled={disabled} onClick={(event) => { event.preventDefault(); event.stopPropagation(); onChange({ ...EMPTY_CONTEXT, project_id: context.project_id, attachment_ids: context.attachment_ids, include_project_assets: context.include_project_assets, memory_scope: context.memory_scope }); }}>清空选择</button></summary>
    <fieldset disabled={disabled}>
      <div className="context-source-grid">
        <section><h4>知识库</h4><ContextSourceRows kind="knowledge" state={sources.knowledge} selected={context.knowledge_ids} toggle={(id) => toggle('knowledge_ids', id)} retry={() => retrySource('knowledge')} /></section>
        <section><h4>持久记忆</h4><ContextSourceRows kind="memories" state={sources.memories} selected={context.memory_ids} toggle={(id) => toggle('memory_ids', id)} retry={() => retrySource('memories')} /><label className="context-remember"><input type="checkbox" checked={context.remember} onChange={(event) => update({ remember: event.target.checked })} />将本次结果保存为记忆</label><label className="context-scope"><span>记忆范围</span><input value={context.memory_scope} onChange={(event) => update({ memory_scope: event.target.value })} placeholder="例如：本项目名称" /></label></section>
      </div>
      <div className="project-context-selector">
        <div><label><span>当前共享项目</span><strong>{activeProject?.name || '未选择项目'}</strong><small>项目在页面顶部选择；代码快照可选。</small></label>{context.project_id && <label><span>已预览代码快照</span><select value={context.snapshot_id || ''} onChange={(event) => { previewGeneration.current++; update({ snapshot_id: event.target.value || null }); setPreview(null); }}><option value="">选择已有快照</option>{context.snapshot_id && !snapshotState.items.some((snapshot) => snapshot.id === context.snapshot_id) && <option value={context.snapshot_id}>已选快照暂未确认 · {context.snapshot_id.slice(0, 8)}</option>}{snapshotState.items.map((snapshot) => <option key={snapshot.id} value={snapshot.id}>{snapshot.scope} · {snapshot.id.slice(0, 8)}{snapshot.created_at && ` · ${new Date(snapshot.created_at).toLocaleDateString('zh-CN')}`}</option>)}</select></label>}</div>
        {snapshotState.status === 'error' && <div className="context-source-error" role="alert"><span>代码快照读取失败：{snapshotState.error}</span><button type="button" className="subtle-button" onClick={loadSnapshots}>重试代码快照</button></div>}
        <div><button type="button" className="subtle-button" onClick={onOpenProjects}>预览当前项目代码</button><button type="button" className="subtle-button" disabled={!context.snapshot_id} onClick={showSnapshot}>查看实际代码来源</button></div>
      </div>
      {preview && preview.id === context.snapshot_id && <div className="context-snapshot-preview"><strong>快照 {preview.id.slice(0, 8)}</strong><span>{preview.files?.length || 0} 个纳入文件；{preview.omitted?.length || 0} 个排除项</span><ul>{(preview.files || []).map((file) => <li key={file.path}>{file.path}{file.current && `（当前第 ${file.current.start_line}–${file.current.end_line} 行）`}</li>)}</ul><p>{preview.coverage?.disclaimer}</p></div>}
      <section className="context-mcp"><h4>可供角色调用的 MCP 服务</h4><SourceStatus kind="mcp" state={sources.mcp} retry={() => retrySource('mcp')} />{sources.mcp.items.map((server) => <label key={server.id}><input type="checkbox" checked={context.mcp_server_ids.includes(server.id)} disabled={!server.enabled && !context.mcp_server_ids.includes(server.id)} onChange={() => toggle('mcp_server_ids', server.id)} />{server.name}<small>{server.enabled ? '已启用' : '已停用，请移除本轮选择'}</small></label>)}{sources.mcp.status === 'ready' && !sources.mcp.items.length && <p>没有已注册的 MCP 服务。可在“项目资源 → 工具与 MCP”添加。</p>}<MissingSelections ids={context.mcp_server_ids} state={sources.mcp} remove={(id) => toggle('mcp_server_ids', id)} /></section>
      {['chat', 'group'].includes(mode) && <section className="context-chat-tools"><h4>允许模型调用的工具</h4><SourceStatus kind="tools" state={sources.tools} retry={() => retrySource('tools')} /><div>{sources.tools.items.map((tool) => <label key={tool.id}><input type="checkbox" checked={context.tools.includes(tool.id)} onChange={() => toggle('tools', tool.id)} />{tool.name}</label>)}</div><MissingSelections ids={context.tools} state={sources.tools} remove={(id) => toggle('tools', id)} /></section>}
      {error && <p className="output-error">{error}</p>}
      <p className="context-boundary">仅附加本次明确选择的来源。读取失败不会清空已选来源；可重试或移除。运行结果保留引用与工具记录，便于核查实际使用的材料。</p>
    </fieldset>
  </details>;
}
