import { useCallback, useEffect, useRef, useState } from 'react';
import { platformApi } from '../api';
import { Icon } from './WorkbenchResults';
import PlatformJobInspector from './PlatformJobInspector';

const list = (value) => Array.isArray(value) ? value : value.items || [];
const statusLabels = { queued: '等待开始', running: '运行中', complete: '已完成', completed: '已完成', error: '失败', failed: '失败', cancelled: '已停止', interrupted: '已中断' };

const STARTER_PROMPTS = [
  { name: '起草并复核', mode: 'crewai', icon: 'tasks', description: '团队先起草，再检查事实、逻辑与表达。', content: '请根据我接下来提供的材料，起草一份中文说明，然后独立复核事实依据、逻辑和表达。请区分已提供的事实、推断与尚待核实的信息，并给出可以直接使用的修订稿。\n\n材料：' },
  { name: '比较两种方案', mode: 'council', icon: 'council', description: '让多个模型比较取舍，再综合建议。', content: '请比较以下两种方案，从实现成本、使用体验、可靠性和限制四个方面分析，说明建议适用的条件。对缺少的事实明确标注，不要编造依据。\n\n方案 A：\n方案 B：' },
  { name: '梳理一个想法', mode: 'group', icon: 'group', description: '让不同模型接着讨论，逐步完善思路。', content: '请一起讨论下面这个想法。先明确目标，再依次提出可行步骤、反例与需要补充的信息，最后整理一份下一步行动清单。\n\n我的想法：' },
];

