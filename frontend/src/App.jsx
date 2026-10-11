import { useCallback, useEffect, useRef, useState } from 'react';
import { platformApi, workbenchApi } from './api';
import { applyEvent, hydrateJob } from './workbench-state';
import { Icon, Markdown, JobOutput, ResultProvenance, StagePending } from './components/WorkbenchResults';
import ProjectWorkspace from './components/ProjectWorkspace';
import ToolSettings from './components/ToolSettings';
import WorkflowEditor from './components/WorkflowEditor';
import { defaultWorkflow, workflowErrors } from './workflow-settings';
import PlatformWorkspace from './components/PlatformWorkspace';
import ContextAttachments from './components/ContextAttachments';
import { EMPTY_CONTEXT, PLATFORM_SECTIONS, contextErrors, jobContext } from './platform-settings';
import './App.css';
import './components/PlatformTheme.css';

const MODES = {
  chat: { label: '多模型聊天', source: 'ChatHub', description: '同一个问题，同时听取不同模型的回答。', example: '比较两种科研论文阅读方法：先读摘要与结论，或先读方法与结果。给出各自适用场景。', action: '同时发送', icon: 'chat' },
  council: { label: '匿名评审', source: 'LLM Council', description: '独立回答、匿名互评，再由主席综合结论。', example: '我有 30 分钟阅读一篇科研论文。请设计阅读与核查流程，分配时间，并说明可能遗漏的证据。', action: '开始评审', icon: 'council' },
  camel: { label: '角色对话', source: 'CAMEL', description: '研究员与审稿人轮流讨论，逐步完善方案。', example: '讨论如何评估多动物跟踪中的身份连续性，区分技术诊断与可靠的实验验证。', action: '开始对话', icon: 'dialogue' },
  crewai: { label: '任务协作', source: 'CrewAI', description: '先起草，再审查，按分工完成一项任务。', example: '制定一份科研论文阅读与核查清单，覆盖方法、数据、统计与结论的证据边界。', action: '开始协作', icon: 'tasks' },
  group: { label: '模型群聊', source: 'MultiBotChat 式协作', description: '多个模型依次接话，共享一段讨论记录。', example: '讨论如何制定一份可执行的科研软件发布检查清单。先提出方案，再质询与完善。', action: '开始群聊', icon: 'group' },
  camel_workforce: { label: 'Workforce 团队', source: 'CAMEL 原生工作队', description: '由原生工作队规划和分解任务，多个角色协作执行。', example: '制定科研软件的发布方案，分别完成测试计划、数据核查与交付检查。', action: '启动工作队', icon: 'group' },
  crewai_flow: { label: '条件流程', source: 'CrewAI 原生 Flow', description: '按真实任务结果执行条件分支，保留路由与工具记录。', example: '先评估方案是否需要修正；需要时执行修正步骤，再进行最终复核。', action: '运行流程', icon: 'tasks' },
};
const STATUS_LABELS = { queued: '等待开始', running: '运行中', complete: '已完成', completed: '已完成', failed: '失败', error: '失败', cancelled: '已停止', interrupted: '已中断' };
const active = (job) => ['queued', 'running'].includes(job?.status);
const modelLabel = (model = '') => model.replace(/^openai\//, '');

function dateLabel(date) {
  if (!date) return '';
  return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(date));
}

