import { useCallback, useEffect, useRef, useState } from 'react';
import { workbenchApi } from '../api';
import { applyEvent, hydrateJob } from '../workbench-state';
import { Icon, Markdown, JobOutput, StagePending } from './WorkbenchResults';
import './ProjectWorkspace.css';

const SCOPES = {
  working: ['工作区改动', '读取实际已暂存、未暂存与未跟踪代码改动。'],
  staged: ['已暂存改动', '只检查即将进入提交的代码。'],
  last_commit: ['最近一次提交', '比较最近一次提交与其父提交。'],
  branch: ['分支差异', '比较当前分支与指定基准。'],
  selected: ['指定文件', '选择需要审查的文件，适用于 Git 与普通文件夹。'],
};
const ENGINES = { chat: '独立审查', council: '匿名互评', camel: '角色讨论', crewai: '分工审查' };
const JOB_STATUS = { queued: '等待开始', running: '审查中', complete: '已完成', completed: '已完成', error: '失败', failed: '失败', cancelled: '已停止', interrupted: '已中断' };
const isActive = (job) => ['queued', 'running'].includes(job?.status);
const formatDate = (date) => date ? new Date(date).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '';
const readable = (value) => typeof value === 'string' ? value : value == null ? '' : JSON.stringify(value, null, 2);

function ReviewModels({ catalog, models, onChange, disabled }) {
  const [filter, setFilter] = useState('');
  const primary = catalog.filter((model) => model.verified || models.includes(model.id));
  const more = catalog.filter((model) => !model.verified && !models.includes(model.id));
  const option = (model) => <label key={model.id} className={models.includes(model.id) ? 'selected' : ''}><input type="checkbox" checked={models.includes(model.id)} disabled={disabled || (!models.includes(model.id) && models.length >= 4)} onChange={() => onChange(model.id)} /><span>{model.id}</span><small>{model.verified ? '已验证' : '未验证'}</small></label>;
  return <div className="review-model-chooser"><strong>参与模型</strong><div className="primary-review-models">{primary.map(option)}</div>{more.length > 0 && <details className="more-review-models"><summary>更多网关模型（未验证）<span>{more.length}</span></summary><input className="review-model-filter" aria-label="筛选更多网关模型" value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="按模型名称筛选…" /><div className="additional-review-models">{more.filter((model) => model.id.toLowerCase().includes(filter.toLowerCase())).map(option)}</div></details>}</div>;
}

function download(job, format) {
  const link = document.createElement('a');
  link.href = workbenchApi.exportUrl(job.id, format); link.download = `ModelDesk-review-${job.id}.${format}`; link.hidden = true;
  document.body.appendChild(link); link.click(); link.remove();
}

function CodeExcerpt({ segment, label, focus }) {
  if (!segment?.text) return null;
  return <div className="code-excerpt"><div className="code-excerpt-label">{label}<span>第 {segment.start_line}–{segment.end_line} 行{segment.total_lines && ` / 文件共 ${segment.total_lines} 行`}</span></div><pre>{segment.text.split('\n').slice(0, segment.end_line - segment.start_line + 1).map((line, index) => { const number = segment.start_line + index; return <div key={index} className={focus && number >= focus.line_start && number <= focus.line_end ? 'code-line highlighted' : 'code-line'}><span>{number}</span><code>{line || ' '}</code></div>; })}</pre></div>;
}