export function DashboardWorkspace({ jobs = [], models = [], onSection, onOpenProjects, onStartCollaboration, onOpenJob, onUsePrompt, onUseAgent, onOpenSdk }) {
  const [overview, setOverview] = useState(null);
  const [error, setError] = useState('');
  const [selectedJob, setSelectedJob] = useState(null);
  const [savedPrompts, setSavedPrompts] = useState([]);
  useEffect(() => { let disposed = false; const refresh = () => platformApi.overview().then((value) => { if (!disposed) { setOverview(value); setError(''); } }).catch((failure) => { if (!disposed) setError(failure.message); }); refresh(); const timer = setInterval(refresh, 15000); return () => { disposed = true; clearInterval(timer); }; }, []);
  useEffect(() => { let disposed = false; platformApi.list('prompts').then((value) => { if (!disposed) setSavedPrompts(list(value).filter((item) => !item.archived && item.content?.trim()).slice(0, 3)); }).catch(() => {}); return () => { disposed = true; }; }, []);
  const running = jobs.filter((job) => ['queued', 'running'].includes(job.status));
  const recent = [...jobs].sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || ''))).slice(0, 5);
  const openJob = (job) => onOpenJob ? onOpenJob(job) : setSelectedJob(job.id);
  const start = () => onStartCollaboration ? onStartCollaboration() : onUsePrompt?.({ mode: 'crewai', content: '' });
  const modeLabels = { chat: '多模型聊天', council: '匿名互评', camel: '角色讨论', crewai: '团队协作', group: '模型群聊', review: '代码评审', camel_workforce: '多智能体协作', crewai_flow: '协作流程', sdk_script: 'SDK 脚本' };
  return <div className="platform-dashboard quick-start-workspace">
    <section className="quick-start-introduction"><span className="quick-start-eyebrow">从一件具体的事开始</span><h2>今天想完成什么？</h2><p>写下目标，让模型一起完成；也可以选择本地项目，审查实际代码。</p></section>
    <div className="quick-start-actions" aria-label="常用任务入口">
      <button className="quick-start-action primary-start-action" onClick={start}><span className="quick-action-icon"><Icon name="tasks" size={23} /></span><span><strong>开始协作</strong><small>输入目标，团队起草并复核</small></span><span className="quick-action-arrow" aria-hidden="true">→</span></button>
      <button className="quick-start-action" onClick={onOpenProjects}><span className="quick-action-icon"><Icon name="folder" size={23} /></span><span><strong>添加项目用于代码审查</strong><small>选文件夹，预览代码，再开始评审</small></span><span className="quick-action-arrow" aria-hidden="true">→</span></button>
      <button className="quick-start-action" disabled={!recent.length} onClick={() => recent[0] && openJob(recent[0])}><span className="quick-action-icon"><Icon name="layers" size={23} /></span><span><strong>打开最近成果</strong><small>{recent.length ? '继续查看结果、依据与导出' : '首次运行后，成果会保留在这里'}</small></span><span className="quick-action-arrow" aria-hidden="true">→</span></button>
    </div>
    <section className="quick-start-templates"><header><div><h3>用一个模板开始</h3><p>点选后填入任务输入框，补充材料再发送。</p></div><button className="subtle-button" onClick={() => onSection('prompts')}>我的提示词</button></header><div className="starter-template-list">{STARTER_PROMPTS.map((template) => <button key={template.name} onClick={() => onUsePrompt?.(template)}><Icon name={template.icon} size={19} /><span><strong>{template.name}</strong><small>{template.description}</small></span><span aria-hidden="true">→</span></button>)}</div>{savedPrompts.length > 0 && <div className="quick-saved-prompts"><span>最近保存</span>{savedPrompts.map((item) => <button key={item.id} title={item.name} onClick={() => onUsePrompt?.(item)}>{item.name}<Icon name="plus" size={13} /></button>)}</div>}{onUseAgent && models.length > 0 && <button className="starter-agent-link" onClick={() => onUseAgent({ name: '写作助手', role: '中文写作助手', goal: '根据用户提供的材料起草清晰的中文内容，标注事实、推断与待核实信息。', backstory: '优先使用用户提供的材料，保留事实依据和内容限制。', model: models[0], tools: [] })}><Icon name="dialogue" size={16} />准备一个写作助手<span>使用当前模型，不添加工具</span></button>}</section>
    <section className="quick-start-recent"><header><h3>最近成果</h3><span>{running.length > 0 ? `${running.length} 个任务正在运行` : '运行记录自动保存在本机'}</span></header>{recent.length ? <div className="quick-recent-list">{recent.map((job) => <button key={job.id} onClick={() => openJob(job)} title={job.prompt || '运行任务'}><Icon name={job.mode === 'review' ? 'code' : 'layers'} size={18} /><span className="quick-recent-title"><strong>{job.prompt || '运行任务'}</strong><small>{modeLabels[job.mode] || '协作任务'} · {job.created_at && new Date(job.created_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}</small></span><span className={`job-monitor-status status-${job.status}`}>{statusLabels[job.status] || job.status}</span><span aria-hidden="true">→</span></button>)}</div> : <p className="quick-start-empty">还没有运行记录。先选择一个模板，完成后可在这里重新打开成果。</p>}</section>
    <details className="quick-start-advanced"><summary><Icon name="settings" size={17} /><span>高级设置</span><small>资源、工具、原生框架与数据迁移</small><Icon name="chevron" size={16} /></summary><div className="quick-advanced-links">{[['knowledge', '知识库', '准备文档与检索来源'], ['memories', '记忆', '管理长期内容与使用范围'], ['agents', '智能体与流程', '编辑角色、工具与分工'], ['tools', '工具与 MCP', '连接服务并检查实际返回'], ['migration', '数据备份与迁移', '导出、校验并导入数据'], ['originals', '完整原版应用', '打开 ChatHub 与 MultiBot Chat']].map(([id, name, description]) => <button key={id} onClick={() => onSection(id)}><strong>{name}</strong><small>{description}</small></button>)}<button onClick={onOpenSdk}><strong>原生 SDK 工作室</strong><small>编辑并确认运行自定义脚本</small></button></div>{error && <div className="error-banner" role="alert">{error}</div>}<div className="quick-resource-summary">{[['knowledge', '知识文档'], ['memories', '记忆'], ['agents', '智能体'], ['flows', '流程'], ['prompts', '提示词']].map(([kind, label]) => <span key={kind}>{label} {overview?.counts?.[kind] ?? '—'}</span>)}</div><section className="runtime-capabilities"><header><h3>运行环境</h3><span>安装状态与实际验证分别显示</span></header><div className="runtime-list">{(overview?.runtimes || []).map((runtime) => <article key={runtime.id}><span className={`status-dot ${runtime.installed ? 'online' : 'offline'}`} /><div><strong>{runtime.name || runtime.label || runtime.id}</strong><small>{runtime.version || runtime.reason || ''}</small></div><span>{runtime.installed ? '已安装' : '未安装'}</span><em>{runtime.validated ? '已验证' : '尚未验证'}</em></article>)}</div>{overview?.features?.length > 0 && <details className="feature-catalog"><summary>完整功能与依赖清单</summary>{overview.features.map((feature) => <div key={feature.id}><strong>{feature.name || feature.label || feature.id}</strong><span>{feature.available ? feature.validated ? '可用，已验证' : '可用，待验证' : feature.reason || '依赖尚未就绪'}</span></div>)}</details>}</section><details className="quick-all-runs"><summary>全部运行记录（{jobs.length}）</summary><div className="job-monitor-table">{jobs.map((job) => <button key={job.id} title={job.prompt || '运行任务'} onClick={() => openJob(job)}><span className={`job-monitor-status status-${job.status}`}>{statusLabels[job.status] || job.status}</span><strong>{job.prompt || '运行任务'}</strong><small>{modeLabels[job.mode] || job.mode}</small><time>{job.created_at && new Date(job.created_at).toLocaleDateString('zh-CN')}</time></button>)}</div></details></details>
    {selectedJob && <PlatformJobInspector key={selectedJob} jobId={selectedJob} onClose={() => setSelectedJob(null)} />}
  </div>;
}

