import { useCallback, useEffect, useRef, useState } from 'react';
import { platformApi, workbenchApi } from './api';
import { applyEvent, hydrateJob } from './workbench-state';
import { Icon, Markdown, JobOutput, ResultProvenance, StagePending } from './components/WorkbenchResults';
import ProjectWorkspace from './components/ProjectWorkspace';
import ToolSettings from './components/ToolSettings';
import WorkflowEditor from './components/WorkflowEditor';
import WorkflowFailureNotice from './components/WorkflowFailureNotice';
import { defaultWorkflow, workflowErrors } from './workflow-settings';
import PlatformWorkspace from './components/PlatformWorkspace';
import ContextAttachments from './components/ContextAttachments';
import ArtifactPanel from './components/ArtifactPanel';
import { CopyTextButton } from './components/ConversationActions';
import { SESSION_KEY, DRAFT_KEY, readSessions, readConversationDrafts, conversationDraft, sourceSelection, conversationPreferences, restoredSession, conversationGroups, previousTurns, taskTitle } from './workbench-sessions';
import { EMPTY_CONTEXT, PLATFORM_SECTIONS, contextErrors, jobContext } from './platform-settings';
import './App.css';
import './components/PlatformTheme.css';
import './WorkbenchShell.css';

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
  const [mode, setMode] = useState(() => Object.hasOwn(MODES, localStorage.getItem('modeldesk.mode')) ? localStorage.getItem('modeldesk.mode') : 'chat');
  const [bootPreferences] = useState(() => conversationPreferences(readSessions(localStorage)[mode]?.preferences));
  const [projectView, setProjectView] = useState(() => localStorage.getItem('modeldesk.view') === 'projects');
  const [platformView, setPlatformView] = useState(() => !['projects', 'tools'].includes(localStorage.getItem('modeldesk.view')));
  const [platformSection, setPlatformSection] = useState(() => localStorage.getItem('modeldesk.platformSection') || 'overview');
  const [navigationTab, setNavigationTab] = useState(() => !['projects', 'tools'].includes(localStorage.getItem('modeldesk.view')) ? 'platform' : 'tools');
  const [context, setContext] = useState(() => sourceSelection(readSessions(localStorage)[localStorage.getItem('modeldesk.mode') || 'chat']?.context));
  const [theme, setTheme] = useState(() => localStorage.getItem('modeldesk.theme') === 'dark' ? 'dark' : 'light');
  const [status, setStatus] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [jobId, setJobId] = useState(() => localStorage.getItem('modeldesk.lastJob') || readSessions(localStorage)[localStorage.getItem('modeldesk.mode') || 'chat']?.job_id || null);
  const [job, setJob] = useState(null);
  const [models, setModels] = useState(bootPreferences.models);
  const [chairman, setChairman] = useState(bootPreferences.chairman);
  const [rounds, setRounds] = useState(bootPreferences.rounds);
  const [systemPrompt, setSystemPrompt] = useState(bootPreferences.system_prompt);
  const [relayPrompt, setRelayPrompt] = useState(bootPreferences.relay_prompt);
  const [roles, setRoles] = useState(bootPreferences.roles);
  const [contextTurns, setContextTurns] = useState(bootPreferences.context_turns);
  const [workflows, setWorkflows] = useState(() => { try { const saved = JSON.parse(localStorage.getItem('modeldesk.workflows') || '{}'); return saved && typeof saved === 'object' && !Array.isArray(saved) ? saved : {}; } catch { return {}; } });
  const [prompt, setPrompt] = useState(() => readSessions(localStorage)[localStorage.getItem('modeldesk.mode') || 'chat']?.draft || '');
  const [workflowEditSignal, setWorkflowEditSignal] = useState(0);
  const [starting, setStarting] = useState(false);
  const [canceling, setCanceling] = useState(false);
  const [error, setError] = useState('');
  const [streamError, setStreamError] = useState(false);
  const [modelMenu, setModelMenu] = useState(false);
  const [accountPanel, setAccountPanel] = useState(false);
  const [exportMenu, setExportMenu] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [historyQuery, setHistoryQuery] = useState('');
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [modeMenu, setModeMenu] = useState(false);
  const [rememberConversation, setRememberConversation] = useState(() => localStorage.getItem('modeldesk.conversationMemory') !== 'off');
  const [artifactsOpen, setArtifactsOpen] = useState(() => localStorage.getItem('modeldesk.artifactsOpen') !== 'off');
  const [artifactWidth, setArtifactWidth] = useState(() => Math.max(320, Math.min(Math.max(320, Math.min(680, window.innerWidth - 744)), Number(localStorage.getItem('modeldesk.artifactWidth')) || 420)));
  const [artifactJob, setArtifactJob] = useState(null);
  const [projectTarget, setProjectTarget] = useState(null);
  const [addProjectSignal, setAddProjectSignal] = useState(0);
  const [mobileArtifactsOpen, setMobileArtifactsOpen] = useState(false);
  const [loadedTurns, setLoadedTurns] = useState([]);
  const inputRef = useRef(null);
  const streamRef = useRef(null);
  const preferencesSet = useRef(false);
  const initialModeSet = useRef(Object.hasOwn(MODES, localStorage.getItem('modeldesk.mode')));
  const modeRef = useRef(mode);
  modeRef.current = mode;
  const artifactRequest = useRef(0);
  const sessionsRef = useRef(readSessions(localStorage));
  const conversationDraftsRef = useRef(readConversationDrafts(localStorage));
  const rememberSession = useCallback((nextMode, patch) => {
    sessionsRef.current = { ...sessionsRef.current, [nextMode]: { ...sessionsRef.current[nextMode], ...patch } };
    localStorage.setItem(SESSION_KEY, JSON.stringify(sessionsRef.current));
  }, []);

  useEffect(() => {
    localStorage.setItem('modeldesk.mode', mode);
    sessionsRef.current = { ...sessionsRef.current, [mode]: { ...sessionsRef.current[mode], draft: prompt, context: sourceSelection(context), preferences: { models, chairman, rounds, system_prompt: systemPrompt, relay_prompt: relayPrompt, roles, context_turns: contextTurns } } };
    localStorage.setItem(SESSION_KEY, JSON.stringify(sessionsRef.current));
  }, [mode, prompt, context, models, chairman, rounds, systemPrompt, relayPrompt, roles, contextTurns]);

  useEffect(() => {
    if (!job || job.id !== jobId) return;
    conversationDraftsRef.current = { ...conversationDraftsRef.current, [job.conversation_id || job.id]: prompt };
    localStorage.setItem(DRAFT_KEY, JSON.stringify(conversationDraftsRef.current));
  }, [job, jobId, prompt]);

  const refresh = useCallback(async () => {
    const [statusResult, jobsResult] = await Promise.allSettled([workbenchApi.status(), workbenchApi.listJobs()]);
    if (statusResult.status === 'fulfilled') {
      setStatus(statusResult.value);
      if (!preferencesSet.current) {
        preferencesSet.current = true;
        const available = statusResult.value.models || [];
        let saved = []; try { saved = sessionsRef.current[modeRef.current]?.preferences?.models?.length ? sessionsRef.current[modeRef.current].preferences.models : JSON.parse(localStorage.getItem('modeldesk.models') || '[]'); } catch { /* Ignore stale preferences. */ }
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
      setJob(hydrateJob(loaded)); setStreamError(false); localStorage.setItem('modeldesk.lastJob', jobId);
      const selectedMode = initialModeSet.current ? modeRef.current : loaded.mode;
      if (!initialModeSet.current) { setMode(loaded.mode); initialModeSet.current = true; }
      const saved = restoredSession(sessionsRef.current, selectedMode, loaded);
      setContext(saved.context);
      rememberSession(selectedMode, { job_id: loaded.id });
      preferencesSet.current = true;
      const restoredModels = saved.preferences.models.length ? saved.preferences.models : loaded.models;
      setModels(restoredModels); localStorage.setItem('modeldesk.models', JSON.stringify(restoredModels));
      setChairman(saved.preferences.chairman); setRounds(saved.preferences.rounds);
      setSystemPrompt(saved.preferences.system_prompt); setRoles(saved.preferences.roles); setContextTurns(saved.preferences.context_turns);
      setRelayPrompt(saved.preferences.relay_prompt);
      if (loaded.workflow) setWorkflows((previous) => previous[loaded.mode] ? previous : ({ ...previous, [loaded.mode]: loaded.workflow }));
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
  }, [jobId, refresh, rememberSession]);

  const selectMode = (next) => {
    initialModeSet.current = true;
    rememberSession(mode, { job_id: jobId, draft: prompt, context: sourceSelection(context) });
    rememberSession(next, { job_id: jobId, draft: prompt, context: sourceSelection(context) });
    setMode(next); setPlatformView(false); setProjectView(false); setNavigationTab('tools'); setSidebarOpen(false); setModeMenu(false); setSettingsOpen(false); setError('');
    artifactRequest.current += 1; setArtifactJob(null);
    localStorage.setItem('modeldesk.view', 'tools');
  };
  const selectJob = (item) => {
    initialModeSet.current = true;
    if (['review', 'sdk_script'].includes(item.mode)) {
      if (item.mode === 'review') openProjects({ project_id: item.project_id, review_id: item.id });
      const request = ++artifactRequest.current;
      workbenchApi.getJob(item.id).then((full) => { if (request === artifactRequest.current) { setArtifactJob(hydrateJob(full)); setArtifactsOpen(true); setMobileArtifactsOpen(true); } }).catch((failure) => setError(failure.message));
      return;
    }
    if (!Object.hasOwn(MODES, item.mode)) return;
    rememberSession(mode, { job_id: jobId, draft: prompt, context: sourceSelection(context) });
    const draft = item.id === jobId ? prompt : conversationDraft(conversationDraftsRef.current, item);
    rememberSession(item.mode, { job_id: item.id, draft, context: sourceSelection(item) });
    setSidebarOpen(false); setPlatformView(false); setNavigationTab('tools'); setProjectView(false); localStorage.setItem('modeldesk.view', 'tools');
    artifactRequest.current += 1; setArtifactJob(null);
    if (item.id === jobId) return;
    setMode(item.mode); setJob(null); setArtifactJob(null); setJobId(item.id); setPrompt(draft); setContext(sourceSelection(item)); setError('');
  };
  const newTask = (nextMode) => { const target = typeof nextMode === 'string' && Object.hasOwn(MODES, nextMode) ? nextMode : mode; artifactRequest.current += 1; rememberSession(target, { job_id: null, draft: '' }); setJobId(null); setJob(null); setArtifactJob(null); setPrompt(''); setError(''); setStreamError(false); localStorage.removeItem('modeldesk.lastJob'); inputRef.current?.focus(); };
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
    const request = artifactRequest.current;
    setStarting(true); setError(''); setStreamError(false); setModelMenu(false);
    try { const created = await workbenchApi.createJob({ mode, prompt: prompt.trim(), models, chairman: mode === 'council' ? chairman : undefined, rounds, conversation_id: rememberConversation ? job?.conversation_id : undefined, system_prompt: systemPrompt, context_turns: contextTurns, ...(mode === 'group' ? { relay_prompt: relayPrompt, roles: Object.fromEntries(models.map((id) => [id, roles[id] || ''])) } : {}), ...jobContext(context), ...(roleMode ? { workflow: { ...configuration, knowledge_ids: context.knowledge_ids, memory_scope: context.memory_scope, project_id: context.project_id, snapshot_id: context.snapshot_id, mcp_server_ids: context.mcp_server_ids } } : {}) }); setJobs((previous) => [created, ...previous.filter((j) => j.id !== created.id)]); if (request === artifactRequest.current) { rememberSession(mode, { job_id: created.id, draft: '', context: sourceSelection(context) }); setJob(created); setJobId(created.id); setArtifactJob(null); setPrompt(''); } }
    catch (failure) { if (request === artifactRequest.current) setError(failure.message); } finally { setStarting(false); }
  };
  const cancel = async () => { if (!job || canceling) return; const request = artifactRequest.current; setCanceling(true); try { const stopped = await workbenchApi.cancelJob(job.id); if (request === artifactRequest.current) { setJob(hydrateJob(stopped)); streamRef.current?.close(); setStreamError(false); } refresh(); } catch (failure) { if (request === artifactRequest.current) setError(failure.message); } finally { setCanceling(false); } };
  const prepareFailedTask = (category) => { setPrompt(job.prompt || ''); setError(''); if (category === 'source_unverified') { inputRef.current?.focus(); inputRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' }); } else { setSettingsOpen(true); setWorkflowEditSignal((signal) => signal + 1); } };
  const manualRefresh = async () => { setRefreshing(true); await refresh(); setRefreshing(false); };
  const history = conversationGroups(jobs, historyQuery);
  const priorTurns = previousTurns(jobs, job);
  const priorTurnKey = priorTurns.slice(-contextTurns).map((item) => item.id).join('|');
  useEffect(() => {
    let disposed = false;
    const ids = priorTurnKey ? priorTurnKey.split('|') : [];
    Promise.allSettled(ids.map((id) => workbenchApi.getJob(id))).then((results) => {
      if (!disposed) setLoadedTurns(results.filter((result) => result.status === 'fulfilled').map((result) => hydrateJob(result.value)));
    });
    return () => { disposed = true; };
  }, [priorTurnKey]);
  const activeJobs = jobs.filter(active);
  const config = MODES[mode];
  const lastLog = [...(job?.events || [])].reverse().find((e) => e.type === 'log')?.text;
  const selectedCatalog = status?.models || [];
  const openPlatform = (section) => { artifactRequest.current += 1; setArtifactJob(null); setPlatformView(true); setProjectView(false); setPlatformSection(section); setNavigationTab('platform'); setSidebarOpen(false); localStorage.setItem('modeldesk.view', 'platform'); localStorage.setItem('modeldesk.platformSection', section); };
  const openProjects = (target) => { artifactRequest.current += 1; setArtifactJob(null); setProjectTarget(target?.project_id ? target : null); setPlatformView(false); setProjectView(true); setNavigationTab('tools'); setSidebarOpen(false); localStorage.setItem('modeldesk.view', 'projects'); };
  const beginAddProject = () => { setAddProjectSignal((signal) => signal + 1); openProjects(); };
  const usePrompt = (resource) => { const next = Object.hasOwn(MODES, resource.mode) ? resource.mode : 'chat'; selectMode(next); newTask(next); setPrompt(resource.content); };
  const useAgent = (agent) => { selectMode('crewai'); newTask('crewai'); setModels([agent.model]); const workflow = { agents: [{ id: 'agent1', role: agent.role, goal: agent.goal, backstory: agent.backstory || '', model: agent.model, tools: agent.tools || [] }], tasks: [{ id: 'task1', description: '完成用户在任务输入中提出的要求。', expected_output: '符合用户要求、注明依据与限制的结果。', agent: 'agent1', context: [] }], process: 'sequential', manager_model: null, max_iter: 3 }; setWorkflows((previous) => ({ ...previous, crewai: workflow })); };
  const onPlatformRun = (created) => { setJobs((previous) => [created, ...previous.filter((item) => item.id !== created.id)]); refresh(); };
  const savePrompt = async () => { if (!prompt.trim()) return; try { await platformApi.create('prompts', { name: prompt.trim().slice(0, 35), content: prompt.trim(), category: config.label, tags: [], mode }); openPlatform('prompts'); } catch (failure) { setError(failure.message); } };
  const toggleArtifacts = () => {
    if (window.innerWidth <= 1180) { setArtifactsOpen(true); setMobileArtifactsOpen(!mobileArtifactsOpen); return; }
    const next = !artifactsOpen; setArtifactsOpen(next); localStorage.setItem('modeldesk.artifactsOpen', next ? 'on' : 'off');
  };
  const resizeArtifacts = (event) => {
    event.preventDefault(); const initial = event.clientX; const width = artifactWidth;
    const move = (pointer) => { const next = Math.max(320, Math.min(Math.max(320, Math.min(680, window.innerWidth - 744)), width + initial - pointer.clientX)); setArtifactWidth(next); localStorage.setItem('modeldesk.artifactWidth', String(next)); };
    const done = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', done); };
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', done, { once: true });
  };

  return <div className="modeldesk" data-theme={theme}>
    {sidebarOpen && <button className="sidebar-backdrop" aria-label="关闭导航" onClick={() => setSidebarOpen(false)} />}
    <aside className={`sidebar ${sidebarOpen ? 'sidebar-open' : ''}`}>
      <div className="brand"><span className="brand-symbol"><i /><i /><i /><i /></span><div><strong>ModelDesk</strong><span>多智能体平台</span></div></div>
      <div className="sidebar-navigation-tabs"><button className={navigationTab === 'tools' ? 'selected' : ''} onClick={() => selectMode(mode)}>协作工作台</button><button className={navigationTab === 'platform' ? 'selected' : ''} onClick={() => openPlatform('overview')}>项目资源</button></div>
      <button className="new-task" onClick={() => { setPlatformView(false); setProjectView(false); localStorage.setItem('modeldesk.view', 'tools'); newTask(); }}><Icon name="plus" size={18} />新建会话</button>
      <button className={`projects-entry ${projectView ? 'active' : ''}`} onClick={openProjects}><Icon name="folder" size={18} /><span><strong>我的项目</strong><small>添加代码项目与审查</small></span></button>
      <div className="sidebar-primary-navigation">{navigationTab === 'platform' ? <nav className="platform-navigation" aria-label="项目资源">{['overview', 'prompts', 'agents'].map((id) => <button className={platformView && platformSection === id ? 'active' : ''} key={id} onClick={() => openPlatform(id)}><Icon name={PLATFORM_SECTIONS[id][1]} size={18} /><span>{PLATFORM_SECTIONS[id][0]}</span></button>)}<details className="advanced-resource-navigation"><summary><Icon name="settings" size={16} />高级资源<Icon name="chevron" size={14} /></summary>{Object.entries(PLATFORM_SECTIONS).filter(([id]) => !['overview', 'prompts', 'agents'].includes(id)).map(([id, item]) => <button className={platformView && platformSection === id ? 'active' : ''} key={id} onClick={() => openPlatform(id)}><Icon name={item[1]} size={16} /><span>{item[0]}</span></button>)}</details></nav> : <nav className="mode-navigation" aria-label="工作模式"><div className="quick-modes">{['chat', 'council', 'crewai'].map((id) => <button key={id} className={!platformView && !projectView && mode === id ? 'active' : ''} onClick={() => selectMode(id)} title={MODES[id].description}><Icon name={MODES[id].icon} size={16} /><span><strong>{{ chat: '聊天', council: '评审', crewai: '团队' }[id]}</strong></span>{activeJobs.some((j) => j.mode === id) && <span className="nav-running" title="有任务运行中" />}</button>)}</div><div className="more-modes"><button type="button" className={!['chat', 'council', 'crewai'].includes(mode) ? 'active' : ''} aria-expanded={modeMenu} onClick={() => setModeMenu(!modeMenu)}><Icon name={MODES[mode].icon} size={18} /><span>{!['chat', 'council', 'crewai'].includes(mode) ? MODES[mode].label : '全部协作方式'}</span><Icon name="chevron" size={14} /></button>{modeMenu && <div className="mode-switcher" role="menu" aria-label="全部协作方式">{Object.entries(MODES).map(([id, item]) => <button role="menuitem" key={id} onClick={() => selectMode(id)}><Icon name={item.icon} size={17} /><span><strong>{item.label}</strong><small>{item.description}</small></span></button>)}</div>}</div></nav>}</div>
      <div className="history-heading"><span>最近会话</span><span>{history.length}</span></div>
      <label className="history-search"><Icon name="search" size={15} /><input aria-label="搜索最近会话" value={historyQuery} onChange={(event) => setHistoryQuery(event.target.value)} placeholder="搜索任务或内容" /></label>
      <div className="history-list">{history.length ? history.map((group) => <button key={group.id} className={`history-item ${group.jobs.some((item) => item.id === jobId) ? 'selected' : ''}`} onClick={() => selectJob(group.latest)} title={group.title}><span className="history-title">{group.title}</span><span className="history-meta"><span className={`history-status status-${group.latest.status}`}>{STATUS_LABELS[group.latest.status] || group.latest.status}</span><span>{group.jobs.length} 轮</span><time>{dateLabel(group.latest.created_at)}</time></span><small className="history-mode">{group.modes.map((id) => MODES[id]?.label || id).join(' / ')}</small></button>) : <p className="history-empty">{historyQuery ? '没有找到匹配的会话。' : '会话会自动保存，随时回来继续。'}</p>}</div>
      <div className="sidebar-footer"><button className="theme-toggle" onClick={() => { const next = theme === 'light' ? 'dark' : 'light'; setTheme(next); localStorage.setItem('modeldesk.theme', next); }}><span><Icon name={theme === 'light' ? 'moon' : 'sun'} size={14} />{theme === 'light' ? '切换深色模式' : '切换浅色模式'}</span><small>{theme === 'light' ? '浅色' : '深色'}</small></button><div className="gateway-indicator"><span className={`status-dot ${status?.gateway?.online ? 'online' : 'offline'}`} /><strong>{status ? status.gateway?.online ? '本地网关已连接' : '本地网关未连接' : '正在检查连接'}</strong></div><span>认证信息保留在本机</span><button onClick={() => { setPlatformView(false); setProjectView(false); setAccountPanel(true); }}>查看账户与连接<Icon name="settings" size={14} /></button></div>
    </aside>
    <div className={`workspace-shell ${artifactsOpen && (artifactJob || (!platformView && !projectView && job)) ? 'artifacts-visible' : ''} ${mobileArtifactsOpen ? 'mobile-artifacts-open' : ''}`} style={{ '--artifact-width': `${artifactWidth}px` }}><>{platformView ? <PlatformWorkspace section={platformSection} onSection={openPlatform} status={status} jobs={jobs} models={models} context={context} onContextChange={setContext} onUsePrompt={usePrompt} onUseAgent={useAgent} onRunJob={onPlatformRun} onOpenProjects={beginAddProject} onStartCollaboration={() => { selectMode('crewai'); newTask('crewai'); }} onOpenJob={selectJob} onOpenNavigation={() => setSidebarOpen(true)} /> : projectView ? <ProjectWorkspace key={`${projectTarget?.project_id || 'default'}:${projectTarget?.review_id || 'latest'}`} addRequested={addProjectSignal} initialProjectId={projectTarget?.project_id} initialReviewId={projectTarget?.review_id} onOpenArtifacts={(next) => { artifactRequest.current += 1; setArtifactJob(hydrateJob(next)); setArtifactsOpen(true); setMobileArtifactsOpen(true); }} status={status} jobs={jobs} models={models} onModelsChange={(next) => { setModels(next); localStorage.setItem('modeldesk.models', JSON.stringify(next)); }} onRefresh={refresh} onOpenNavigation={() => setSidebarOpen(true)} /> : <main className="workspace">
      <header className="workspace-header"><div className="header-title"><button className="mobile-menu icon-button" aria-label="打开导航" onClick={() => setSidebarOpen(true)}><Icon name="menu" /></button><span className="mode-header-icon"><Icon name={config.icon} size={22} /></span><div><h1 title={job ? taskTitle(job.prompt) : undefined}>{job ? taskTitle(job.prompt) : '协作工作台'}</h1><span>{job && job.mode !== mode ? '下一轮：' : ''}{config.label} · {config.source}</span></div></div><div className="header-actions"><button className="subtle-button" aria-expanded={settingsOpen} onClick={() => setSettingsOpen(!settingsOpen)}><Icon name="settings" size={16} />设置</button><button disabled={!job && !artifactJob} className={`subtle-button ${artifactsOpen ? 'selected' : ''}`} aria-expanded={artifactsOpen} onClick={toggleArtifacts}><Icon name="layers" size={16} />最终产物</button><button className={`account-toggle ${accountPanel ? 'selected' : ''}`} onClick={() => setAccountPanel(!accountPanel)}><span className={`status-dot ${status?.providers?.some((p) => p.authenticated) ? 'online' : 'offline'}`} />已有账户<Icon name="chevron" size={14} /></button><button className="subtle-button" disabled={!prompt.trim()} onClick={savePrompt}>保存提示</button><div className="export-control"><button className="subtle-button" disabled={!job} onClick={() => setExportMenu(!exportMenu)}><Icon name="export" size={17} />导出</button>{exportMenu && job && <div className="small-menu"><button onClick={() => { exportJob(job, 'md'); setExportMenu(false); }}>导出 Markdown</button><button onClick={() => { exportJob(job, 'json'); setExportMenu(false); }}>导出完整 JSON</button></div>}</div></div></header>
      {accountPanel && <section className="account-panel" aria-label="账户与连接状态"><div className="account-panel-title"><div><strong>账户与连接</strong><span>凭据存在与模型调用验证分别显示</span></div><button className="icon-button" aria-label="刷新连接状态" disabled={refreshing} onClick={manualRefresh}><Icon name="refresh" size={18} /></button><button className="icon-button" aria-label="关闭账户面板" onClick={() => setAccountPanel(false)}><Icon name="close" size={18} /></button></div><div className="provider-list">{status?.providers?.length ? status.providers.map((provider) => <div className="provider-row" key={provider.id}><span className={`status-dot ${provider.authenticated ? 'online' : 'offline'}`} /><strong>{provider.label}</strong><span>{provider.authenticated ? '已发现登录凭据' : '未接入'}</span><span className="provider-verification">{provider.verified_models?.length ? `${provider.verified_models.length} 个模型已验证` : '尚无已验证调用'}</span>{provider.reason && <p>{provider.reason}</p>}</div>) : <p className="result-note">{status?.gateway?.error || '正在读取本地登录状态…'}</p>}</div><p className="account-footnote">Claude 订阅登录未接入第三方网关。当前工作台共享 ChatGPT 与 Google / Antigravity 的已有认证。</p></section>}
      <div className="context-toolbar"><label className="conversation-memory"><input type="checkbox" checked={rememberConversation} disabled={running || starting} onChange={(event) => { setRememberConversation(event.target.checked); localStorage.setItem('modeldesk.conversationMemory', event.target.checked ? 'on' : 'off'); }} /><Icon name="memory" size={16} /><span>{rememberConversation ? '会话记忆已开启' : '本轮不引用历史'}</span></label><span className="conversation-turns">{job ? `${priorTurns.length + 1} 轮记录` : '新会话'}</span><button className="context-source-trigger" onClick={() => setSettingsOpen(!settingsOpen)}><Icon name="folder" size={15} />资料与工具{context.knowledge_ids.length + context.memory_ids.length + (context.snapshot_id ? 1 : 0) > 0 && <b>{context.knowledge_ids.length + context.memory_ids.length + (context.snapshot_id ? 1 : 0)}</b>}</button><button className="current-models" title={models.join(' + ')} onClick={() => { setSettingsOpen(true); setModelMenu(true); }} disabled={running || starting}>{models.length ? `${models.length} 个模型` : '选择模型'}<Icon name="chevron" size={14} /></button></div>
      {settingsOpen && <section className="workbench-settings" aria-label="协作设置"><div className="settings-heading"><strong>本会话设置</strong><span>默认配置即可开始，高级功能按需启用。</span><button className="icon-button" aria-label="收起协作设置" onClick={() => setSettingsOpen(false)}><Icon name="close" size={16} /></button></div>
      <section className="configuration-bar" aria-label="模型与任务设置"><div className="model-control"><span className="control-label">参与模型</span><button className={`model-selector ${modelMenu ? 'open' : ''}`} onClick={() => setModelMenu(!modelMenu)} disabled={running || starting}><span>{models.length ? models.map(modelLabel).join(' + ') : '选择模型'}</span><Icon name="chevron" size={15} /></button>{modelMenu && <div className="model-popover"><div className="model-popover-heading"><strong>选择参与模型</strong><span>{mode === 'camel' ? '选择两个模型，分别承担角色' : `可选择 ${minimum}–4 个模型`}</span></div><div className="model-options">{selectedCatalog.map((model) => <label key={model.id} className={models.includes(model.id) ? 'checked' : ''}><input type="checkbox" checked={models.includes(model.id)} disabled={!models.includes(model.id) && models.length >= 4} onChange={() => toggleModel(model.id)} /><span>{modelLabel(model.id)}</span><small className={model.verified ? 'verified-label' : ''}>{model.verified ? '已验证' : '未验证'}</small></label>)}</div><div className="model-popover-footer">未验证模型的可用性以实际调用为准<button onClick={() => setModelMenu(false)}>完成</button></div></div>}</div>{mode === 'council' && <label className="inline-setting"><span>主席</span><select value={chairman} onChange={(e) => setChairman(e.target.value)} disabled={running}>{selectedCatalog.map((model) => <option value={model.id} key={model.id}>{modelLabel(model.id)}</option>)}</select></label>}{['camel', 'camel_workforce', 'group'].includes(mode) && <label className="inline-setting"><span>讨论轮数</span><select value={rounds} onChange={(e) => setRounds(Number(e.target.value))} disabled={running}>{[1, 2, 3, 4, 5, 6].map((n) => <option key={n} value={n}>{n} 轮</option>)}</select></label>}{roleMode && <span className="role-assignment">{configuration.agents.map((agent) => agent.role || '未命名角色').join(' / ')}，可编辑分工</span>}</section>
      <ContextAttachments mode={mode} value={context} onChange={setContext} jobs={jobs} disabled={running || starting} onOpenProjects={openProjects} />
      <ToolSettings mode={mode} models={models} systemPrompt={systemPrompt} onSystemPrompt={setSystemPrompt} relayPrompt={relayPrompt} onRelayPrompt={setRelayPrompt} roles={roles} onRoles={setRoles} contextTurns={contextTurns} onContextTurns={setContextTurns} disabled={running || starting} />
        <WorkflowEditor openSignal={workflowEditSignal} mode={mode} models={models} workflow={configuration} onChange={changeWorkflow} disabled={running || starting} />
      </section>}
      <div className="work-surface">
        {error && <div role="alert" className="error-banner"><span>{error}</span><button className="icon-button" aria-label="关闭错误提示" onClick={() => setError('')}><Icon name="close" size={15} /></button></div>}
        {status && !status.gateway?.online && <div className="connection-warning"><strong>本地网关未连接</strong><span>请双击 ModelDesk 启动入口，再点击刷新。</span><button className="subtle-button" onClick={manualRefresh} disabled={refreshing}><Icon name="refresh" size={15} />刷新</button></div>}
        {!toolAvailable && <div className="connection-warning"><strong>{config.source} 暂不可用</strong><span>{tool?.reason || '请检查本地依赖与服务。'}</span></div>}
        {priorTurns.length > 0 && <section className="conversation-previous" aria-label="之前的会话">{loadedTurns.map((previous) => <details className="previous-turn" key={previous.id}><summary><Icon name="chat" size={15} /><span title={taskTitle(previous.prompt)}>{taskTitle(previous.prompt)}</span><small>{MODES[previous.mode]?.label || previous.mode}</small><Icon name="chevron" size={14} /></summary><div className="previous-turn-body"><div className="prompt-actions"><CopyTextButton text={previous.prompt} label="复制这条提问" /></div><Markdown>{previous.prompt}</Markdown><button className="subtle-button" onClick={() => { setArtifactJob(previous); setArtifactsOpen(true); setMobileArtifactsOpen(true); }}>查看这轮成果</button><JobOutput job={previous} /></div></details>)}</section>}
        {jobId && !job ? <StagePending text="正在读取保存的记录" running /> : job ? <article className="job-detail"><div className="prompt-heading"><span className="prompt-label">{job.mode === 'chat' ? '你的问题' : '任务要求'}</span><div className={`job-status status-${job.status}`}>{running && <span className="activity-dot" />}{STATUS_LABELS[job.status] || job.status}</div><time>{dateLabel(job.created_at)}</time></div><div className="prompt-text"><Markdown>{job.prompt}</Markdown><div className="prompt-actions"><CopyTextButton text={job.prompt} label="复制提问" /><button className="subtle-button" onClick={() => { setPrompt(job.prompt); inputRef.current?.focus(); }}>编辑再用</button></div></div>{running && <div className="run-progress"><span className="small-spinner" /><span>{lastLog || (mode === 'chat' ? '各模型正在独立回答，可随时停止' : mode === 'council' ? '正在执行独立回答、匿名评审与主席综合' : mode === 'group' ? '群聊模型正在依次阅读记录并发言' : '正在执行角色分工与协作')}</span></div>}{streamError && running && <div className="stream-warning">实时连接暂时中断，正在重连。后台任务继续运行。</div>}<WorkflowFailureNotice job={job} onPrepare={roleMode && !running ? prepareFailedTask : undefined} />{job.status === 'cancelled' && !['camel', 'crewai'].includes(job.mode) && <p className="result-note">任务已停止。已收到的内容保留在记录中。</p>}<JobOutput key={job.id} job={job} conversationView /><ResultProvenance job={job} /></article> : <div className="empty-workspace"><div className="workspace-illustration" aria-hidden="true"><div className="illustration-input"><span /><span /></div><div className="illustration-lines"><i /><i /></div><div className="illustration-responses"><div><b className="illustration-model">O</b><span /><span /><span /></div><div><b className="illustration-model gemini-mark">G</b><span /><span /><span /></div></div></div><h2>{mode === 'chat' ? '一个问题，多种思路' : mode === 'council' ? '让回答接受另一轮审视' : mode === 'camel' ? '把想法放进一场讨论' : mode === 'group' ? '让不同模型接着聊' : '把任务交给一个协作团队'}</h2><p>{config.description}</p><button className="example-prompt" onClick={() => { setPrompt(config.example); inputRef.current?.focus(); }}><span>试一试</span>{config.example}<Icon name="plus" size={17} /></button><div className="empty-flow">{mode === 'chat' ? <><span>统一提问</span><i /><span>同时回答</span><i /><span>并排比较</span></> : mode === 'council' ? <><span>独立回答</span><i /><span>匿名互评</span><i /><span>主席综合</span></> : mode === 'camel' ? <><span>研究员提出</span><i /><span>审稿人质询</span><i /><span>多轮完善</span></> : mode === 'group' ? <><span>共享话题</span><i /><span>依次接话</span><i /><span>多轮讨论</span></> : <><span>明确任务</span><i /><span>起草方案</span><i /><span>审查交付</span></>}</div></div>}
      </div>
      <div className="composer-wrap"><form className="composer" onSubmit={send}><textarea ref={inputRef} aria-label={mode === 'chat' ? '输入问题' : '输入任务要求'} value={prompt} onChange={(e) => setPrompt(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(e); } }} rows={3} placeholder={job && rememberConversation ? '继续当前会话，例如：根据上面的结果，再完善一下…' : '描述你希望团队完成的任务…'} disabled={running || starting} /><div className="composer-footer"><span>{job && rememberConversation ? '接续当前会话 · Enter 发送，Shift + Enter 换行' : 'Enter 发送，Shift + Enter 换行'}{!selectionValid && <strong>{configurationErrors[0] || (mode === 'camel' ? '请选择恰好两个模型' : `请至少选择 ${minimum} 个模型`)}</strong>}</span>{running ? <button className="stop-button" type="button" disabled={canceling} onClick={cancel}><Icon name="stop" size={16} />{canceling ? '正在停止' : '停止运行'}</button> : <button className="primary-button" type="submit" disabled={!canSubmit}><Icon name="send" size={16} />{starting ? '正在启动' : config.action}</button>}</div></form><div className="composer-note"><span>会话和草稿自动保存在本机</span><button type="button" onClick={newTask} disabled={running || starting}>新建独立会话</button><button type="button" disabled={!prompt} onClick={savePrompt}>存为提示</button><button type="button" disabled={!prompt} onClick={() => setPrompt('')}>清空输入</button></div></div>
    </main>}</>{artifactsOpen && (artifactJob || (!platformView && !projectView && job)) && <div className="artifact-dock"><div className="artifact-resizer" role="separator" aria-label="调整成果面板宽度" aria-orientation="vertical" aria-valuemin={320} aria-valuemax={Math.max(320, Math.min(680, window.innerWidth - 744))} aria-valuenow={artifactWidth} tabIndex={0} onPointerDown={resizeArtifacts} onKeyDown={(event) => { if (['ArrowLeft', 'ArrowRight'].includes(event.key)) { event.preventDefault(); const next = Math.max(320, Math.min(Math.max(320, Math.min(680, window.innerWidth - 744)), artifactWidth + (event.key === 'ArrowLeft' ? 24 : -24))); setArtifactWidth(next); localStorage.setItem('modeldesk.artifactWidth', String(next)); } }} /><ArtifactPanel job={artifactJob || job} onClose={toggleArtifacts} /></div>}</div>
  </div>;
}