function SnapshotManifest({ snapshot, focus }) {
  if (!snapshot) return null;
  const coverage = snapshot.coverage || {};
  return <section className="snapshot-manifest"><header><div><strong>实际发送的代码上下文</strong><span>{SCOPES[snapshot.scope]?.[0] || snapshot.scope}</span></div><span className="snapshot-fingerprint">快照 {snapshot.id.slice(0, 8)}{snapshot.head && ` · ${snapshot.head.slice(0, 7)}`}</span></header><p className="coverage-description">{coverage.description || '以下文件片段与行号构成本次审查的实际输入。'}{coverage.disclaimer && <span>{coverage.disclaimer}</span>}</p><div className="coverage-summary"><span><strong>{snapshot.files?.length || 0}</strong> 文件已纳入</span><span><strong>{snapshot.omitted?.length || 0}</strong> 文件已排除</span><span><strong>{snapshot.truncated?.length || 0}</strong> 处内容截断</span>{coverage.complete === false && <span className="coverage-limited">覆盖范围有限</span>}</div><div className="snapshot-files">{(snapshot.files || []).map((file) => <details className="snapshot-file" key={file.path} open={focus?.file === file.path ? true : undefined}><summary><Icon name="code" size={15} /><strong>{file.path}</strong><span>{file.status || '代码'}</span><Icon name="chevron" size={15} /></summary><CodeExcerpt segment={file.current} label="当前文件" focus={focus?.file === file.path && focus.side === 'current' ? focus : null} /><CodeExcerpt segment={file.base} label="基准文件" focus={focus?.file === file.path && focus.side === 'base' ? focus : null} /></details>)}</div>{snapshot.omitted?.length > 0 && <details className="manifest-exclusions"><summary>排除清单（{snapshot.omitted.length}）</summary>{snapshot.omitted.map((item, index) => <div key={index}><span>{item.path}</span><small>{item.reason}</small></div>)}</details>}{snapshot.truncated?.length > 0 && <details className="manifest-exclusions"><summary>截断清单（{snapshot.truncated.length}）</summary>{snapshot.truncated.map((item, index) => <div key={index}><span>{item.path}</span><small>{item.reason}</small></div>)}</details>}<p className="snapshot-digest">内容指纹 {snapshot.digest}</p></section>;
}

function ReviewResult({ job, snapshot, resultTab, onResultTab, onFocusFinding, focus }) {
  if (!job) return null;
  const result = job.result || {};
  const findings = result.findings || [];
  const unconfirmed = result.unconfirmed || [];
  const raw = result.raw || (!result.findings ? result : null);
  const logs = (job.events || []).filter((event) => event.type === 'log' && event.text);
  return <div className="review-results"><div className="review-result-tabs"><button className={resultTab === 'findings' ? 'selected' : ''} onClick={() => onResultTab('findings')}>问题与证据{findings.length > 0 && <span>{findings.length}</span>}</button><button className={resultTab === 'context' ? 'selected' : ''} onClick={() => onResultTab('context')}>审查上下文</button><button className={resultTab === 'raw' ? 'selected' : ''} onClick={() => onResultTab('raw')}>原始模型评审</button></div>{resultTab === 'context' && <SnapshotManifest snapshot={snapshot} focus={focus} />}{resultTab === 'raw' && <><p className="result-note">保留原始模型意见与协作阶段，便于复核结论来源。</p>{raw && Object.keys(raw).length > 0 ? <JobOutput job={{ ...job, mode: job.engine, result: raw }} /> : <StagePending text={isActive(job) ? '原始评审正在生成' : '本次运行尚未产生完整模型结果'} running={isActive(job)} />}{logs.length > 0 && <details className="review-item execution-record"><summary>运行记录<span>{logs.length} 条</span><Icon name="chevron" size={16} /></summary><pre className="execution-log">{logs.map((event) => event.text).join('\n')}</pre></details>}</>}{resultTab === 'findings' && <><p className="review-evidence-note">位置与引用范围经过实际代码快照校验。模型的风险判断仍需人工复核。</p>{isActive(job) && <StagePending text="正在检查实际代码，完成后将显示问题与证据" running />}{!isActive(job) && !['complete', 'completed'].includes(job.status) && <div className="connection-warning"><strong>{JOB_STATUS[job.status] || '任务未完成'}</strong><span>运行记录已保存，完整代码评审未生成。</span></div>}{!isActive(job) && ['complete', 'completed'].includes(job.status) && findings.length === 0 && <div className="review-no-findings"><Icon name="check" size={22} /><strong>本次未得到位置可核验的问题</strong><p>这不代表项目没有缺陷。请结合覆盖范围、未确认意见与原始评审判断。</p></div>}{findings.map((finding, index) => <article className="code-finding" key={`${finding.file}-${finding.line_start}-${index}`}><header><span className={`finding-severity severity-${finding.severity?.toLowerCase()}`}>{finding.severity}</span><h3>{finding.title}</h3></header><button className="finding-location" onClick={() => { onFocusFinding(finding); onResultTab('context'); }}><Icon name="code" size={14} />{finding.file}:{finding.line_start}{finding.line_end !== finding.line_start && `–${finding.line_end}`}<span>{finding.side === 'base' ? '基准代码' : '当前代码'}</span></button><Markdown>{finding.body}</Markdown>{finding.evidence && <div className="finding-evidence"><strong>代码证据</strong><pre>{readable(finding.evidence)}</pre></div>}{finding.suggestion && <div className="finding-suggestion"><strong>建议修复</strong><Markdown>{readable(finding.suggestion)}</Markdown></div>}<footer><Icon name="check" size={13} /><span>定位已校验</span><span>来源：{(finding.models || []).join('、') || '模型评审'}</span></footer></article>)}{unconfirmed.length > 0 && <details className="unconfirmed-reviews"><summary>未确认的模型意见（{unconfirmed.length}）<span>不会作为已定位问题</span></summary>{unconfirmed.map((item, index) => <section key={index}><header><strong>{item.model}</strong><span>{item.reason}</span></header><Markdown>{readable(item.claim)}</Markdown></section>)}</details>}{result.coverage && <p className="review-coverage-note">{result.coverage.disclaimer || result.coverage.description || '评审仅覆盖已发送的文件片段。'}</p>}</>}</div>;
}