export function MigrationWorkspace() {
  const [source, setSource] = useState('');
  const [fileName, setFileName] = useState('');
  const [conflict, setConflict] = useState('skip');
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  let parsed = null; try { parsed = source ? JSON.parse(source) : null; } catch { /* Show actionable validation message below. */ }
  const selectFile = async (file) => { if (!file) return; try { setSource(await file.text()); setFileName(file.name); setResult(null); setError(''); } catch { setError('文件无法读取，请重新选择。'); } };
  const importData = async () => { if (!parsed) return; setBusy(true); setError(''); try { setResult(await platformApi.importData({ data: parsed, conflict })); } catch (failure) { setError(failure.message); } finally { setBusy(false); } };
  const exportData = () => { const anchor = document.createElement('a'); anchor.href = platformApi.exportUrl(); anchor.download = 'ModelDesk-data.json'; document.body.appendChild(anchor); anchor.click(); anchor.remove(); };
  return <div className="migration-workspace"><section><header><h2>导出平台数据</h2><p>包含资源与运行记录，便于备份、迁移和复核。</p></header><button className="primary-button" onClick={exportData}><Icon name="export" size={17} />导出 JSON 备份</button><p className="platform-boundary">提供资源与运行数据迁移。供应商认证凭据不进入导出文件。</p></section><section><header><h2>导入已有备份</h2><p>先读取与校验，确认后才加入本地数据。</p></header><label className="migration-file-picker">选择 ModelDesk JSON 备份<input type="file" accept=".json,application/json" onChange={(event) => { selectFile(event.target.files[0]); event.target.value = ''; }} /></label>{fileName && <p className="selected-data-file">已选择：{fileName}</p>}<label className="platform-field"><span>导入数据预览（可编辑）</span><textarea rows={8} value={source} onChange={(event) => { setSource(event.target.value); setResult(null); }} spellCheck={false} /></label>{source && !parsed && <div className="output-error">文件内容不是有效 JSON。请检查备份文件或修正格式后再导入。</div>}{parsed && <p className="import-schema-preview">已解析 JSON；平台将继续校验数据版本、资源字段和任务状态。</p>}<label className="platform-field"><span>同名或重复记录处理</span><select value={conflict} onChange={(event) => setConflict(event.target.value)}><option value="skip">跳过重复记录</option><option value="rename">重命名并新增</option></select></label><button className="primary-button" disabled={!parsed || busy} onClick={importData}>{busy ? '正在校验与导入' : '校验并导入'}</button>{error && <div className="error-banner" role="alert">{error}</div>}{result && <div className="migration-report"><strong>实际导入结果</strong><pre>{JSON.stringify(result, null, 2)}</pre></div>}</section></div>;
}

