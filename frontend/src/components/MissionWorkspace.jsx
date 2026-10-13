import { useCallback, useEffect, useRef, useState } from 'react';
import { workbenchApi } from '../api';
import { harnessApi } from '../harness-api';
import { hydrateJob } from '../workbench-state';
import { attachmentIssues } from '../attachment-files';
import { contextErrors } from '../platform-settings';
import { useProjects } from '../project-context';
import { MODES } from '../workbench-modes';
import { RUN_STATUS, missionActive, missionRecoverable, missionScopeKey, readMissionWorkspace, missionProgress, missionErrors, selectedMissionStep, stepJobId, missionRequest, copyMissionDraft, nextStepId } from '../mission-state';
import useMissions from '../hooks/useMissions';
import { Icon, Markdown, ResultProvenance } from './WorkbenchResults';
import { CopyTextButton } from './ConversationActions';
import AttachmentComposer from './AttachmentComposer';
import ContextAttachments from './ContextAttachments';
import MissionTeamPicker from './MissionTeamPicker';
import { teamIssues } from '../mission-teams';
import './MissionWorkspace.css';

const engines = { ...MODES, review: { label: '代码审查', source: '项目代码快照', icon: 'code' } };
const dateLabel = (date) => date ? new Date(date).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '';
const latestError = (step) => step?.error || step?.attempts?.at(-1)?.error;

export function MissionPlan({ steps, onChange, editable, models = [] }) {
  const change = (index, patch) => onChange(steps.map((step, position) => position === index ? { ...step, ...patch } : step));
  const move = (index, direction) => { const next = [...steps]; [next[index], next[index + direction]] = [next[index + direction], next[index]]; onChange(next); };
  return <ol className="mission-plan" aria-label="协作执行计划">{steps.map((step, index) => <li key={step.id}>
    <span className="mission-step-number">{index + 1}</span><div className="mission-plan-step"><header><strong>{step.title}</strong><span>{engines[step.mode]?.source || step.mode}</span>{step.require_review && <small>需你审阅</small>}</header>
      {step.mode === 'camel' && !step.workflow && (step.models || models).length > 2 && <p className="mission-participant-note">CAMEL 双角色对话使用前两位成员：{(step.models || models).slice(0, 2).join(' + ')}。其他步骤按计划使用协作组成员。</p>}
      {editable ? <details><summary>调整这一步<Icon name="chevron" size={13} /></summary><div className="mission-step-editor"><label><span>步骤名称</span><input value={step.title} maxLength={100} onChange={(event) => change(index, { title: event.target.value })} /></label><label><span>协作引擎</span><select value={step.mode} onChange={(event) => change(index, { mode: event.target.value, workflow: undefined })}>{Object.entries(engines).map(([id, engine]) => <option key={id} value={id}>{engine.label} · {engine.source}</option>)}</select></label><label className="mission-step-instructions"><span>执行要求</span><textarea rows={3} value={step.instructions} onChange={(event) => change(index, { instructions: event.target.value })} /></label><label className="mission-review-toggle"><input type="checkbox" checked={Boolean(step.require_review)} onChange={(event) => change(index, { require_review: event.target.checked })} />完成后暂停，等我审阅再继续</label><div className="mission-step-actions"><button type="button" disabled={index === 0} onClick={() => move(index, -1)}>上移</button><button type="button" disabled={index === steps.length - 1} onClick={() => move(index, 1)}>下移</button><button type="button" disabled={steps.length === 1} onClick={() => onChange(steps.filter((_, position) => position !== index))}>移除此步</button></div></div></details> : <p>{step.instructions}</p>}
    </div></li>)}</ol>;
}

