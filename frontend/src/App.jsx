import { useCallback, useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { workbenchApi } from './api';
import { applyEvent, hydrateJob } from './workbench-state';
import './App.css';

const MODES = {
  chat: { label: '多模型聊天', source: 'ChatHub', description: '同一个问题，同时听取不同模型的回答。', example: '比较两种科研论文阅读方法：先读摘要与结论，或先读方法与结果。给出各自适用场景。', action: '同时发送', icon: 'chat' },
  council: { label: '匿名评审', source: 'LLM Council', description: '独立回答、匿名互评，再由主席综合结论。', example: '我有 30 分钟阅读一篇科研论文。请设计阅读与核查流程，分配时间，并说明可能遗漏的证据。', action: '开始评审', icon: 'council' },
  camel: { label: '角色对话', source: 'CAMEL', description: '研究员与审稿人轮流讨论，逐步完善方案。', example: '讨论如何评估多动物跟踪中的身份连续性，区分技术诊断与可靠的实验验证。', action: '开始对话', icon: 'dialogue' },
  crewai: { label: '任务协作', source: 'CrewAI', description: '先起草，再审查，按分工完成一项任务。', example: '制定一份科研论文阅读与核查清单，覆盖方法、数据、统计与结论的证据边界。', action: '开始协作', icon: 'tasks' },
};
const STATUS_LABELS = { queued: '等待开始', running: '运行中', complete: '已完成', completed: '已完成', failed: '失败', error: '失败', cancelled: '已停止', interrupted: '已中断' };
const active = (job) => ['queued', 'running'].includes(job?.status);
const modelLabel = (model = '') => model.replace(/^openai\//, '');
const asText = (value) => typeof value === 'string' ? value : value?.content || value?.response || '';

function Icon({ name, size = 20 }) {
  const paths = {
    chat: <><path d="M4 5h16v11H9l-5 4V5Z" /><path d="M8 9h8M8 12h5" /></>,
    council: <><path d="m12 3 8 4v5c0 5-8 9-8 9s-8-4-8-9V7l8-4Z" /><path d="m8 12 3 3 5-6" /></>,
    dialogue: <><path d="M3 4h13v9H8l-5 3V4Z" /><path d="M9 16v2h7l5 3V10h-3" /></>,
    tasks: <><rect x="4" y="3" width="16" height="18" rx="2" /><path d="m7 8 1 1 2-2M12 8h5m-10 5 1 1 2-2m2 1h5M7 18h10" /></>,
    plus: <path d="M12 5v14M5 12h14" />,
    send: <><path d="m4 4 17 8-17 8 4-8-4-8Z" /><path d="M8 12h13" /></>,
    stop: <rect x="6" y="6" width="12" height="12" rx="1" />,
    export: <><path d="M12 3v12m-4-4 4 4 4-4M4 15v5h16v-5" /></>,
    chevron: <path d="m8 10 4 4 4-4" />,
    settings: <><path d="M4 7h16M4 17h16" /><circle cx="9" cy="7" r="3" /><circle cx="15" cy="17" r="3" /></>,
    refresh: <><path d="M20 7v5h-5M4 17v-5h5" /><path d="M6 7a7 7 0 0 1 12-2l2 2M18 17A7 7 0 0 1 6 19l-2-2" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    close: <path d="m6 6 12 12M6 18 18 6" />,
    clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
    menu: <path d="M4 6h16M4 12h16M4 18h16" />,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] || paths.chat}</svg>;
}

function Markdown({ children }) {
  return <div className="markdown"><ReactMarkdown>{asText(children)}</ReactMarkdown></div>;
}

function ResponsePane({ model, text, error, waiting, index }) {
  return <section className="response-pane">
    <header className="pane-header"><span className={`model-mark model-mark-${index % 3}`}>{model.toLowerCase().includes('gemini') ? 'G' : 'O'}</span><div><strong>{modelLabel(model)}</strong><span>{error ? '调用失败' : waiting ? text ? '正在生成' : '等待回答' : '模型回答'}</span></div>{waiting && <span className="activity-dot" />}</header>
    <div className="pane-body">{error && <div className="output-error">{error}{text && <span> 已收到的部分回答保留如下。</span>}</div>}{text ? <Markdown>{text}</Markdown> : !error && <div className="pane-placeholder">{waiting ? '模型正在组织回答…' : '暂无回答'}</div>}</div>
  </section>;
}

function StagePending({ text, running }) {
  return <div className="stage-pending">{running ? <span className="small-spinner" /> : <Icon name="clock" size={22} />}<span>{text}</span></div>;
}

function WorkerPending({ job, text }) {
  if (active(job)) return <StagePending text={text} running />;
  const terminal = { cancelled: '任务已停止', interrupted: '任务已中断', failed: '任务失败', error: '任务失败' };
  const logs = (job.events || []).filter((event) => event.type === 'log' && event.text);
  return <div className="worker-incomplete"><p className="result-note">{terminal[job.status] || '任务已结束'}，运行记录已保存；完整结果未生成。</p><details className="review-item execution-record"><summary>运行记录<span>{logs.length} 条</span><Icon name="chevron" size={16} /></summary>{logs.length ? <pre className="execution-log">{logs.map((event) => event.text).join('\n')}</pre> : <p className="result-note execution-log-empty">任务信息已保存，本次运行尚未产生输出记录。</p>}</details></div>;
}

function CouncilResult({ result, running }) {
  const [tab, setTab] = useState('synthesis');
  const responses = result?.stage1 || [];
  const rankings = result?.stage2 || [];
  const synthesis = result?.stage3;
  const currentStage = synthesis ? 3 : rankings.length ? 2 : responses.length ? 1 : 0;
  return <div className="council-output">
    <div className="stage-navigation" aria-label="评审阶段">{[['responses', '独立回答', 1], ['rankings', '匿名互评', 2], ['synthesis', '主席结论', 3]].map(([id, label, number]) => <button key={id} className={tab === id ? 'selected' : ''} onClick={() => setTab(id)}><span className={currentStage >= number ? 'stage-number done' : 'stage-number'}>{currentStage >= number ? <Icon name="check" size={13} /> : number}</span>{label}</button>)}</div>
    {tab === 'responses' && <div className="comparison-grid">{responses.length ? responses.map((r, i) => <ResponsePane key={r.model} model={r.model} text={r.response} error={r.error} index={i} />) : <StagePending text="等待各模型独立回答" running={running} />}</div>}
    {tab === 'rankings' && <div className="review-output"><p className="result-note">评审时使用匿名标签。展开每个评审模型，查看原始评价与排序。</p>{rankings.length ? rankings.map((r) => <details className="review-item" key={r.model} open={rankings.length === 1}><summary>{modelLabel(r.model)}<span>查看评审</span><Icon name="chevron" size={16} /></summary><Markdown>{r.ranking}</Markdown>{r.parsed_ranking?.length > 0 && <div className="ranking-order">{r.parsed_ranking.map((label, i) => <span key={`${label}-${i}`}>{i + 1}. {result?.metadata?.label_to_model?.[label] || label}</span>)}</div>}</details>) : <StagePending text="独立回答完成后，模型将匿名互评" running={running} />}{result?.metadata?.aggregate_rankings?.length > 0 && <div className="aggregate-ranking"><strong>综合排序</strong>{result.metadata.aggregate_rankings.map((r, i) => <div key={r.model}><span>{i + 1}. {modelLabel(r.model)}</span><span>平均名次 {Number(r.average_rank).toFixed(2)}</span></div>)}</div>}</div>}
    {tab === 'synthesis' && (synthesis ? <section className="final-answer"><header><span className="final-label">综合结论</span><span>主席：{modelLabel(synthesis.model)}</span></header><Markdown>{synthesis.response}</Markdown><p className="result-note">模型间的一致意见仍需外部证据核查。</p></section> : <StagePending text={currentStage === 2 ? '主席正在综合回答与评审结果' : currentStage === 1 ? '已收到独立回答，正在匿名互评' : '完整评审结束后，主席结论将在这里显示'} running={running} />)}
  </div>;
}

function JobOutput({ job }) {
  const result = job.result;
  if (job.mode === 'chat') {
    const replies = result?.responses || [];
    return <div className="comparison-grid">{job.models.map((model, i) => { const reply = replies.find((r) => r.model === model); return <ResponsePane key={model} model={model} text={reply?.text || reply?.response} error={reply?.error} waiting={active(job) && !reply?.done} index={i} />; })}</div>;
  }
  if (job.mode === 'council') return <CouncilResult result={result} running={active(job)} />;
  if (job.mode === 'camel') return <div className="role-transcript">{result?.messages?.length ? result.messages.map((m, i) => { const reviewer = /review|审/.test(m.role || ''); return <section key={i} className={`role-message role-${reviewer ? 1 : 0}`}><div className="role-line"><span className="role-avatar">{reviewer ? '审' : '研'}</span><strong>{reviewer ? '审稿人' : '研究员'}</strong>{m.model && <span>{modelLabel(m.model)}</span>}</div><Markdown>{m.content}</Markdown></section>; }) : <WorkerPending job={job} text="研究员与审稿人正在建立讨论" />}</div>;
  const tasks = result?.tasks || [];
  return <div className="crew-output">{tasks.length > 0 && <div className="task-results">{tasks.map((task, i) => <details className="review-item" key={i}><summary><span className="task-number">{i + 1}</span>{task.name || task.agent || task.description || `协作任务 ${i + 1}`}<Icon name="chevron" size={16} /></summary><Markdown>{task.output || task.result || task.raw || task}</Markdown></details>)}</div>}{result?.answer ? <section className="final-answer"><header><span className="final-label">协作结果</span><span>起草与审查已完成</span></header><Markdown>{result.answer}</Markdown></section> : <WorkerPending job={job} text="团队正在起草与审查任务结果" />}</div>;
}

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
  const [status, setStatus] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [jobId, setJobId] = useState(() => localStorage.getItem('modeldesk.lastJob') || null);
  const [job, setJob] = useState(null);
  const [models, setModels] = useState([]);
  const [chairman, setChairman] = useState('gpt-6.1-sol');
  const [rounds, setRounds] = useState(2);
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
      setJob(hydrateJob(loaded)); setMode(loaded.mode); setStreamError(false); localStorage.setItem('modeldesk.lastJob', jobId);
      preferencesSet.current = true;
      setModels(loaded.models); localStorage.setItem('modeldesk.models', JSON.stringify(loaded.models));
      if (loaded.chairman) setChairman(loaded.chairman);
      if (loaded.rounds) setRounds(loaded.rounds);
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

  const selectMode = (next) => { setMode(next); setJobId(null); setJob(null); setError(''); setStreamError(false); setSidebarOpen(false); setModelMenu(false); setPrompt(''); localStorage.removeItem('modeldesk.lastJob'); };
  const selectJob = (item) => { setSidebarOpen(false); if (item.id === jobId) return; setMode(item.mode); setJob(null); setJobId(item.id); setPrompt(''); setError(''); };
  const newTask = () => { setJobId(null); setJob(null); setPrompt(''); setError(''); setStreamError(false); localStorage.removeItem('modeldesk.lastJob'); inputRef.current?.focus(); };
  const toggleModel = (id) => { const next = models.includes(id) ? models.filter((m) => m !== id) : models.length < 4 ? [...models, id] : models; setModels(next); localStorage.setItem('modeldesk.models', JSON.stringify(next)); };
  const tool = status?.tools?.[mode];
  const toolAvailable = typeof tool === 'boolean' ? tool : tool?.available !== false;
  const minimum = mode === 'chat' ? 1 : 2;
  const roleMode = ['camel', 'crewai'].includes(mode);
  const selectionValid = models.length >= minimum && (!roleMode || models.length === 2);
  const running = active(job);
  const canSubmit = Boolean(prompt.trim() && !starting && !running && status?.gateway?.online && toolAvailable && selectionValid);
  const send = async (event) => {
    event?.preventDefault(); if (!canSubmit) return;
    setStarting(true); setError(''); setStreamError(false); setModelMenu(false);
    try { const created = await workbenchApi.createJob({ mode, prompt: prompt.trim(), models, chairman: mode === 'council' ? chairman : undefined, rounds, conversation_id: mode === 'chat' && job?.mode === 'chat' ? job.conversation_id : undefined }); setJob(created); setJobId(created.id); setPrompt(''); setJobs((previous) => [created, ...previous.filter((j) => j.id !== created.id)]); }
    catch (failure) { setError(failure.message); } finally { setStarting(false); }
  };
  const cancel = async () => { if (!job || canceling) return; setCanceling(true); try { const stopped = await workbenchApi.cancelJob(job.id); setJob(hydrateJob(stopped)); streamRef.current?.close(); setStreamError(false); refresh(); } catch (failure) { setError(failure.message); } finally { setCanceling(false); } };
  const manualRefresh = async () => { setRefreshing(true); await refresh(); setRefreshing(false); };
  const history = jobs.filter((j) => j.mode === mode);
  const activeJobs = jobs.filter(active);
  const config = MODES[mode];
  const lastLog = [...(job?.events || [])].reverse().find((e) => e.type === 'log')?.text;
  const selectedCatalog = status?.models || [];

  return <div className="modeldesk">
    {sidebarOpen && <button className="sidebar-backdrop" aria-label="关闭导航" onClick={() => setSidebarOpen(false)} />}
    <aside className={`sidebar ${sidebarOpen ? 'sidebar-open' : ''}`}>
      <div className="brand"><span className="brand-symbol"><i /><i /><i /><i /></span><div><strong>ModelDesk</strong><span>多模型协作工作台</span></div></div>
      <button className="new-task" onClick={newTask}><Icon name="plus" size={18} />新建{mode === 'chat' ? '聊天' : '任务'}</button>
      <nav className="mode-navigation" aria-label="工作模式">{Object.entries(MODES).map(([id, item]) => <button key={id} className={mode === id ? 'active' : ''} onClick={() => selectMode(id)}><Icon name={item.icon} size={20} /><span><strong>{item.label}</strong><small>{item.source}</small></span>{activeJobs.some((j) => j.mode === id) && <span className="nav-running" title="有任务运行中" />}</button>)}</nav>
      <div className="history-heading"><span>最近记录</span><span>{history.length}</span></div>
      <div className="history-list">{history.length ? history.map((item) => <button key={item.id} className={`history-item ${jobId === item.id ? 'selected' : ''}`} onClick={() => selectJob(item)}><span className="history-title">{item.prompt || '未命名任务'}</span><span className="history-meta"><span className={`history-status status-${item.status}`}>{STATUS_LABELS[item.status] || item.status}</span><time>{dateLabel(item.created_at)}</time></span></button>) : <p className="history-empty">首次运行后，记录会自动保存在这里。</p>}</div>
      <div className="sidebar-footer"><div className="gateway-indicator"><span className={`status-dot ${status?.gateway?.online ? 'online' : 'offline'}`} /><strong>{status ? status.gateway?.online ? '本地网关已连接' : '本地网关未连接' : '正在检查连接'}</strong></div><span>认证信息保留在本机</span><button onClick={() => setAccountPanel(true)}>查看账户与连接<Icon name="settings" size={14} /></button></div>
    </aside>
    <main className="workspace">
      <header className="workspace-header"><div className="header-title"><button className="mobile-menu icon-button" aria-label="打开导航" onClick={() => setSidebarOpen(true)}><Icon name="menu" /></button><span className="mode-header-icon"><Icon name={config.icon} size={22} /></span><div><h1>{config.label}</h1><span>{config.source}</span></div></div><div className="header-actions"><button className={`account-toggle ${accountPanel ? 'selected' : ''}`} onClick={() => setAccountPanel(!accountPanel)}><span className={`status-dot ${status?.providers?.some((p) => p.authenticated) ? 'online' : 'offline'}`} />已有账户<Icon name="chevron" size={14} /></button><div className="export-control"><button className="subtle-button" disabled={!job} onClick={() => setExportMenu(!exportMenu)}><Icon name="export" size={17} />导出</button>{exportMenu && job && <div className="small-menu"><button onClick={() => { exportJob(job, 'md'); setExportMenu(false); }}>导出 Markdown</button><button onClick={() => { exportJob(job, 'json'); setExportMenu(false); }}>导出完整 JSON</button></div>}</div></div></header>
      {accountPanel && <section className="account-panel" aria-label="账户与连接状态"><div className="account-panel-title"><div><strong>账户与连接</strong><span>凭据存在与模型调用验证分别显示</span></div><button className="icon-button" aria-label="刷新连接状态" disabled={refreshing} onClick={manualRefresh}><Icon name="refresh" size={18} /></button><button className="icon-button" aria-label="关闭账户面板" onClick={() => setAccountPanel(false)}><Icon name="close" size={18} /></button></div><div className="provider-list">{status?.providers?.length ? status.providers.map((provider) => <div className="provider-row" key={provider.id}><span className={`status-dot ${provider.authenticated ? 'online' : 'offline'}`} /><strong>{provider.label}</strong><span>{provider.authenticated ? '已发现登录凭据' : '未接入'}</span><span className="provider-verification">{provider.verified_models?.length ? `${provider.verified_models.length} 个模型已验证` : '尚无已验证调用'}</span>{provider.reason && <p>{provider.reason}</p>}</div>) : <p className="result-note">{status?.gateway?.error || '正在读取本地登录状态…'}</p>}</div><p className="account-footnote">Claude 订阅登录未接入第三方网关。当前工作台共享 ChatGPT 与 Google / Antigravity 的已有认证。</p></section>}
      <section className="configuration-bar" aria-label="模型与任务设置"><div className="model-control"><span className="control-label">参与模型</span><button className={`model-selector ${modelMenu ? 'open' : ''}`} onClick={() => setModelMenu(!modelMenu)} disabled={running || starting}><span>{models.length ? models.map(modelLabel).join(' + ') : '选择模型'}</span><Icon name="chevron" size={15} /></button>{modelMenu && <div className="model-popover"><div className="model-popover-heading"><strong>选择参与模型</strong><span>{roleMode ? '选择两个模型，分别承担角色' : `可选择 ${minimum}–4 个模型`}</span></div><div className="model-options">{selectedCatalog.map((model) => <label key={model.id} className={models.includes(model.id) ? 'checked' : ''}><input type="checkbox" checked={models.includes(model.id)} disabled={!models.includes(model.id) && models.length >= 4} onChange={() => toggleModel(model.id)} /><span>{modelLabel(model.id)}</span><small className={model.verified ? 'verified-label' : ''}>{model.verified ? '已验证' : '未验证'}</small></label>)}</div><div className="model-popover-footer">未验证模型的可用性以实际调用为准<button onClick={() => setModelMenu(false)}>完成</button></div></div>}</div>{mode === 'council' && <label className="inline-setting"><span>主席</span><select value={chairman} onChange={(e) => setChairman(e.target.value)} disabled={running}>{selectedCatalog.map((model) => <option value={model.id} key={model.id}>{modelLabel(model.id)}</option>)}</select></label>}{mode === 'camel' && <label className="inline-setting"><span>讨论轮数</span><select value={rounds} onChange={(e) => setRounds(Number(e.target.value))} disabled={running}>{[1, 2, 3, 4, 5, 6].map((n) => <option key={n} value={n}>{n} 轮</option>)}</select></label>}{roleMode && models.length === 2 && <span className="role-assignment">{mode === 'camel' ? '研究员 / 审稿人' : '起草 / 审查'}，按所选顺序分工</span>}</section>
      <div className="work-surface">
        {error && <div role="alert" className="error-banner"><span>{error}</span><button className="icon-button" aria-label="关闭错误提示" onClick={() => setError('')}><Icon name="close" size={15} /></button></div>}
        {status && !status.gateway?.online && <div className="connection-warning"><strong>本地网关未连接</strong><span>请双击 ModelDesk 启动入口，再点击刷新。</span><button className="subtle-button" onClick={manualRefresh} disabled={refreshing}><Icon name="refresh" size={15} />刷新</button></div>}
        {!toolAvailable && <div className="connection-warning"><strong>{config.source} 暂不可用</strong><span>{tool?.reason || '请检查本地依赖与服务。'}</span></div>}
        {jobId && !job ? <StagePending text="正在读取保存的记录" running /> : job ? <article className="job-detail"><div className="prompt-heading"><span className="prompt-label">{job.mode === 'chat' ? '你的问题' : '任务要求'}</span><div className={`job-status status-${job.status}`}>{running && <span className="activity-dot" />}{STATUS_LABELS[job.status] || job.status}</div><time>{dateLabel(job.created_at)}</time></div><div className="prompt-text"><Markdown>{job.prompt}</Markdown></div>{running && <div className="run-progress"><span className="small-spinner" /><span>{lastLog || (mode === 'chat' ? '各模型正在独立回答，可随时停止' : mode === 'council' ? '正在执行独立回答、匿名评审与主席综合' : '正在执行角色分工与协作')}</span></div>}{streamError && running && <div className="stream-warning">实时连接暂时中断，正在重连。后台任务继续运行。</div>}{job.error && <div className="output-error" role="alert">{job.error}</div>}{job.status === 'cancelled' && !['camel', 'crewai'].includes(job.mode) && <p className="result-note">任务已停止。已收到的内容保留在记录中。</p>}<JobOutput key={job.id} job={job} /></article> : <div className="empty-workspace"><div className="workspace-illustration" aria-hidden="true"><div className="illustration-input"><span /><span /></div><div className="illustration-lines"><i /><i /></div><div className="illustration-responses"><div><b className="illustration-model">O</b><span /><span /><span /></div><div><b className="illustration-model gemini-mark">G</b><span /><span /><span /></div></div></div><h2>{mode === 'chat' ? '一个问题，多种思路' : mode === 'council' ? '让回答接受另一轮审视' : mode === 'camel' ? '把想法放进一场讨论' : '把任务交给一个协作团队'}</h2><p>{config.description}</p><button className="example-prompt" onClick={() => { setPrompt(config.example); inputRef.current?.focus(); }}><span>试一试</span>{config.example}<Icon name="plus" size={17} /></button><div className="empty-flow">{mode === 'chat' ? <><span>统一提问</span><i /><span>同时回答</span><i /><span>并排比较</span></> : mode === 'council' ? <><span>独立回答</span><i /><span>匿名互评</span><i /><span>主席综合</span></> : mode === 'camel' ? <><span>研究员提出</span><i /><span>审稿人质询</span><i /><span>多轮完善</span></> : <><span>明确任务</span><i /><span>起草方案</span><i /><span>审查交付</span></>}</div></div>}
      </div>
      <div className="composer-wrap"><form className="composer" onSubmit={send}><textarea ref={inputRef} aria-label={mode === 'chat' ? '输入问题' : '输入任务要求'} value={prompt} onChange={(e) => setPrompt(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(e); } }} rows={3} placeholder={mode === 'chat' && job ? '继续提问，保留这段聊天的上下文…' : '写下你的问题或任务要求…'} disabled={running || starting} /><div className="composer-footer"><span>{mode === 'chat' && job ? '继续当前聊天' : 'Enter 发送，Shift + Enter 换行'}{!selectionValid && <strong>{roleMode ? '请选择恰好两个模型' : `请至少选择 ${minimum} 个模型`}</strong>}</span>{running ? <button className="stop-button" type="button" disabled={canceling} onClick={cancel}><Icon name="stop" size={16} />{canceling ? '正在停止' : '停止运行'}</button> : <button className="primary-button" type="submit" disabled={!canSubmit}><Icon name="send" size={16} />{starting ? '正在启动' : config.action}</button>}</div></form><div className="composer-note"><span>使用已有登录认证</span><span>结果与记录自动保存在本机</span></div></div>
    </main>
  </div>;
}