function catalogItems(value, field) {
  const items = value?.[field] ?? value?.items ?? value;
  if (!Array.isArray(items)) throw new Error('服务返回的清单格式无效。');
  return items;
}

const loadTools = async () => catalogItems(await platformApi.tools(), 'tools');
const loadActiveServers = async () => catalogItems(await platformApi.list('mcp'));
const loadArchivedServers = async () => catalogItems(await platformApi.list('mcp', true));
const loadOriginals = async () => catalogItems(await platformApi.originals(), 'apps');

function useCatalog(load) {
  const [items, setItems] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const alive = useRef(false);
  const pending = useRef(null);
  const refresh = useCallback(function refreshCatalog(afterCurrent = false) {
    if (pending.current) return afterCurrent ? pending.current.then(() => refreshCatalog()) : pending.current;
    if (!alive.current) return Promise.resolve();
    setLoading(true);
    setError('');
    pending.current = Promise.resolve().then(load).then((next) => {
      if (alive.current) setItems(next);
    }).catch((failure) => {
      if (alive.current) setError(failure.message);
    }).finally(() => {
      pending.current = null;
      if (alive.current) setLoading(false);
    });
    return pending.current;
  }, [load]);
  useEffect(() => {
    alive.current = true;
    refresh();
    return () => { alive.current = false; };
  }, [refresh]);
  return { items, setItems, loading, error, refresh };
}

function CatalogFeedback({ catalog, label, empty }) {
  if (catalog.error) return <div className="error-banner platform-catalog-error" role="alert">
    <span>无法读取{label}。{catalog.error}{catalog.items?.length > 0 && ' 已保留上次读取结果。'}</span>
    <button className="subtle-button" type="button" onClick={() => catalog.refresh()} aria-label={`重试读取${label}`}>重试</button>
  </div>;
  if (catalog.loading) return <p className="resource-empty" role="status">正在读取{label}…</p>;
  if (!catalog.items?.length) return <p className="resource-empty" role="status">{empty}</p>;
  return null;
}

function usePlatformAction() {
  const [action, setAction] = useState('');
  const [error, setError] = useState('');
  const pending = useRef(false);
  const run = async (name, perform) => {
    if (pending.current) return;
    pending.current = true;
    setAction(name);
    setError('');
    try { await perform(); }
    catch (failure) { setError(failure.message); }
    finally { pending.current = false; setAction(''); }
  };
  return { action, error, setError, run };
}

export function OriginalsWorkspace() {
  const catalog = useCatalog(loadOriginals);
  const [selected, setSelected] = useState(null);
  const operation = usePlatformAction();
  const open = (app) => operation.run(app.id, async () => {
    if (app.id === 'chathub' && window.modelDesk?.openOriginalApp) {
      const opened = await window.modelDesk.openOriginalApp('chathub');
      if (!opened) throw new Error('Chrome 未能打开原版 ChatHub，请检查扩展是否已加载。');
      return;
    }
    const started = await platformApi.startOriginal(app.id);
    catalog.setItems((previous) => previous.map((item) => item.id === started.id ? started : item));
    if (started.embed) setSelected(started);
  });
  return <div className="originals-workspace">
    <CatalogFeedback catalog={catalog} label="原版应用" empty="没有可显示的原版应用。" />
    <div className="original-app-list">{catalog.items?.map((app) => <article key={app.id}>
      <header><span className="model-mark">{app.id === 'multibot' ? 'M' : 'C'}</span><div><h2>{app.label}</h2><p>{app.account_mode}</p></div></header>
      <div className="original-app-meta"><span>{app.installed ? '已安装源码' : '未安装'}</span><span>{app.running ? '服务已运行' : '尚未启动'}</span>{app.license && <span>{app.license}</span>}</div>
      {app.revision && <small>源码版本 {app.revision.slice(0, 10)}</small>}
      <div><button className="primary-button" onClick={() => open(app)} disabled={Boolean(operation.action)}>{operation.action === app.id ? '正在打开' : app.embed ? '打开完整原版' : '在 Chrome 打开原版'}</button>{app.source_url && <a href={app.source_url} target="_blank" rel="noreferrer">查看上游源码</a>}</div>
      {app.error && <p className="output-error">{app.error}</p>}
    </article>)}</div>
    {operation.error && <div className="error-banner" role="alert">{operation.error}</div>}
    {selected && <section className="original-app-frame"><header><strong>{selected.label}</strong><a href={selected.url} target="_blank" rel="noreferrer">独立窗口打开</a><a href={`${selected.url}?login=1`} target="_blank" rel="noreferrer">原版账户管理</a><button className="icon-button" aria-label="关闭原版应用" onClick={() => setSelected(null)}><Icon name="close" size={16} /></button></header><iframe src={selected.url} title={selected.label} /></section>}
  </div>;
}