export default function MissionWorkspace({ status, models, onModelsChange, context, onContextChange, jobs, onOpenNavigation, onOpenProjects, onOpenArtifacts, onClearArtifacts, onToggleArtifacts }) {
  const { activeProjectId, activeProject, assets, transfers } = useProjects();
  const [saved] = useState(() => readMissionWorkspace(localStorage, activeProjectId));
  const mission = useMissions(activeProjectId, saved.runId);
  const [draft, setDraft] = useState(saved.draft);
  const [templateId, setTemplateId] = useState(saved.templateId);
  const [customSteps, setCustomSteps] = useState(saved.steps);
  const [requestOptions, setRequestOptions] = useState(saved.requestOptions);
  const [team, setTeam] = useState(saved.team);
  const [draftModels, setDraftModels] = useState(saved.models);
  const missionModels = team?.members?.map((member) => member.model) || draftModels || models;
  const [teamPending, setTeamPending] = useState(false);
  const [teamFilter, setTeamFilter] = useState('');
  const chooseTeam = useCallback((next) => { setTeam(next); if (next?.members?.length) { const selected = next.members.map((member) => member.model); setDraftModels(selected); onModelsChange(selected); } }, [onModelsChange]);
  const [templates, setTemplates] = useState([]);
  const [templateError, setTemplateError] = useState('');
  const [editPlan, setEditPlan] = useState(false);
  const [selection, setSelection] = useState(null);
  const [childJob, setChildJob] = useState(null);
  const [childError, setChildError] = useState('');
  const [pendingAttachments, setPendingAttachments] = useState(false);
  const [modelMenu, setModelMenu] = useState(false);
  const [query, setQuery] = useState('');
  const [retryAcknowledged, setRetryAcknowledged] = useState(false);
  const attachmentRef = useRef(null);
  const displayedChild = useRef(null);
  const run = mission.run;
  const template = templates.find((item) => item.id === templateId) || templates[0];
  const steps = customSteps || template?.steps || [];
  const selectedStep = selectedMissionStep(run, selection);
  const childId = stepJobId(selectedStep);
  const progress = missionProgress(run);
  const active = missionActive(run);
  const runTeams = [...new Map(mission.runs.filter((item) => item.team_snapshot?.id).map((item) => [item.team_snapshot.id, item.team_snapshot.name])).entries()];
  const visibleRuns = mission.runs.filter((item) => (!teamFilter || item.team_snapshot?.id === teamFilter) && `${item.title} ${item.goal}`.toLowerCase().includes(query.toLowerCase()));
  useEffect(() => { let disposed = false; harnessApi.templates().then((result) => { if (!disposed) setTemplates(Array.isArray(result) ? result : result.templates || []); }).catch((failure) => { if (!disposed) setTemplateError(failure.message); }); return () => { disposed = true; }; }, []);
  useEffect(() => { localStorage.setItem(missionScopeKey(activeProjectId), JSON.stringify({ runId: mission.runId, draft, templateId, steps: customSteps, requestOptions, team, models: missionModels })); }, [activeProjectId, mission.runId, draft, templateId, customSteps, requestOptions, team, missionModels]);
  useEffect(() => {
    let disposed = false;
    if (displayedChild.current !== childId) { onClearArtifacts(); displayedChild.current = childId; }
    if (!childId) return;
    workbenchApi.getJob(childId).then((loaded) => {
      if (disposed) return;
      if ((loaded.project_id || null) !== (activeProjectId || null)) return;
      const job = hydrateJob(loaded); setChildJob(job); setChildError(''); onOpenArtifacts(job, false);
    }).catch((failure) => { if (!disposed) setChildError(failure.message); });
    return () => { disposed = true; };
  }, [childId, run?.updated_at, activeProjectId, onOpenArtifacts, onClearArtifacts]);

  const errors = missionErrors({ goal: draft, steps, models: missionModels, catalog: status?.models, online: Boolean(status?.gateway?.online), attachments: [...teamIssues(team, status?.models || []), ...((context.project_id || null) !== activeProjectId ? ['正在切换项目资料，请稍候。'] : []), ...attachmentIssues(assets, context.attachment_ids, context.include_project_assets), ...contextErrors(context), ...(steps.some((step) => step.mode === 'review') && !context.snapshot_id ? ['代码审查步骤需要先在“来源与工具”选择已预览的代码快照。'] : [])] });
  const selectRun = (id) => { mission.select(id); setSelection(null); setChildJob(null); setChildError(''); setRetryAcknowledged(false); onClearArtifacts(); };
  const start = async (event) => {
    event.preventDefault(); if (errors.length || pendingAttachments || teamPending || transfers || mission.busy) return;
    const created = await mission.create(missionRequest({ goal: draft, templateId: template?.id || templateId, steps, models: missionModels, context: { ...context, project_id: activeProjectId }, requestOptions, team }));
    if (created) { setDraft(''); setSelection(null); setChildJob(null); onClearArtifacts(); }
  };
  const chooseStep = (step) => { setSelection({ runId: run.id, stepId: step.id }); setChildJob(null); onClearArtifacts(); };
  const copyRun = () => { const copied = copyMissionDraft(run); selectRun(null); setDraft(copied.draft); setTemplateId(copied.templateId); setCustomSteps(copied.steps); setRequestOptions(copied.requestOptions); setTeam(copied.team); setDraftModels(copied.models); onModelsChange(copied.models); onContextChange(copied.context); };
  const toggleModel = (id) => { const selected = missionModels.includes(id) ? missionModels.filter((model) => model !== id) : missionModels.length < 4 ? [...missionModels, id] : missionModels; setDraftModels(selected); onModelsChange(selected); };

  return <main className="workspace mission-workspace">
    <header className="workspace-header"><div className="header-title"><button className="mobile-menu icon-button" aria-label="打开导航" onClick={onOpenNavigation}><Icon name="menu" /></button><span className="mode-header-icon"><Icon name="tasks" size={22} /></span><div><h1>{run?.title || '协作任务'}</h1><span>{activeProject?.name || '独立任务'} · 目标、过程与交付保存在一起</span></div></div><div className="mission-header-actions"><button className="subtle-button" onClick={() => selectRun(null)}><Icon name="plus" size={15} />新建任务</button>{run && <button className="subtle-button" onClick={onToggleArtifacts}><Icon name="panel" size={16} />成果</button>}</div></header>
    {mission.runs.length > 0 && <div className="mission-history-bar"><Icon name="clock" size={16} /><label><span className="mission-sr-only">搜索协作任务</span><input placeholder="搜索本项目任务" value={query} onChange={(event) => setQuery(event.target.value)} /></label><select aria-label="打开已有协作任务" value={mission.runId || ''} onChange={(event) => selectRun(event.target.value || null)}><option value="">新任务草稿</option>{visibleRuns.map((item) => <option key={item.id} value={item.id}>{RUN_STATUS[item.status] || item.status} · {item.title || item.goal?.slice(0, 50)} · {dateLabel(item.created_at)}</option>)}</select><span>{mission.runs.length} 项</span></div>}
    <div className="mission-content">
      {runTeams.length > 1 && <label className="mission-team-filter"><span>按协作组查看任务</span><select aria-label="筛选任务协作组" value={teamFilter} onChange={(event) => setTeamFilter(event.target.value)}><option value="">全部协作组</option>{runTeams.map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label>}
      {(mission.error || templateError) && <div className="error-banner" role="alert">{mission.error || templateError}<button className="subtle-button" onClick={mission.refresh}>重新连接</button></div>}
      {mission.runId && !run ? <div className="mission-loading" role="status"><span className="small-spinner" />正在读取保存的任务…</div> : run ? <>
        <div className="mission-goal"><div><span className={`mission-status mission-status-${run.status}`}>{active && <span className="activity-dot" />}{RUN_STATUS[run.status] || run.status}</span><time>{dateLabel(run.created_at)}</time><CopyTextButton text={run.goal} label="复制目标" /></div><h2>任务目标</h2><Markdown>{run.goal}</Markdown></div>
        {run.team_snapshot?.members?.length > 0 && <details className="mission-running-team"><summary><Icon name="group" size={16} /><strong>{run.team_snapshot.name || '本次协作组'}</strong><span>{run.team_snapshot.members.length} 名成员 · 使用任务启动时的分工</span></summary>{run.team_snapshot.members.map((member) => <div key={member.id}><strong>{member.name}</strong><small>{member.model}</small><p>{member.role}</p></div>)}</details>}
        <section className="mission-progress" aria-label="整体任务进度"><div><strong>{run.status === 'complete' ? '全部步骤已完成' : run.status === 'waiting_review' ? '请审阅这一步的成果' : progress.current?.title || '执行记录已保存'}</strong><span>{progress.complete} / {progress.total} 步完成</span></div><progress value={progress.complete} max={progress.total || 1} /><p>{active ? '当前步骤接收之前已完成的输出；切换页面后，后台会继续运行。' : run.status === 'complete' ? '右侧可查看最终交付，也可以逐步检查来源与中间输出。' : '已完成步骤和实际输出会保留，继续时从未完成步骤开始。'}</p></section>
        {run.status === 'waiting_review' && <div className="mission-review-banner"><Icon name="council" size={20} /><div><strong>审阅后继续</strong><p>检查右侧实际输出，确认后再把结果交给下一步。</p></div><button className="primary-button" disabled={mission.busy} onClick={mission.approve}>确认并继续</button></div>}
        {run.error && <div className="output-error" role="alert">{run.error}</div>}
        <ol className="mission-timeline" aria-label="协作步骤与执行状态">{run.steps.map((step, index) => <li key={step.id} className={`${selectedStep?.id === step.id ? 'selected' : ''} mission-step-${step.status}`}><button className="mission-step-select" onClick={() => chooseStep(step)}><span className="mission-step-number">{step.status === 'complete' ? <Icon name="check" size={15} /> : index + 1}</span><span><strong>{step.title}</strong><small>{engines[step.mode]?.label || step.mode} · {engines[step.mode]?.source || step.mode}</small></span><span className={`mission-status mission-status-${step.status}`}>{step.status === 'running' && <span className="activity-dot" />}{step.review_status === 'pending' ? '待你审阅' : RUN_STATUS[step.status] || step.status}</span></button>{selectedStep?.id === step.id && <div className="mission-step-detail"><p>{step.config?.instructions || step.instructions}</p>{step.participant_models?.length > 0 && <div className="mission-participant-note"><strong>本步成员</strong><span>{step.participant_models.map((model) => run.team_snapshot?.members?.find((member) => member.model === model)?.name || model).join(" · ")}</span><small>{step.participant_policy}</small></div>}{step.attempts?.length > 0 && <div className="mission-attempt"><span>第 {step.attempts.length} 次执行</span><span>{dateLabel(step.attempts.at(-1).started_at)}</span>{stepJobId(step) && <button type="button" disabled={!childJob || childJob.id !== childId} onClick={() => onOpenArtifacts(childJob, true)}>查看实际输出<Icon name="panel" size={14} /></button>}</div>}{latestError(step) && <p className="output-error">{latestError(step)}</p>}{step.output?.text && <details className="mission-step-output"><summary>展开已保存的输出</summary><Markdown>{step.output.text}</Markdown><CopyTextButton text={step.output.text} label="复制这步输出" /></details>}{childJob?.id === childId && <ResultProvenance job={childJob} />}</div>}</li>)}</ol>
        {childError && <p className="output-error">读取步骤输出失败：{childError}</p>}
        {missionRecoverable(run) && <div className="mission-recovery"><strong>从未完成步骤继续</strong><p>保留之前已完成的步骤；本次会重新执行未完成步骤。</p><label><input type="checkbox" checked={retryAcknowledged} onChange={(event) => setRetryAcknowledged(event.target.checked)} />我知道这一步的工具操作可能再次执行</label><button className="primary-button" disabled={mission.busy || !retryAcknowledged} onClick={async () => { await mission.resume(); setRetryAcknowledged(false); }}>{mission.busy ? '正在恢复' : '重试未完成步骤'}</button></div>}
        <details className="mission-event-record"><summary>执行记录与导出<span>{run.events?.length || 0} 条记录</span></summary><div className="mission-export-actions"><a href={harnessApi.exportUrl(run.id)} download>下载任务 Markdown</a><a href={harnessApi.exportUrl(run.id, 'json')} download>下载完整 JSON</a></div><ol>{(run.events || []).map((event, index) => <li key={event.seq || index}><span>{dateLabel(event.created_at || event.timestamp)}</span><code>{event.type}</code><span>{event.message || event.title || event.step_id || ''}</span></li>)}</ol></details>
        <footer className="mission-run-actions"><button className="subtle-button" onClick={copyRun}>复制为新任务</button>{(active || run.status === 'waiting_review') && <button className="stop-button" disabled={mission.busy} onClick={mission.cancel}><Icon name="stop" size={15} />{mission.busy ? '正在停止' : '停止任务'}</button>}{run.status === 'complete' && <a className="primary-button" href={harnessApi.exportUrl(run.id)} download><Icon name="export" size={16} />下载任务成果</a>}</footer>
      </> : <form id="mission-draft-form" className="mission-draft" onSubmit={start}>
        <div className="mission-introduction"><h2>给团队一个目标。</h2><p>选一种协作计划，让不同模型接力研究、审查和完善。每一步的结果都留得住。</p></div>
        <div className="mission-template-tabs" role="group" aria-label="选择协作计划">{templates.map((item) => <button type="button" aria-pressed={template?.id === item.id} key={item.id} className={template?.id === item.id ? 'selected' : ''} onClick={() => { setTemplateId(item.id); setCustomSteps(null); }}><strong>{item.name}</strong><span>{item.description}</span></button>)}</div>
        <MissionTeamPicker projectId={activeProjectId} team={team} models={missionModels} catalog={status?.models || []} onChange={chooseTeam} onPending={setTeamPending} />
        <label className="mission-goal-input"><span>希望团队完成什么？</span><textarea aria-label="协作任务目标" value={draft} onChange={(event) => setDraft(event.target.value)} rows={4} placeholder="例如：根据项目资料，比较两个实现方案，审查风险，并交付一份可以执行的改进计划。" onPaste={(event) => attachmentRef.current?.handlePaste(event)} onDragOver={(event) => { if (event.dataTransfer.types.includes('Files')) event.preventDefault(); }} onDrop={(event) => attachmentRef.current?.handleDrop(event)} /></label>
        <AttachmentComposer ref={attachmentRef} attachmentIds={context.attachment_ids} onChange={(ids) => onContextChange({ ...context, attachment_ids: ids })} includeShared={context.include_project_assets} onIncludeShared={(include) => onContextChange({ ...context, include_project_assets: include })} disabled={mission.busy} onPendingChange={setPendingAttachments} />
        <section className="mission-plan-section"><header><div><h3>协作计划</h3><p>后续步骤会接收此前已完成的结果。</p></div><button type="button" className="subtle-button" aria-expanded={editPlan} onClick={() => setEditPlan(!editPlan)}><Icon name="settings" size={15} />{editPlan ? '收起调整' : '调整步骤'}</button></header>{steps.length ? <MissionPlan steps={steps} models={missionModels} onChange={setCustomSteps} editable={editPlan} /> : <p className="result-note">正在读取可用的协作计划…</p>}{editPlan && <button className="mission-add-step" type="button" disabled={steps.length >= 6} onClick={() => setCustomSteps([...steps, { id: nextStepId(steps), title: '进一步完善', mode: 'chat', instructions: '根据任务目标和此前步骤输出，修正遗漏并给出最终交付。', require_review: false }])}><Icon name="plus" size={15} />添加步骤</button>}</section>
        <details className="mission-advanced"><summary><Icon name="layers" size={16} />来源与工具<span>可选设置</span></summary><ContextAttachments value={context} onChange={onContextChange} jobs={jobs} mode="chat" onOpenProjects={onOpenProjects} disabled={mission.busy} /></details>
      </form>}
    </div>
    {!run && !mission.runId && <footer className="mission-compose-footer"><div>
        <div className="mission-launch"><div className="mission-models"><button type="button" className="subtle-button" aria-expanded={modelMenu} disabled={Boolean(team)} onClick={() => setModelMenu(!modelMenu)}><Icon name="group" size={15} />{missionModels.length} 个模型<Icon name="chevron" size={13} /></button><span>{team ? `${team.name} · 分工已就绪` : missionModels.join(' + ') || '先选择参与模型'}</span>{modelMenu && <div className="mission-model-menu">{(status?.models || []).map((model) => <label key={model.id}><input type="checkbox" checked={missionModels.includes(model.id)} disabled={!missionModels.includes(model.id) && missionModels.length >= 4} onChange={() => toggleModel(model.id)} /><span>{model.id}</span>{model.verified && <small>已验证</small>}</label>)}</div>}</div><button className="primary-button" disabled={Boolean(errors.length || mission.busy || pendingAttachments || teamPending || transfers)} type="submit" form="mission-draft-form"><Icon name="send" size={16} />{mission.busy ? '正在启动' : '开始协作任务'}</button></div>
        {(errors.filter((error) => draft.trim() || !error.includes('先写下')).length > 0 || pendingAttachments || transfers > 0) && <p className="mission-form-note">{pendingAttachments || transfers > 0 ? '请完成资料上传或选择后再开始。' : errors.find((error) => draft.trim() || !error.includes('先写下'))}</p>}
        <p className="mission-save-note"><Icon name="memory" size={13} />草稿按项目自动保存 · 运行中可切换页面</p>
    </div></footer>}
  </main>;
}