function exportJob(job, format) {
  const link = document.createElement('a');
  link.href = workbenchApi.exportUrl(job.id, format);
  link.download = `ModelDesk-${job.mode}-${job.id.slice(0, 8)}.${format === 'json' ? 'json' : 'md'}`;
  link.hidden = true;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

export default function App() {
  const [mode, setMode] = useState('chat');
  const [projectView, setProjectView] = useState(() => localStorage.getItem('modeldesk.view') === 'projects');
  const [platformView, setPlatformView] = useState(() => !['projects', 'tools'].includes(localStorage.getItem('modeldesk.view')));
  const [platformSection, setPlatformSection] = useState(() => localStorage.getItem('modeldesk.platformSection') || 'overview');
  const [navigationTab, setNavigationTab] = useState(() => !['projects', 'tools'].includes(localStorage.getItem('modeldesk.view')) ? 'platform' : 'tools');
  const [context, setContext] = useState(() => ({ ...EMPTY_CONTEXT }));
  const [theme, setTheme] = useState(() => localStorage.getItem('modeldesk.theme') === 'dark' ? 'dark' : 'light');
  const [status, setStatus] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [jobId, setJobId] = useState(() => localStorage.getItem('modeldesk.lastJob') || null);
  const [job, setJob] = useState(null);
  const [models, setModels] = useState([]);
  const [chairman, setChairman] = useState('gpt-6.1-sol');
  const [rounds, setRounds] = useState(2);
  const [systemPrompt, setSystemPrompt] = useState('');
  const [relayPrompt, setRelayPrompt] = useState('请阅读其他参与者的发言，补充新的观点或指出具体问题，避免重复。');
  const [roles, setRoles] = useState({});
  const [contextTurns, setContextTurns] = useState(12);
  const [workflows, setWorkflows] = useState(() => { try { const saved = JSON.parse(localStorage.getItem('modeldesk.workflows') || '{}'); return saved && typeof saved === 'object' && !Array.isArray(saved) ? saved : {}; } catch { return {}; } });
  const [prompt, setPrompt] = useState('');
  const [starting, setStarting] = useState(false);
  const [canceling, setCanceling] = useState(false);
  const [error, setError] = useState('');
  const [streamError, setStreamError] = useState(false);
  const [modelMenu, setModelMenu] = useState(false);
  const [accountPanel, setAccountPanel] = useState(false);
  const [exportMenu, setExportMenu] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const inputRef = useRef(null);
  const streamRef = useRef(null);
  const preferencesSet = useRef(false);

  const refresh = useCallback(async () => {
    const [statusResult, jobsResult] = await Promise.allSettled([workbenchApi.status(), workbenchApi.listJobs()]);
    if (statusResult.status === 'fulfilled') {
      setStatus(statusResult.value);
      if (!preferencesSet.current) {
        preferencesSet.current = true;
        const available = statusResult.value.models || [];
        let saved = []; try { saved = JSON.parse(localStorage.getItem('modeldesk.models') || '[]'); } catch { /* Ignore stale preferences. */ }
        const valid = [...new Set((Array.isArray(saved) ? saved : []).filter((id) => available.some((m) => m.id === id)))].slice(0, 4);
        const defaults = ['gpt-6.1-sol', 'gemini-3-flash'].filter((id) => available.some((m) => m.id === id));
        const verified = available.filter((m) => m.verified).map((m) => m.id);
        setModels(valid.length ? valid : defaults.length === 2 ? defaults : [...new Set([...defaults, ...verified, ...available.map((m) => m.id)])].slice(0, 2));
      }
    } else setStatus({ gateway: { online: false, error: statusResult.reason.message }, providers: [], models: [], tools: {} });
    if (jobsResult.status === 'fulfilled') setJobs(Array.isArray(jobsResult.value) ? jobsResult.value : jobsResult.value.jobs || []);
    else setError(jobsResult.reason.message);
  }, []);

  useEffect(() => { refresh(); const timer = setInterval(refresh, 15000); return () => clearInterval(timer); }, [refresh]);
  useEffect(() => {
    if (!jobId) return;
    let disposed = false; let source;
    workbenchApi.getJob(jobId).then((loaded) => {
      if (disposed) return;
      if (!MODES[loaded.mode]) { setJobId(null); return; }
      setJob(hydrateJob(loaded)); setMode(loaded.mode); setStreamError(false); localStorage.setItem('modeldesk.lastJob', jobId);
      preferencesSet.current = true;
      setModels(loaded.models); localStorage.setItem('modeldesk.models', JSON.stringify(loaded.models));
      if (loaded.chairman) setChairman(loaded.chairman);
      if (loaded.rounds) setRounds(loaded.rounds);
      setSystemPrompt(loaded.system_prompt || ''); setRoles(loaded.roles || {}); setContextTurns(loaded.context_turns || 12);
      if (loaded.relay_prompt) setRelayPrompt(loaded.relay_prompt);
      if (loaded.workflow) setWorkflows((previous) => ({ ...previous, [loaded.mode]: loaded.workflow }));
      if (!active(loaded)) return;
      const after = Math.max(0, ...(loaded.events || []).map((e) => e.seq || 0)); source = workbenchApi.events(jobId, after); streamRef.current = source;
      source.onmessage = (message) => {
        if (disposed) return;
        let event; try { event = JSON.parse(message.data); } catch { return; }
        if (!event.seq && message.lastEventId) event.seq = Number(message.lastEventId);
        setStreamError(false); setJob((previous) => applyEvent(previous, event));
        if (['complete', 'error', 'cancelled'].includes(event.type)) { source.close(); workbenchApi.getJob(jobId).then((full) => { if (!disposed) setJob(hydrateJob(full)); }).catch(() => {}); refresh(); }
      };
      source.onerror = () => { if (!disposed) setStreamError(true); };
    }).catch((failure) => { if (!disposed) { setError(failure.message); setJobId(null); localStorage.removeItem('modeldesk.lastJob'); } });
    return () => { disposed = true; source?.close(); streamRef.current = null; };
  }, [jobId, refresh]);

  const selectMode = (next) => { setPlatformView(false); setNavigationTab('tools'); setProjectView(false); localStorage.setItem('modeldesk.view', 'tools'); setMode(next); setJobId(null); setJob(null); setError(''); setStreamError(false); setSidebarOpen(false); setModelMenu(false); setPrompt(''); localStorage.removeItem('modeldesk.lastJob'); };
  const selectJob = (item) => { setSidebarOpen(false); setPlatformView(false); setNavigationTab('tools'); setProjectView(false); localStorage.setItem('modeldesk.view', 'tools'); if (item.id === jobId) return; setMode(item.mode); setJob(null); setJobId(item.id); setPrompt(''); setError(''); };
  const newTask = () => { setJobId(null); setJob(null); setPrompt(''); setError(''); setStreamError(false); localStorage.removeItem('modeldesk.lastJob'); inputRef.current?.focus(); };
  const toggleModel = (id) => { const next = models.includes(id) ? models.filter((m) => m !== id) : models.length < 4 ? [...models, id] : models; setModels(next); localStorage.setItem('modeldesk.models', JSON.stringify(next)); };
  const tool = status?.tools?.[mode];
  const toolAvailable = typeof tool === 'boolean' ? tool : tool?.available !== false;
  const minimum = ['chat', 'crewai', 'camel_workforce', 'crewai_flow'].includes(mode) ? 1 : 2;
  const roleMode = ['camel', 'crewai', 'camel_workforce', 'crewai_flow'].includes(mode);
  const configuration = roleMode ? workflows[mode] || defaultWorkflow(mode, models) : null;
  const configurationErrors = roleMode ? workflowErrors(configuration, mode, models) : [];
  const selectionValid = models.length >= minimum && (mode !== 'camel' || models.length === 2) && configurationErrors.length === 0;
  const changeWorkflow = (next) => { const updated = { ...workflows, [mode]: next }; setWorkflows(updated); localStorage.setItem('modeldesk.workflows', JSON.stringify(updated)); };
  const running = active(job);
  const canSubmit = Boolean(prompt.trim() && !starting && !running && status?.gateway?.online && toolAvailable && selectionValid && contextErrors(context).length === 0);
  const send = async (event) => {
    event?.preventDefault(); if (!canSubmit) return;
    setStarting(true); setError(''); setStreamError(false); setModelMenu(false);
    try { const created = await workbenchApi.createJob({ mode, prompt: prompt.trim(), models, chairman: mode === 'council' ? chairman : undefined, rounds, conversation_id: ['chat', 'group'].includes(mode) && job?.mode === mode ? job.conversation_id : undefined, ...(['chat', 'group'].includes(mode) ? { system_prompt: systemPrompt, context_turns: contextTurns } : {}), ...(mode === 'group' ? { relay_prompt: relayPrompt, roles: Object.fromEntries(models.map((id) => [id, roles[id] || ''])) } : {}), ...jobContext(context), ...(roleMode ? { workflow: { ...configuration, knowledge_ids: context.knowledge_ids, memory_scope: context.memory_scope, project_id: context.project_id, snapshot_id: context.snapshot_id, mcp_server_ids: context.mcp_server_ids } } : {}) }); setJob(created); setJobId(created.id); setPrompt(''); setJobs((previous) => [created, ...previous.filter((j) => j.id !== created.id)]); }
    catch (failure) { setError(failure.message); } finally { setStarting(false); }
  };
  const cancel = async () => { if (!job || canceling) return; setCanceling(true); try { const stopped = await workbenchApi.cancelJob(job.id); setJob(hydrateJob(stopped)); streamRef.current?.close(); setStreamError(false); refresh(); } catch (failure) { setError(failure.message); } finally { setCanceling(false); } };
  const manualRefresh = async () => { setRefreshing(true); await refresh(); setRefreshing(false); };
  const history = jobs.filter((j) => j.mode === mode);
  const activeJobs = jobs.filter(active);
  const config = MODES[mode];
  const lastLog = [...(job?.events || [])].reverse().find((e) => e.type === 'log')?.text;
  const selectedCatalog = status?.models || [];
  const openPlatform = (section) => { setPlatformView(true); setProjectView(false); setPlatformSection(section); setNavigationTab('platform'); setSidebarOpen(false); localStorage.setItem('modeldesk.view', 'platform'); localStorage.setItem('modeldesk.platformSection', section); };
  const openProjects = () => { setPlatformView(false); setProjectView(true); setNavigationTab('tools'); setSidebarOpen(false); localStorage.setItem('modeldesk.view', 'projects'); };
  const usePrompt = (resource) => { const next = Object.hasOwn(MODES, resource.mode) ? resource.mode : 'chat'; selectMode(next); setPrompt(resource.content); };
  const useAgent = (agent) => { selectMode('crewai'); setModels([agent.model]); const workflow = { agents: [{ id: 'agent1', role: agent.role, goal: agent.goal, backstory: agent.backstory || '', model: agent.model, tools: agent.tools || [] }], tasks: [{ id: 'task1', description: '完成用户在任务输入中提出的要求。', expected_output: '符合用户要求、注明依据与限制的结果。', agent: 'agent1', context: [] }], process: 'sequential', manager_model: null, max_iter: 3 }; setWorkflows((previous) => ({ ...previous, crewai: workflow })); };
  const onPlatformRun = (created) => { setJobs((previous) => [created, ...previous.filter((item) => item.id !== created.id)]); refresh(); };
  const savePrompt = async () => { if (!prompt.trim()) return; try { await platformApi.create('prompts', { name: prompt.trim().slice(0, 35), content: prompt.trim(), category: config.label, tags: [], mode }); openPlatform('prompts'); } catch (failure) { setError(failure.message); } };

  return <div className="modeldesk" data-theme={theme}>
    {sidebarOpen && <button className="sidebar-backdrop" aria-label="关闭导航" onClick={() => setSidebarOpen(false)} />}
    <aside className={`sidebar ${sidebarOpen ? 'sidebar-open' : ''}`}>
      <div className="brand"><span className="brand-symbol"><i /><i /><i /><i /></span><div><strong>ModelDesk</strong><span>多智能体平台</span></div></div>
      <div className="sidebar-navigation-tabs"><button className={navigationTab === 'tools' ? 'selected' : ''} onClick={() => setNavigationTab('tools')}>协作工作台</button><button className={navigationTab === 'platform' ? 'selected' : ''} onClick={() => setNavigationTab('platform')}>平台资源</button></div>
      <div className="sidebar-primary-navigation">{navigationTab === 'platform' ? <nav className="platform-navigation" aria-label="平台功能">{Object.entries(PLATFORM_SECTIONS).map(([id, item]) => <button className={platformView && platformSection === id ? 'active' : ''} key={id} onClick={() => openPlatform(id)}><Icon name={item[1]} size={18} /><span>{item[0]}</span></button>)}</nav> : <>
      <button className={`projects-entry ${projectView ? 'active' : ''}`} onClick={openProjects}><Icon name="folder" size={20} /><span><strong>项目与代码评审</strong><small>本地项目与真实代码上下文</small></span></button><button className="new-task" onClick={() => { setPlatformView(false); setProjectView(false); localStorage.setItem('modeldesk.view', 'tools'); newTask(); }}><Icon name="plus" size={18} />新建{mode === 'group' ? '群聊' : mode === 'chat' ? '聊天' : '任务'}</button>
      <nav className="mode-navigation" aria-label="工作模式">{Object.entries(MODES).map(([id, item]) => <button key={id} className={!platformView && !projectView && mode === id ? 'active' : ''} onClick={() => selectMode(id)}><Icon name={item.icon} size={20} /><span><strong>{item.label}</strong><small>{item.source}</small></span>{activeJobs.some((j) => j.mode === id) && <span className="nav-running" title="有任务运行中" />}</button>)}</nav>
      </>}</div>
      <div className="history-heading"><span>{projectView ? '工具记录' : '最近记录'}</span><span>{history.length}</span></div>
      <div className="history-list">{history.length ? history.map((item) => <button key={item.id} className={`history-item ${jobId === item.id ? 'selected' : ''}`} onClick={() => selectJob(item)}><span className="history-title">{item.prompt || '未命名任务'}</span><span className="history-meta"><span className={`history-status status-${item.status}`}>{STATUS_LABELS[item.status] || item.status}</span><time>{dateLabel(item.created_at)}</time></span></button>) : <p className="history-empty">首次运行后，记录会自动保存在这里。</p>}</div>
      <div className="sidebar-footer"><button className="theme-toggle" onClick={() => { const next = theme === 'light' ? 'dark' : 'light'; setTheme(next); localStorage.setItem('modeldesk.theme', next); }}><span><Icon name={theme === 'light' ? 'moon' : 'sun'} size={14} />{theme === 'light' ? '切换深色模式' : '切换浅色模式'}</span><small>{theme === 'light' ? '浅色' : '深色'}</small></button><div className="gateway-indicator"><span className={`status-dot ${status?.gateway?.online ? 'online' : 'offline'}`} /><strong>{status ? status.gateway?.online ? '本地网关已连接' : '本地网关未连接' : '正在检查连接'}</strong></div><span>认证信息保留在本机</span><button onClick={() => { setPlatformView(false); setProjectView(false); setAccountPanel(true); }}>查看账户与连接<Icon name="settings" size={14} /></button></div>
    </aside>
    <>{platformView ? <PlatformWorkspace section={platformSection} onSection={openPlatform} status={status} jobs={jobs} models={models} context={context} onContextChange={setContext} onUsePrompt={usePrompt} onUseAgent={useAgent} onRunJob={onPlatformRun} onOpenProjects={openProjects} onOpenNavigation={() => setSidebarOpen(true)} /> : projectView ? <ProjectWorkspace status={status} jobs={jobs} models={models} onModelsChange={(next) => { setModels(next); localStorage.setItem('modeldesk.models', JSON.stringify(next)); }} onRefresh={refresh} onOpenNavigation={() => setSidebarOpen(true)} /> : <main className="workspace">
      <header className="workspace-header"><div className="header-title"><button className="mobile-menu icon-button" aria-label="打开导航" onClick={() => setSidebarOpen(true)}><Icon name="menu" /></button><span className="mode-header-icon"><Icon name={config.icon} size={22} /></span><div><h1>{config.label}</h1><span>{config.source}</span></div></div><div className="header-actions"><button className={`account-toggle ${accountPanel ? 'selected' : ''}`} onClick={() => setAccountPanel(!accountPanel)}><span className={`status-dot ${status?.providers?.some((p) => p.authenticated) ? 'online' : 'offline'}`} />已有账户<Icon name="chevron" size={14} /></button><button className="subtle-button" disabled={!prompt.trim()} onClick={savePrompt}>保存提示</button><div className="export-control"><button className="subtle-button" disabled={!job} onClick={() => setExportMenu(!exportMenu)}><Icon name="export" size={17} />导出</button>{exportMenu && job && <div className="small-menu"><button onClick={() => { exportJob(job, 'md'); setExportMenu(false); }}>导出 Markdown</button><button onClick={() => { exportJob(job, 'json'); setExportMenu(false); }}>导出完整 JSON</button></div>}</div></div></header>
      {accountPanel && <section className="account-panel" aria-label="账户与连接状态"><div className="account-panel-title"><div><strong>账户与连接</strong><span>凭据存在与模型调用验证分别显示</span></div><button className="icon-button" aria-label="刷新连接状态" disabled={refreshing} onClick={manualRefresh}><Icon name="refresh" size={18} /></button><button className="icon-button" aria-label="关闭账户面板" onClick={() => setAccountPanel(false)}><Icon name="close" size={18} /></button></div><div className="provider-list">{status?.providers?.length ? status.providers.map((provider) => <div className="provider-row" key={provider.id}><span className={`status-dot ${provider.authenticated ? 'online' : 'offline'}`} /><strong>{provider.label}</strong><span>{provider.authenticated ? '已发现登录凭据' : '未接入'}</span><span className="provider-verification">{provider.verified_models?.length ? `${provider.verified_models.length} 个模型已验证` : '尚无已验证调用'}</span>{provider.reason && <p>{provider.reason}</p>}</div>) : <p className="result-note">{status?.gateway?.error || '正在读取本地登录状态…'}</p>}</div><p className="account-footnote">Claude 订阅登录未接入第三方网关。当前工作台共享 ChatGPT 与 Google / Antigravity 的已有认证。</p></section>}
      <section className="configuration-bar" aria-label="模型与任务设置"><div className="model-control"><span className="control-label">参与模型</span><button className={`model-selector ${modelMenu ? 'open' : ''}`} onClick={() => setModelMenu(!modelMenu)} disabled={running || starting}><span>{models.length ? models.map(modelLabel).join(' + ') : '选择模型'}</span><Icon name="chevron" size={15} /></button>{modelMenu && <div className="model-popover"><div className="model-popover-heading"><strong>选择参与模型</strong><span>{mode === 'camel' ? '选择两个模型，分别承担角色' : `可选择 ${minimum}–4 个模型`}</span></div><div className="model-options">{selectedCatalog.map((model) => <label key={model.id} className={models.includes(model.id) ? 'checked' : ''}><input type="checkbox" checked={models.includes(model.id)} disabled={!models.includes(model.id) && models.length >= 4} onChange={() => toggleModel(model.id)} /><span>{modelLabel(model.id)}</span><small className={model.verified ? 'verified-label' : ''}>{model.verified ? '已验证' : '未验证'}</small></label>)}</div><div className="model-popover-footer">未验证模型的可用性以实际调用为准<button onClick={() => setModelMenu(false)}>完成</button></div></div>}</div>{mode === 'council' && <label className="inline-setting"><span>主席</span><select value={chairman} onChange={(e) => setChairman(e.target.value)} disabled={running}>{selectedCatalog.map((model) => <option value={model.id} key={model.id}>{modelLabel(model.id)}</option>)}</select></label>}{['camel', 'camel_workforce', 'group'].includes(mode) && <label className="inline-setting"><span>讨论轮数</span><select value={rounds} onChange={(e) => setRounds(Number(e.target.value))} disabled={running}>{[1, 2, 3, 4, 5, 6].map((n) => <option key={n} value={n}>{n} 轮</option>)}</select></label>}{roleMode && <span className="role-assignment">{configuration.agents.map((agent) => agent.role || '未命名角色').join(' / ')}，可编辑分工</span>}</section>
      <ContextAttachments mode={mode} value={context} onChange={setContext} jobs={jobs} disabled={running || starting} onOpenProjects={openProjects} />
      <ToolSettings mode={mode} models={models} systemPrompt={systemPrompt} onSystemPrompt={setSystemPrompt} relayPrompt={relayPrompt} onRelayPrompt={setRelayPrompt} roles={roles} onRoles={setRoles} contextTurns={contextTurns} onContextTurns={setContextTurns} disabled={running || starting} />
      <div className="work-surface">
        <WorkflowEditor mode={mode} models={models} workflow={configuration} onChange={changeWorkflow} disabled={running || starting} />
        {error && <div role="alert" className="error-banner"><span>{error}</span><button className="icon-button" aria-label="关闭错误提示" onClick={() => setError('')}><Icon name="close" size={15} /></button></div>}
        {status && !status.gateway?.online && <div className="connection-warning"><strong>本地网关未连接</strong><span>请双击 ModelDesk 启动入口，再点击刷新。</span><button className="subtle-button" onClick={manualRefresh} disabled={refreshing}><Icon name="refresh" size={15} />刷新</button></div>}
        {!toolAvailable && <div className="connection-warning"><strong>{config.source} 暂不可用</strong><span>{tool?.reason || '请检查本地依赖与服务。'}</span></div>}
        {jobId && !job ? <StagePending text="正在读取保存的记录" running /> : job ? <article className="job-detail"><div className="prompt-heading"><span className="prompt-label">{job.mode === 'chat' ? '你的问题' : '任务要求'}</span><div className={`job-status status-${job.status}`}>{running && <span className="activity-dot" />}{STATUS_LABELS[job.status] || job.status}</div><time>{dateLabel(job.created_at)}</time></div><div className="prompt-text"><Markdown>{job.prompt}</Markdown></div>{running && <div className="run-progress"><span className="small-spinner" /><span>{lastLog || (mode === 'chat' ? '各模型正在独立回答，可随时停止' : mode === 'council' ? '正在执行独立回答、匿名评审与主席综合' : mode === 'group' ? '群聊模型正在依次阅读记录并发言' : '正在执行角色分工与协作')}</span></div>}{streamError && running && <div className="stream-warning">实时连接暂时中断，正在重连。后台任务继续运行。</div>}{job.error && <div className="output-error" role="alert">{job.error}</div>}{job.status === 'cancelled' && !['camel', 'crewai'].includes(job.mode) && <p className="result-note">任务已停止。已收到的内容保留在记录中。</p>}<JobOutput key={job.id} job={job} /><ResultProvenance job={job} /></article> : <div className="empty-workspace"><div className="workspace-illustration" aria-hidden="true"><div className="illustration-input"><span /><span /></div><div className="illustration-lines"><i /><i /></div><div className="illustration-responses"><div><b className="illustration-model">O</b><span /><span /><span /></div><div><b className="illustration-model gemini-mark">G</b><span /><span /><span /></div></div></div><h2>{mode === 'chat' ? '一个问题，多种思路' : mode === 'council' ? '让回答接受另一轮审视' : mode === 'camel' ? '把想法放进一场讨论' : mode === 'group' ? '让不同模型接着聊' : '把任务交给一个协作团队'}</h2><p>{config.description}</p><button className="example-prompt" onClick={() => { setPrompt(config.example); inputRef.current?.focus(); }}><span>试一试</span>{config.example}<Icon name="plus" size={17} /></button><div className="empty-flow">{mode === 'chat' ? <><span>统一提问</span><i /><span>同时回答</span><i /><span>并排比较</span></> : mode === 'council' ? <><span>独立回答</span><i /><span>匿名互评</span><i /><span>主席综合</span></> : mode === 'camel' ? <><span>研究员提出</span><i /><span>审稿人质询</span><i /><span>多轮完善</span></> : mode === 'group' ? <><span>共享话题</span><i /><span>依次接话</span><i /><span>多轮讨论</span></> : <><span>明确任务</span><i /><span>起草方案</span><i /><span>审查交付</span></>}</div></div>}
      </div>
      <div className="composer-wrap"><form className="composer" onSubmit={send}><textarea ref={inputRef} aria-label={mode === 'chat' ? '输入问题' : '输入任务要求'} value={prompt} onChange={(e) => setPrompt(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(e); } }} rows={3} placeholder={['chat', 'group'].includes(mode) && job ? mode === 'group' ? '继续这个话题，各模型将阅读已有群聊记录…' : '继续提问，保留这段聊天的上下文…' : '写下你的问题或任务要求…'} disabled={running || starting} /><div className="composer-footer"><span>{['chat', 'group'].includes(mode) && job ? mode === 'group' ? '继续当前群聊话题' : '继续当前聊天' : 'Enter 发送，Shift + Enter 换行'}{!selectionValid && <strong>{configurationErrors[0] || (mode === 'camel' ? '请选择恰好两个模型' : `请至少选择 ${minimum} 个模型`)}</strong>}</span>{running ? <button className="stop-button" type="button" disabled={canceling} onClick={cancel}><Icon name="stop" size={16} />{canceling ? '正在停止' : '停止运行'}</button> : <button className="primary-button" type="submit" disabled={!canSubmit}><Icon name="send" size={16} />{starting ? '正在启动' : config.action}</button>}</div></form><div className="composer-note"><span>使用已有登录认证</span><span>结果与记录自动保存在本机</span></div></div>
    </main>}</>
  </div>;
}