function defaultArguments(tool) {
  const properties = (tool.parameters || tool.inputSchema || {}).properties || {};
  const defaults = { number: 0, integer: 0, boolean: false, array: [], object: {} };
  return JSON.stringify(Object.fromEntries(Object.entries(properties).map(([key, schema]) => [
    key, schema.default ?? schema.enum?.[0] ?? schema.minimum ?? defaults[schema.type] ?? '',
  ])), null, 2);
}

function ToolInvokePanel({ tool, operation, call, onGrant }) {
  const [argumentsText, setArgumentsText] = useState(() => defaultArguments(tool));
  const [result, setResult] = useState(null);
  const invoke = () => operation.run('invoke', async () => {
    let args;
    try { args = JSON.parse(argumentsText); } catch { throw new Error('参数必须是有效 JSON。'); }
    if (args === null || typeof args !== 'object' || Array.isArray(args)) throw new Error('调用参数必须是 JSON 对象。');
    setResult(null);
    setResult(await call(args));
  });
  return <section className="tool-invoke-panel">
    <header><strong>{tool.name || tool.id}</strong><span>{tool.description}</span></header>
    <details><summary>实际参数定义</summary><pre>{JSON.stringify(tool.parameters || tool.inputSchema || {}, null, 2)}</pre></details>
    <label className="platform-field"><span>调用参数（JSON）</span><textarea rows={6} value={argumentsText} onChange={(event) => setArgumentsText(event.target.value)} spellCheck={false} /></label>
    {onGrant && tool.allowed === false && <p className="context-invalid">此工具尚未加入该服务的允许列表。<button className="subtle-button" onClick={onGrant} disabled={Boolean(operation.action)}>允许调用此工具</button></p>}
    <button className="primary-button" onClick={invoke} disabled={Boolean(operation.action) || tool.allowed === false}>{operation.action === 'invoke' ? '正在调用' : '调用工具'}</button>
    {result !== null && <div className="tool-actual-result"><strong>实际返回结果</strong><pre>{JSON.stringify(result, null, 2)}</pre></div>}
  </section>;
}

const toolKey = (tool) => tool.id || tool.name;

function BuiltInTools({ context }) {
  const catalog = useCatalog(loadTools);
  const [selectedTool, setSelectedTool] = useState(null);
  const operation = usePlatformAction();
  const call = (args) => platformApi.invokeTool(toolKey(selectedTool), {
    arguments: args, knowledge_ids: context.knowledge_ids, memory_scope: context.memory_scope,
    project_id: context.project_id, snapshot_id: context.snapshot_id, mcp_server_ids: context.mcp_server_ids,
  });
  return <>
    <CatalogFeedback catalog={catalog} label="内置工具" empty="没有可用的内置工具。" />
    {operation.error && <div className="error-banner" role="alert">{operation.error}</div>}
    <div className="platform-tool-catalog">{catalog.items?.map((tool) => <button className={selectedTool && toolKey(selectedTool) === toolKey(tool) ? 'selected' : ''} key={toolKey(tool)} disabled={Boolean(operation.action)} onClick={() => { setSelectedTool(tool); operation.setError(''); }}><strong>{tool.label || tool.name}</strong><p>{tool.description}</p></button>)}</div>
    {selectedTool && <ToolInvokePanel key={toolKey(selectedTool)} tool={selectedTool} operation={operation} call={call} />}
  </>;
}