export default function ProjectWorkspace({ status, jobs, models, onModelsChange, onRefresh, onOpenNavigation }) {
  const [projects, setProjects] = useState([]);
  const [projectId, setProjectId] = useState(() => localStorage.getItem('modeldesk.project') || null);
  const [project, setProject] = useState(null);
  const [loadingProject, setLoadingProject] = useState(false);
  const [scope, setScope] = useState('working');
  const [baseRef, setBaseRef] = useState('');
  const [files, setFiles] = useState([]);
  const [fileSearch, setFileSearch] = useState('');
  const [snapshot, setSnapshot] = useState(null);
  const [previewing, setPreviewing] = useState(false);
  const [engine, setEngine] = useState('council');
  const [chairman, setChairman] = useState('gpt-6.1-sol');
  const [rounds, setRounds] = useState(2);
  const [prompt, setPrompt] = useState('');
  const [reviewId, setReviewId] = useState(() => localStorage.getItem('modeldesk.reviewJob') || null);
  const [job, setJob] = useState(null);
  const [starting, setStarting] = useState(false);
  const [canceling, setCanceling] = useState(false);
  const [view, setView] = useState('setup');
  const [resultTab, setResultTab] = useState('findings');
  const [focus, setFocus] = useState(null);
  const [error, setError] = useState('');
  const [streamError, setStreamError] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [editingProject, setEditingProject] = useState(null);
  const [unregisterOpen, setUnregisterOpen] = useState(false);
  const [projectNotice, setProjectNotice] = useState('');
  const [addName, setAddName] = useState('');
  const [addPath, setAddPath] = useState('');
  const [adding, setAdding] = useState(false);
  const [choosing, setChoosing] = useState(false);
  const streamRef = useRef(null);
  const modelHandler = useRef(onModelsChange);
  modelHandler.current = onModelsChange;
  const running = isActive(job);
  const currentJobs = jobs.filter((item) => item.mode === 'review' && item.project_id === projectId);
  const catalog = project?.files || [];
  const visibleFiles = catalog.filter((file) => file.path.toLowerCase().includes(fileSearch.toLowerCase()));
  const engineTool = status?.tools?.[engine];
  const engineAvailable = typeof engineTool === 'boolean' ? engineTool : engineTool?.available !== false;
  const validModels = engine === 'chat' ? models.length >= 1 : ['camel', 'crewai'].includes(engine) ? models.length === 2 : models.length >= 2;
  const nativeChooser = typeof window.modelDesk?.chooseProjectDirectory === 'function';

  const loadProjects = useCallback(async () => {
    try { const list = await workbenchApi.listProjects(); setProjects(Array.isArray(list) ? list : list.projects || []); }
    catch (failure) { setError(failure.message); }
  }, []);
  useEffect(() => { loadProjects(); }, [loadProjects]);
  useEffect(() => {
    if (!projectId) return;
    let disposed = false;
    workbenchApi.getProject(projectId).then((loaded) => { if (disposed) return; setProject(loaded); setLoadingProject(false); localStorage.setItem('modeldesk.project', loaded.id); if (!loaded.git?.available) setScope('selected'); }).catch((failure) => { if (!disposed) { setError(failure.message); setLoadingProject(false); } });
    return () => { disposed = true; };
  }, [projectId]);
  useEffect(() => {
    if (!reviewId) return;
    let disposed = false; let stream;
    workbenchApi.getJob(reviewId).then(async (saved) => {
      if (disposed || saved.mode !== 'review') return;
      setJob(hydrateJob(saved)); setProjectId(saved.project_id); setEngine(saved.engine); setView('results'); setStreamError(false);
      modelHandler.current(saved.models); if (saved.chairman) setChairman(saved.chairman); if (saved.rounds) setRounds(saved.rounds);
      localStorage.setItem('modeldesk.reviewJob', saved.id);
      if (saved.snapshot_id) workbenchApi.getSnapshot(saved.project_id, saved.snapshot_id).then((loaded) => { if (!disposed) { setSnapshot(loaded); setScope(loaded.scope); setBaseRef(loaded.base_ref || ''); } }).catch((failure) => { if (!disposed) setError(failure.message); });
      if (!isActive(saved)) return;
      stream = workbenchApi.events(saved.id, Math.max(0, ...(saved.events || []).map((event) => event.seq || 0))); streamRef.current = stream;
      stream.onmessage = (message) => {
        if (disposed) return;
        let event; try { event = JSON.parse(message.data); } catch { return; }
        if (!event.seq && message.lastEventId) event.seq = Number(message.lastEventId);
        setStreamError(false); setJob((previous) => applyEvent(previous, event));
        if (['complete', 'error', 'cancelled'].includes(event.type)) { stream.close(); workbenchApi.getJob(saved.id).then((full) => { if (!disposed) setJob(hydrateJob(full)); }).catch(() => {}); onRefresh(); }
      };
      stream.onerror = () => { if (!disposed) setStreamError(true); };
    }).catch((failure) => { if (!disposed) { setError(failure.message); setReviewId(null); localStorage.removeItem('modeldesk.reviewJob'); } });
    return () => { disposed = true; stream?.close(); streamRef.current = null; };
  }, [reviewId, onRefresh]);

  const clearReview = () => { setReviewId(null); setJob(null); setView('setup'); setFocus(null); setStreamError(false); localStorage.removeItem('modeldesk.reviewJob'); };
  const selectProject = (item) => { if (item.id === projectId) return; clearReview(); setProject(null); setSnapshot(null); setFiles([]); setFileSearch(''); setBaseRef(''); setScope('working'); setLoadingProject(true); setProjectId(item.id); setError(''); };
  const openReview = (item) => { if (reviewId === item.id) { setView('results'); return; } setJob(null); setSnapshot(null); setResultTab('findings'); setReviewId(item.id); setError(''); };
  const refreshProject = async () => { if (!projectId) return; setLoadingProject(true); setError(''); try { const loaded = await workbenchApi.getProject(projectId); setProject(loaded); } catch (failure) { setError(failure.message); } finally { setLoadingProject(false); } };
  const addProject = async (event) => {
    event.preventDefault(); if (!addPath.trim() || adding) return;
    setAdding(true); setError('');
    try { const body = { name: addName.trim() || addPath.trim().split('/').filter(Boolean).pop(), path: addPath.trim() }; const added = editingProject ? await workbenchApi.updateProject(editingProject.id, body) : await workbenchApi.addProject(body); await loadProjects(); if (editingProject) { clearReview(); setSnapshot(null); setFiles([]); setFileSearch(''); setBaseRef(''); const loaded = await workbenchApi.getProject(added.id); setProject(loaded); setProjectId(loaded.id); setScope(loaded.git?.available ? 'working' : 'selected'); setProjectNotice('项目登记已更新。'); } else selectProject(added); setAddOpen(false); setEditingProject(null); setAddName(''); setAddPath(''); }
    catch (failure) { setError(failure.message); } finally { setAdding(false); }
  };
  const openAdd = () => { setEditingProject(null); setAddName(''); setAddPath(''); setAddOpen(true); };
  const openEdit = () => { setEditingProject(project); setAddName(project.name); setAddPath(project.path); setAddOpen(true); };
  const unregisterProject = async () => { if (!projectId || running) return; setAdding(true); setError(''); try { await workbenchApi.removeProject(projectId); await loadProjects(); clearReview(); setProjectId(null); setProject(null); setSnapshot(null); setFiles([]); setUnregisterOpen(false); localStorage.removeItem('modeldesk.project'); setProjectNotice('已解除项目登记，原文件夹与代码文件保留。'); } catch (failure) { setError(failure.message); } finally { setAdding(false); } };
  const chooseDirectory = async () => { if (!nativeChooser || choosing) return; setChoosing(true); try { const selected = await window.modelDesk.chooseProjectDirectory(); const path = typeof selected === 'string' ? selected : selected?.path; if (path) { setAddPath(path); if (!addName) setAddName(path.split('/').filter(Boolean).pop()); } } catch (failure) { setError(failure.message); } finally { setChoosing(false); } };
  const changeScope = (value) => { setScope(value); setSnapshot(null); setError(''); };
  const toggleFile = (path) => { setFiles((previous) => previous.includes(path) ? previous.filter((file) => file !== path) : [...previous, path]); setSnapshot(null); };
  const preview = async () => { if (!projectId || previewing) return; setPreviewing(true); setError(''); try { const created = await workbenchApi.createSnapshot(projectId, { scope, ...(scope === 'branch' ? { base_ref: baseRef.trim() } : {}), ...(scope === 'selected' ? { files } : {}) }); setSnapshot(created); setFocus(null); } catch (failure) { setError(failure.message); } finally { setPreviewing(false); } };
  const startReview = async () => {
    if (!snapshot?.id || starting || running || !validModels || !engineAvailable || !status?.gateway?.online) return;
    setStarting(true); setError(''); setResultTab('findings');
    try { const created = await workbenchApi.createReview(projectId, { snapshot_id: snapshot.id, engine, models, chairman: engine === 'council' ? chairman : undefined, rounds, prompt: prompt.trim() }); setJob(created); setReviewId(created.id); setView('results'); onRefresh(); }
    catch (failure) { setError(failure.message); } finally { setStarting(false); }
  };
  const cancelReview = async () => { if (!job || canceling) return; setCanceling(true); try { const stopped = await workbenchApi.cancelJob(job.id); setJob(hydrateJob(stopped)); streamRef.current?.close(); setStreamError(false); onRefresh(); } catch (failure) { setError(failure.message); } finally { setCanceling(false); } };
  const changeModel = (id) => { const next = models.includes(id) ? models.filter((model) => model !== id) : models.length < 4 ? [...models, id] : models; onModelsChange(next); };
  const canPreview = project && !previewing && !running && (scope !== 'branch' || baseRef.trim()) && (scope !== 'selected' || files.length > 0);

  return <main className="workspace project-workspace"><header className="workspace-header"><div className="header-title"><button className="mobile-menu icon-button" aria-label="打开导航" onClick={onOpenNavigation}><Icon name="menu" /></button><span className="mode-header-icon"><Icon name="folder" size={22} /></span><div><h1>项目与代码评审</h1><span>本地项目 · 真实代码上下文</span></div></div><button className="subtle-button" onClick={openAdd}><Icon name="plus" size={16} />添加项目</button></header><div className="project-layout"><aside className="project-list"><div className="project-list-heading"><strong>本地项目</strong><button className="icon-button" aria-label="刷新项目列表" onClick={loadProjects}><Icon name="refresh" size={15} /></button></div>{projects.length ? projects.map((item) => <button className={`project-list-item ${item.id === projectId ? 'selected' : ''}`} key={item.id} onClick={() => selectProject(item)}><Icon name="folder" size={16} /><span><strong>{item.name}</strong><small title={item.path}>{item.path}</small><em>{item.git?.available ? item.git.branch || 'Git 仓库' : '普通文件夹'}</em></span>{jobs.some((entry) => entry.project_id === item.id && isActive(entry)) && <span className="nav-running" />}</button>) : <p className="project-list-empty">添加一个已有项目，读取实际代码与差异。</p>}<button className="project-list-add" onClick={openAdd}><Icon name="plus" size={15} />添加本地项目</button></aside><div className="project-content">{projectNotice && <p className="resource-notice">{projectNotice}</p>}{error && <div className="error-banner" role="alert"><span>{error}</span><button className="icon-button" aria-label="关闭错误提示" onClick={() => setError('')}><Icon name="close" size={15} /></button></div>}{!projectId ? <div className="project-empty"><Icon name="folder" size={43} /><h2>从一个真实项目开始</h2><p>选择代码文件夹，预览实际发送的文件与行号，再让模型审查。</p><button className="primary-button" onClick={openAdd}><Icon name="plus" size={16} />添加本地项目</button><small>源码按所选范围读取；凭据、缓存与不适合审查的内容由后端排除。</small></div> : !project ? <StagePending text="正在读取项目与文件清单" running /> : <><div className="project-heading"><div><h2>{project.name}</h2><p title={project.path}>{project.path}</p></div><div className="project-registration-actions"><button className="subtle-button" disabled={running} onClick={openEdit}>编辑登记</button><button className="subtle-button" disabled={running} onClick={() => setUnregisterOpen(true)}>解除登记</button></div><div className="repository-state"><span className={`status-dot ${project.git?.available ? 'online' : ''}`} />{project.git?.available ? <><span>{project.git.branch || '游离提交'}</span><code>{project.git.head?.slice(0, 7)}</code></> : <span>普通文件夹</span>}<button className="icon-button" onClick={refreshProject} disabled={loadingProject} aria-label="刷新项目状态"><Icon name="refresh" size={16} /></button></div></div>{project.git?.available && project.git.status?.length > 0 && <details className="repository-changes"><summary>Git 工作区状态</summary><pre>{readable(project.git.status)}</pre></details>}<div className="project-view-tabs"><button className={view === 'setup' ? 'selected' : ''} onClick={() => setView('setup')}>准备评审</button><button className={view === 'results' ? 'selected' : ''} onClick={() => setView('results')}>评审记录<span>{currentJobs.length}</span></button>{job && view === 'results' && <button className="new-review-link" onClick={clearReview}><Icon name="plus" size={14} />新建评审</button>}</div>{view === 'setup' ? <><section className="review-scope-panel"><header><span className="section-step">1</span><div><h3>选择代码范围</h3><p>范围决定实际读取的文件。预览后才会提交给模型。</p></div></header><div className="scope-options">{Object.entries(SCOPES).map(([value, labels]) => <button key={value} className={scope === value ? 'selected' : ''} disabled={running || (!project.git?.available && value !== 'selected')} onClick={() => changeScope(value)}>{labels[0]}</button>)}</div><p className="scope-description">{SCOPES[scope]?.[1]}</p>{scope === 'branch' && <label className="base-ref-input"><span>比较基准</span><input value={baseRef} onChange={(event) => { setBaseRef(event.target.value); setSnapshot(null); }} placeholder="分支、标签或提交，例如 origin/main" disabled={running} /></label>}{scope === 'selected' && <div className="file-selector"><header><input aria-label="搜索项目文件" value={fileSearch} onChange={(event) => setFileSearch(event.target.value)} placeholder="按文件路径筛选…" /><span>已选 {files.length} 个文件</span></header><div>{visibleFiles.map((file) => <label key={file.path}><input type="checkbox" checked={files.includes(file.path)} disabled={running} onChange={() => toggleFile(file.path)} /><span>{file.path}</span><small>{file.status}</small></label>)}</div>{catalog.length === 0 && <p className="result-note">没有可供审查的文件。请刷新项目，或检查文件清单中的排除说明。</p>}</div>}<div className="scope-preview-action"><span>预览会显示纳入、排除与截断清单。</span><button className="subtle-button" disabled={!canPreview} onClick={preview}><Icon name="code" size={15} />{previewing ? '正在生成预览' : snapshot ? '重新生成上下文' : '生成上下文预览'}</button></div></section>{snapshot && <SnapshotManifest snapshot={snapshot} focus={null} />}<section className="review-engine-panel"><header><span className="section-step">2</span><div><h3>选择审查方式</h3><p>同一份固定代码快照，交给不同协作流程。</p></div></header><div className="review-engine-options">{Object.entries(ENGINES).map(([value, label]) => <button key={value} className={engine === value ? 'selected' : ''} onClick={() => setEngine(value)} disabled={running}><strong>{label}</strong><span>{value === 'chat' ? '模型独立检查' : value === 'council' ? '回答、互评与综合' : value === 'camel' ? '研究员与审稿人' : '起草与复核'}</span></button>)}</div><ReviewModels catalog={status?.models || []} models={models} onChange={changeModel} disabled={running} /><div className="review-options-row">{engine === 'council' && <label className="inline-setting"><span>主席模型</span><select value={chairman} onChange={(event) => setChairman(event.target.value)} disabled={running}>{(status?.models || []).map((model) => <option key={model.id} value={model.id}>{model.id}</option>)}</select></label>}{engine === 'camel' && <label className="inline-setting"><span>讨论轮数</span><select value={rounds} onChange={(event) => setRounds(Number(event.target.value))} disabled={running}>{[1, 2, 3, 4, 5, 6].map((round) => <option key={round} value={round}>{round} 轮</option>)}</select></label>}{!validModels && <span className="review-selection-warning">{['camel', 'crewai'].includes(engine) ? '此方式需要恰好两个模型' : `请至少选择 ${engine === 'chat' ? 1 : 2} 个模型`}</span>}</div><label className="review-prompt-label"><span>额外审查重点（可选）</span><textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} disabled={running} placeholder="例如：重点检查错误处理、数据丢失与身份信息泄漏。" rows={2} /></label><div className="review-submit-bar"><div>{snapshot ? <><strong>使用已预览快照 {snapshot.id.slice(0, 8)}</strong><span>提交实际文件片段；模型只根据这份代码提供意见。</span></> : <><strong>请先生成代码上下文预览</strong><span>预览后才能开始代码评审。</span></>}</div><button className="primary-button" disabled={!snapshot?.files?.length || starting || running || !validModels || !engineAvailable || !status?.gateway?.online} onClick={startReview}><Icon name="council" size={16} />{starting ? '正在启动' : '开始代码评审'}</button></div></section></> : <div className="project-review-history"><div className="project-review-list">{currentJobs.length ? currentJobs.map((item) => <button className={item.id === reviewId ? 'selected' : ''} key={item.id} onClick={() => openReview(item)}><strong>{ENGINES[item.engine] || '代码评审'}</strong><span>{JOB_STATUS[item.status] || item.status}</span><small>{formatDate(item.created_at)}{item.snapshot_id && ` · 快照 ${item.snapshot_id.slice(0, 8)}`}</small></button>) : <p className="result-note">此项目尚无评审记录。</p>}</div>{job ? <section className="selected-review"><header><div><strong>{ENGINES[job.engine]}代码评审</strong><span>{JOB_STATUS[job.status] || job.status} · {formatDate(job.created_at)}</span></div><div>{running && <button className="stop-button" disabled={canceling} onClick={cancelReview}><Icon name="stop" size={14} />{canceling ? '正在停止' : '停止评审'}</button>}<button className="subtle-button" onClick={() => download(job, 'md')}><Icon name="export" size={15} />Markdown</button><button className="subtle-button" onClick={() => download(job, 'json')}>JSON</button></div></header>{job.error && <div className="output-error">{job.error}</div>}{streamError && running && <div className="stream-warning">实时连接暂时中断，正在重连；后台评审继续执行。</div>}<ReviewResult job={job} snapshot={snapshot} resultTab={resultTab} onResultTab={setResultTab} onFocusFinding={setFocus} focus={focus} /></section> : reviewId ? <StagePending text="正在恢复评审记录" running /> : <div className="review-history-empty"><Icon name="council" size={30} /><p>选择一条记录，查看问题、上下文与模型原始评审。</p></div>}</div>}</>}</div></div>{addOpen && <div className="project-modal-backdrop"><section className="project-add-modal" role="dialog" aria-modal="true" aria-labelledby="project-add-title"><header><div><h2 id="project-add-title">{editingProject ? '编辑项目登记' : '添加本地项目'}</h2><p>注册已有代码文件夹，保留原项目结构。</p></div><button className="icon-button" aria-label="关闭添加项目" onClick={() => setAddOpen(false)}><Icon name="close" size={19} /></button></header><form onSubmit={addProject}><label><span>项目名称</span><input value={addName} onChange={(event) => setAddName(event.target.value)} placeholder="例如：BehaviorFlow" /></label><label><span>本地文件夹路径</span><div className="project-path-input"><input value={addPath} onChange={(event) => setAddPath(event.target.value)} placeholder="/Users/…/Project/项目目录" required /><button type="button" className="subtle-button" disabled={!nativeChooser || choosing} onClick={chooseDirectory}><Icon name="folder" size={15} />{choosing ? '正在选择' : '选择文件夹'}</button></div></label><p className="directory-picker-note">{nativeChooser ? '可选择项目文件夹，或粘贴其完整路径。' : '浏览器中请粘贴本机文件夹完整路径；桌面应用支持直接选择。'}</p>{error && <p className="output-error" role="alert">{error}</p>}<footer><button type="button" className="subtle-button" onClick={() => setAddOpen(false)}>取消</button><button type="submit" className="primary-button" disabled={!addPath.trim() || adding}>{adding ? '正在读取项目' : editingProject ? '保存登记' : '添加项目'}</button></footer></form></section></div>}{unregisterOpen && <div className="project-modal-backdrop"><section className="project-add-modal" role="dialog" aria-modal="true" aria-labelledby="project-unregister-title"><header><div><h2 id="project-unregister-title">解除项目登记</h2><p>仅解除登记，原文件夹与代码文件不会删除。</p></div><button className="icon-button" aria-label="关闭解除登记" onClick={() => setUnregisterOpen(false)}><Icon name="close" size={18} /></button></header><p className="project-unregister-summary">{project?.name}<br />{project?.path}</p><footer><button className="subtle-button" onClick={() => setUnregisterOpen(false)}>保留登记</button><button className="primary-button" onClick={unregisterProject} disabled={adding}>{adding ? '正在解除' : '仅解除登记'}</button></footer></section></div>}</main>;
}