const blankServer = { name: '', url: '', transport: 'streamable_http', enabled: true, allowed_tools: [] };
const splitTools = (value) => value.split(/[,，]/).map((item) => item.trim()).filter(Boolean);

function McpServerEditor({ initialServer, onSaved, onArchive }) {
  const [saved, setSaved] = useState(initialServer);
  const [draft, setDraft] = useState(initialServer || blankServer);
  const [allowedText, setAllowedText] = useState(() => (initialServer?.allowed_tools || []).join(', '));
  const [discovery, setDiscovery] = useState(null);
  const [selectedName, setSelectedName] = useState(null);
  const operation = usePlatformAction();
  const busy = Boolean(operation.action);
  const connectionSaved = saved && draft.url === saved.url && draft.transport === saved.transport && draft.enabled === saved.enabled;
  const selectedTool = connectionSaved && discovery?.tools.find((tool) => tool.name === selectedName);
  const update = (changes) => setDraft((previous) => ({ ...previous, ...changes }));
  const save = (event) => {
    event.preventDefault();
    if (saved?.archived) return;
    return operation.run('save', async () => {
      const body = { name: draft.name, url: draft.url, transport: draft.transport, enabled: draft.enabled, allowed_tools: splitTools(allowedText) };
      const response = saved ? await platformApi.update('mcp', saved.id, body) : await platformApi.create('mcp', body);
      const item = response.item || response;
      setSaved(item);
      setDraft(item);
      setAllowedText(item.allowed_tools.join(', '));
      setDiscovery(null);
      setSelectedName(null);
      await onSaved(item);
    });
  };
  const archive = () => operation.run('archive', async () => {
    if (saved.archived) await platformApi.restore('mcp', saved.id);
    else await platformApi.archive('mcp', saved.id);
    await onArchive();
  });
  const discover = () => operation.run('discover', async () => {
    setSelectedName(null);
    setDiscovery(null);
    const value = await platformApi.discoverMcp(saved.id);
    if (value.id !== saved.id) throw new Error('发现工具的服务器与当前连接不匹配。');
    setDiscovery({ ...value, tools: catalogItems(value, 'tools') });
  });
  const grant = () => operation.run('grant', async () => {
    const { name, url, transport, enabled } = saved;
    const allowed_tools = [...new Set([...saved.allowed_tools, selectedTool.name])];
    const response = await platformApi.update('mcp', saved.id, { name, url, transport, enabled, allowed_tools });
    const item = response.item || response;
    setSaved(item);
    setAllowedText((previous) => [...new Set([...splitTools(previous), selectedTool.name])].join(', '));
    setDiscovery((previous) => ({ ...previous, tools: previous.tools.map((tool) => tool.name === selectedTool.name ? { ...tool, allowed: true } : tool) }));
    await onSaved(item);
  });
  const call = (args) => platformApi.callMcp(saved.id, { tool: selectedTool.name, arguments: args });
  return <>
    {operation.error && <div className="error-banner" role="alert">{operation.error}</div>}
    <form onSubmit={save}>
      <div className="platform-field-row">
        <label className="platform-field"><span>服务名称</span><input value={draft.name} onChange={(event) => update({ name: event.target.value })} disabled={busy} required /></label>
        <label className="platform-field"><span>协议</span><select value={draft.transport} onChange={(event) => update({ transport: event.target.value })} disabled={busy}><option value="streamable_http">Streamable HTTP</option><option value="sse">SSE</option></select></label>
      </div>
      <label className="platform-field"><span>服务 URL</span><input type="url" value={draft.url} onChange={(event) => update({ url: event.target.value })} placeholder="http://127.0.0.1:…/mcp" disabled={busy} required /></label>
      <label className="platform-field"><span>允许调用的工具（逗号分隔）</span><input value={allowedText} onChange={(event) => setAllowedText(event.target.value)} disabled={busy} /></label>
      <label className="mcp-enabled"><input type="checkbox" checked={draft.enabled} onChange={(event) => update({ enabled: event.target.checked })} disabled={busy} />启用服务</label>
      <div className="mcp-actions">
        <button className="primary-button" type="submit" disabled={busy || saved?.archived}>{operation.action === 'save' ? '正在保存' : '保存服务'}</button>
        {saved && <button className="subtle-button" type="button" onClick={archive} disabled={busy}>{saved.archived ? '恢复服务' : '归档服务'}</button>}
        <button className="subtle-button" type="button" onClick={discover} disabled={busy || !connectionSaved || !saved.enabled || saved.archived}>{operation.action === 'discover' ? '正在发现工具' : '连接并发现工具'}</button>
      </div>
    </form>
    {connectionSaved && discovery && <div className="mcp-discovered-tools">
      <p role="status">{discovery.tools.length ? '实际发现的工具' : '此服务没有发布工具。'}</p>
      {discovery.tools.map((tool) => <button className="subtle-button" type="button" key={tool.name} disabled={busy} onClick={() => { setSelectedName(tool.name); operation.setError(''); }}>{tool.name}{tool.allowed === false && '（未授权）'}</button>)}
      {discovery.health && <small>{typeof discovery.health === 'string' ? discovery.health : JSON.stringify(discovery.health)}</small>}
    </div>}
    {selectedTool && <ToolInvokePanel key={selectedTool.name} tool={selectedTool} operation={operation} call={call} onGrant={grant} />}
  </>;
}

function McpServices() {
  const active = useCatalog(loadActiveServers);
  const archived = useCatalog(loadArchivedServers);
  const [showArchived, setShowArchived] = useState(false);
  const [selection, setSelection] = useState({ revision: 0, item: null });
  const catalog = showArchived ? archived : active;
  const choose = (item) => setSelection((previous) => ({ revision: previous.revision + 1, item }));
  const refresh = () => Promise.all([active.refresh(true), archived.refresh(true)]);
  const onSaved = (item) => {
    setSelection((previous) => previous.revision === selection.revision ? { ...previous, item } : previous);
    return refresh();
  };
  const onArchive = () => {
    setSelection((previous) => previous.revision === selection.revision ? { revision: previous.revision + 1, item: null } : previous);
    return refresh();
  };
  return <div className="mcp-manager">
    <aside>
      <label className="archive-toggle"><input type="checkbox" checked={showArchived} onChange={(event) => setShowArchived(event.target.checked)} />查看归档服务</label>
      <CatalogFeedback catalog={catalog} label={showArchived ? '归档 MCP 服务' : 'MCP 服务'} empty={showArchived ? '没有归档的 MCP 服务。' : '还没有 MCP 服务。'} />
      {catalog.items?.map((item) => <button type="button" key={item.id} className={selection.item?.id === item.id ? 'selected' : ''} onClick={() => choose(item)}><strong>{item.name}</strong><small>{item.transport} · {item.enabled ? '已启用' : '未启用'}</small></button>)}
      <button type="button" onClick={() => choose(null)}><Icon name="plus" size={14} />新增服务</button>
    </aside>
    <section><McpServerEditor key={selection.revision} initialServer={selection.item} onSaved={onSaved} onArchive={onArchive} /></section>
  </div>;
}

export function ToolsWorkspace({ context }) {
  const [tab, setTab] = useState('tools');
  return <div className="tools-workspace">
    <div className="platform-inner-tabs" role="group" aria-label="工具分类">
      <button className={tab === 'tools' ? 'selected' : ''} aria-pressed={tab === 'tools'} onClick={() => setTab('tools')}>内置工具</button>
      <button className={tab === 'mcp' ? 'selected' : ''} aria-pressed={tab === 'mcp'} onClick={() => setTab('mcp')}>MCP 服务</button>
    </div>
    <div hidden={tab !== 'tools'}><BuiltInTools context={context} /></div>
    <div hidden={tab !== 'mcp'}><McpServices /></div>
  </div>;
}
