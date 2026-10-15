import { useCallback, useEffect, useRef, useState } from 'react';
import { groupTaskApi } from '../group-task-api';
import { groupMemberKind, groupMemberKindLabel } from '../group-state';
import { APPROVAL_LABELS, TASK_LABELS, approvalDecision, eligibleReviews, memberEngineSupports, readTaskDraft, taskEngineLabel, taskActive, taskDraftKey, taskRequest } from '../group-task-state';
import { Icon, Markdown } from './WorkbenchResults';
import { CopyTextButton } from './ConversationActions';
import './GroupAgentTasks.css';

const short = (sha) => sha ? sha.slice(0, 12) : '尚未生成';
const date = (value) => value ? new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '';

export function ApprovalCard({ card, busy, onDecision }) {
  const [confirming, setConfirming] = useState(false);
  return <section className={`group-approval-card approval-${card.status}`} aria-label={`合入审批 ${card.title || card.id}`}>
    <header><Icon name="council" size={18} /><strong>{APPROVAL_LABELS[card.status] || card.status}</strong></header>
    <h3 title={card.title || '将审阅后的改动合入项目'}>{card.title || '将审阅后的改动合入项目'}</h3>
    <p>目标：本地 <strong>{card.target_branch || '项目分支'}</strong>。批准后写入此项目；推送 GitHub 需另外操作。</p>
    <dl><div><dt>基准提交</dt><dd><code title={card.base_sha}>{short(card.base_sha)}</code></dd></div><div><dt>审阅补丁</dt><dd><code title={card.patch_sha256}>{short(card.patch_sha256)}</code></dd></div><div><dt>变更文件</dt><dd>{card.changed_files?.length ?? 0} 个</dd></div></dl>
    {card.review_output && <details><summary>阅读独立审阅意见</summary><Markdown>{card.review_output}</Markdown></details>}
    {card.error && <p className="output-error" role="alert">{card.error}</p>}
    {card.merge_commit_sha && <p>{card.status === 'merged' ? '合入提交' : '待核对的候选提交'} <code>{short(card.merge_commit_sha)}</code></p>}
    {card.status === 'pending' && <div className="group-approval-actions">{confirming ? <><p>确认将上述版本合入本地项目？提交或补丁发生变化时，服务会拒绝本次审批。</p><button type="button" className="primary-button" disabled={busy} onClick={() => onDecision(card, 'approve')}>确认合入本地项目</button><button type="button" className="subtle-button" disabled={busy} onClick={() => setConfirming(false)}>返回检查</button></> : <><button type="button" className="primary-button" disabled={busy} onClick={() => setConfirming(true)}>批准并合入本地项目</button><button type="button" className="subtle-button" disabled={busy} onClick={() => onDecision(card, 'reject')}>先不合入</button></>}</div>}
  </section>;
}

export function TaskCard({ task, tasks, approvals, busy, onReview, onCancel, onRequestApproval, onInspect, onOpenArtifacts }) {
  const reviews = eligibleReviews(task, tasks);
  const [reviewId, setReviewId] = useState('');
  const approval = approvals.find((item) => item.task_id === task.id && ['pending', 'merging', 'merged', 'needs_attention'].includes(item.status));
  const name = task.member?.name || task.member_id;
  const [detail, setDetail] = useState(null);
  const [detailError, setDetailError] = useState('');
  const [inspecting, setInspecting] = useState(false);
  const inspect = async () => {
    setInspecting(true); setDetailError('');
    try { setDetail(await onInspect(task)); } catch (failure) { setDetailError(failure.message); }
    finally { setInspecting(false); }
  };
  return <article className={`group-agent-task task-${task.status}`} aria-label={`${name} 的${task.kind === 'review' ? '审阅' : '执行'}任务`}>
    <header><span className="group-member-avatar" aria-hidden="true">{name?.slice(0, 1) || 'A'}</span><div><strong>{name}</strong><small>由 {taskEngineLabel(task)} 执行 · {task.kind === 'review' ? '独立审阅' : '代码任务'}</small></div><span className="group-task-status">{taskActive(task) && <span className="small-spinner" />}{TASK_LABELS[task.status] || task.status}</span></header>
    <time>{date(task.created_at)}</time><Markdown>{task.prompt}</Markdown>
    {task.base_sha && <p className="group-task-provenance">基于 <code title={task.base_sha}>{short(task.base_sha)}</code>{task.diff_sha256 && <> · 补丁 <code title={task.diff_sha256}>{short(task.diff_sha256)}</code></>}{task.changed_files?.length > 0 && <> · {task.changed_files.length} 个文件</>}</p>}
    {task.project_context && <details className="group-task-reference"><summary>实际引用的项目与群聊上下文</summary><p className="result-note">执行开始时保存的项目说明与最近群聊摘要；内容受预算限制。</p><pre>{task.project_context}</pre></details>}
    {task.output && <details className="group-task-output" open={task.kind === 'review' || undefined}><summary>{task.kind === 'review' ? '审阅意见' : 'Agent 执行结果'}</summary><Markdown>{task.output}</Markdown><CopyTextButton text={task.output} label="复制 Agent 结果" /></details>}
    {task.error && <p className="output-error" role="alert">{task.error}</p>}
    {!!task.events?.length && <details className="group-task-events"><summary>运行记录 · {task.events.length} 条</summary><ol>{task.events.map((event, index) => <li key={`${event.at}-${index}`}><time>{date(event.at)}</time><span>{event.text || event.message || event.type}</span></li>)}</ol></details>}
    {task.changed_files?.length > 0 && <details className="group-task-diff" onToggle={(event) => { if (event.currentTarget.open && !detail && !inspecting) inspect(); }}><summary>检查文件与实际改动</summary><ul>{task.changed_files.map((file) => <li key={file.path || file}><code>{file.path || file}</code><span>{file.status || ''}</span></li>)}</ul>{inspecting && <p role="status">读取补丁中…</p>}{detailError && <p className="output-error">{detailError}<button type="button" className="subtle-button" onClick={inspect}>重试</button></p>}{detail?.diff && <pre>{detail.diff}</pre>}</details>}
    <div className="group-task-actions">{(task.output || task.diff_sha256) && <button type="button" className="subtle-button" onClick={() => onOpenArtifacts(task)}><Icon name="panel" size={14} />查看成果</button>}{taskActive(task) && <button type="button" disabled={busy} className="stop-button" onClick={() => onCancel(task)}><Icon name="stop" size={14} />停止任务</button>}{task.kind === 'code' && task.status === 'complete' && task.diff_sha256 && <button type="button" disabled={busy} className="subtle-button" onClick={() => onReview(task)}><Icon name="council" size={14} />请另一位成员审阅</button>}{reviews.length > 0 && !approval && <><select aria-label={`为 ${name} 的改动选择审阅记录`} value={reviewId || reviews[0].id} onChange={(event) => setReviewId(event.target.value)}>{reviews.map((review) => <option key={review.id} value={review.id}>{review.member?.name || review.member_id} · {date(review.created_at)}</option>)}</select><button type="button" disabled={busy} className="primary-button" onClick={() => onRequestApproval(task, reviews.find((review) => review.id === reviewId) || reviews[0])}>提交合入审批</button></>}</div>
    {task.kind === 'code' && task.status === 'complete' && !task.changed_files?.length && <p className="result-note">本次执行没有生成文件改动。</p>}
  </article>;
}

export default function GroupAgentTasks({ projectId, room, kind, onKindChange, onOpenProjects, onOpenArtifacts }) {
  const roomId = room.id;
  const members = (room.team_snapshot?.members || []).filter((member) => groupMemberKind(member) !== 'muse');
  const [draft, setDraft] = useState(() => readTaskDraft(localStorage, projectId, roomId));
  const [tasks, setTasks] = useState([]);
  const [engines, setEngines] = useState([]);
  const [enginesLoading, setEnginesLoading] = useState(true);
  const [approvals, setApprovals] = useState([]);
  const [busy, setBusy] = useState(false);
  const [composerOpen, setComposerOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [artifactId, setArtifactId] = useState(null);
  const [artifactDetail, setArtifactDetail] = useState(null);
  const alive = useRef(true);
  const promptRef = useRef(null);
  useEffect(() => { if (composerOpen && !window.matchMedia('(max-width: 600px)').matches) promptRef.current?.focus(); }, [composerOpen]);
  const polling = useRef(false);
  const refreshEngines = useCallback(async () => {
    setEnginesLoading(true);
    try { const result = await groupTaskApi.engines(); if (alive.current) setEngines(result.engines || []); }
    catch (failure) { if (alive.current) setError(failure.message); }
    finally { if (alive.current) setEnginesLoading(false); }
  }, []);
  const refresh = useCallback(async () => {
    if (polling.current) return;
    polling.current = true;
    try {
      const result = await Promise.all([groupTaskApi.list(projectId, roomId), groupTaskApi.approvals(projectId, roomId)]);
      if (alive.current) { setTasks(result[0].filter((task) => task.project_id === projectId && task.room_id === roomId)); setApprovals(result[1].filter((card) => card.project_id === projectId && card.room_id === roomId)); }
    } catch (failure) { if (alive.current) setError(failure.message); }
    finally { polling.current = false; if (alive.current) setLoading(false); }
  }, [projectId, roomId]);
  useEffect(() => { alive.current = true; refresh(); refreshEngines(); const timer = setInterval(refresh, 5000); return () => { alive.current = false; clearInterval(timer); }; }, [refresh, refreshEngines]);
  useEffect(() => { localStorage.setItem(taskDraftKey(projectId, roomId), JSON.stringify(draft)); }, [projectId, roomId, draft]);
  const selectedArtifact = tasks.find((task) => task.id === artifactId) || tasks.find((task) => task.output);
  useEffect(() => { if (selectedArtifact) onOpenArtifacts({ ...selectedArtifact, ...(artifactDetail?.id === selectedArtifact.id ? { diff: artifactDetail.diff } : {}), mode: 'group_agent' }, false); }, [selectedArtifact, artifactDetail, onOpenArtifacts]);
  const showArtifact = async (task) => { setArtifactId(task.id); onOpenArtifacts({ ...task, mode: 'group_agent' }, true); try { const detail = await groupTaskApi.get(task.id, projectId, roomId); if (alive.current) setArtifactDetail(detail); } catch (failure) { if (alive.current) setError(failure.message); } };
  const update = (patch) => setDraft((previous) => ({ ...previous, ...patch }));
  const defaultMember = kind === 'code' ? members.find((member) => ['codex', 'claude'].includes(groupMemberKind(member))) : members.find((member) => groupMemberKind(member) === 'model');
  const memberId = members.some((member) => member.id === draft.memberId) ? draft.memberId : defaultMember?.id || members[0]?.id;
  const selectedMember = members.find((member) => member.id === memberId);
  const selectedKind = groupMemberKind(selectedMember);
  const compatibleEngines = engines.filter((item) => memberEngineSupports(item, kind, selectedMember));
  const available = compatibleEngines.filter((item) => item.available && item.authenticated !== false);
  const engine = compatibleEngines.find((item) => item.id === draft.engine) || (kind === 'review' ? available.find((item) => item.id === 'member_model') : null) || available[0] || compatibleEngines[0];
  const sources = tasks.filter((task) => task.kind === 'code' && task.status === 'complete' && task.diff_sha256 && task.changed_files?.length);
  const source = sources.find((task) => task.id === draft.sourceTaskId);
  const valid = projectId && draft.prompt.trim() && memberId && engine?.available && engine?.authenticated !== false && (kind !== 'review' || (source && source.member_id !== memberId));
  const mutate = async (action) => { setBusy(true); setError(''); try { await action(); await refresh(); } catch (failure) { if (alive.current) setError(failure.message); } finally { if (alive.current) setBusy(false); } };
  const send = (event) => { event.preventDefault(); if (!valid || busy) return; const sent = draft.prompt; mutate(async () => { await groupTaskApi.create(taskRequest({ projectId, roomId, memberId, engine: engine.id, kind, prompt: draft.prompt, sourceTaskId: draft.sourceTaskId })); if (alive.current) { setDraft((previous) => ({ ...previous, prompt: previous.prompt === sent ? '' : previous.prompt })); setComposerOpen(false); } }); };
  const review = (task) => { update({ sourceTaskId: task.id, engine: 'member_model', memberId: (members.find((member) => member.id !== task.member_id && groupMemberKind(member) === 'model') || members.find((member) => member.id !== task.member_id))?.id || '', prompt: `请独立审阅这份改动，重点检查正确性、回归风险和验证证据。列出必须修复的问题，并明确当前审阅的限制。` }); onKindChange('review'); setComposerOpen(true); promptRef.current?.focus(); };
  return <div className={`group-agent-workspace ${composerOpen ? 'agent-composer-open' : 'agent-composer-collapsed'}`}>
    <div className="group-agent-timeline">
      {error && <div className="error-banner" role="alert">{error}<button type="button" className="subtle-button" onClick={() => { setError(''); refresh(); refreshEngines(); }}>重新连接</button></div>}
      {!projectId && <div className="group-task-empty"><h2>先为这个协作群选择项目</h2><p>代码任务需要本地 Git 项目，才能创建独立工作区并保留可审阅的改动。</p><button type="button" className="primary-button" onClick={onOpenProjects}>选择本地项目</button></div>}
      {loading && <p role="status" className="mission-loading"><span className="small-spinner" />正在读取成员任务…</p>}
      {!loading && !tasks.length && projectId && <div className="group-task-empty"><Icon name="tasks" size={30} /><h2>把讨论交给成员执行</h2><p>指定一位成员，在独立 Git 工作区完成任务。另一位成员审阅实际补丁后，由你决定是否合入本地项目。</p><div className="group-task-sequence"><span>分配任务</span><Icon name="chevron" size={13} /><span>独立审阅</span><Icon name="chevron" size={13} /><span>你来批准</span></div></div>}
      {[...tasks].sort((a, b) => String(a.created_at).localeCompare(String(b.created_at))).map((task) => <div key={task.id}><TaskCard onOpenArtifacts={showArtifact} task={task} tasks={tasks} approvals={approvals} busy={busy} onReview={review} onCancel={(target) => mutate(() => groupTaskApi.cancel(target.id, projectId, roomId))} onInspect={(target) => groupTaskApi.get(target.id, projectId, roomId)} onRequestApproval={(target, selectedReview) => mutate(() => groupTaskApi.requestApproval({ project_id: projectId, room_id: roomId, task_id: target.id, review_task_id: selectedReview.id }))} />{approvals.filter((card) => card.task_id === task.id).map((card) => <ApprovalCard key={`${card.id}:${card.base_sha}:${card.patch_sha256}`} card={card} busy={busy} onDecision={(target, decision) => mutate(() => groupTaskApi.decide(target.id, approvalDecision(target, decision)))} />)}</div>)}
    </div>
    <div className="group-agent-composer-toggle"><button type="button" aria-expanded={composerOpen} aria-controls={`group-agent-form-${roomId}`} onClick={() => setComposerOpen(!composerOpen)}><Icon name={composerOpen ? 'close' : 'plus'} size={17} /><strong>{composerOpen ? '收起，查看任务记录' : kind === 'review' ? '发起独立审阅' : '派发新任务'}</strong>{!composerOpen && draft.prompt.trim() && <small>草稿已保存</small>}</button></div>
    <form id={`group-agent-form-${roomId}`} className="group-agent-composer" onSubmit={send}>
      <div className="group-agent-assignment"><label>交给<select aria-label="指定执行成员" value={memberId || ''} onChange={(event) => update({ memberId: event.target.value })}>{members.map((member) => <option key={member.id} value={member.id}>@{member.name} · {groupMemberKindLabel(member)}</option>)}</select></label><label>执行引擎<select aria-label="Agent 执行引擎" value={engine?.id || ''} onChange={(event) => update({ engine: event.target.value })}>{!compatibleEngines.length && <option value="">正在检测</option>}{compatibleEngines.map((item) => <option key={item.id} value={item.id} disabled={!item.available || item.authenticated === false}>{item.id === 'member_model' ? `成员模型 · ${selectedMember?.model || '已保存模型'}` : item.label || item.id}{!item.available ? ' · 不可用' : item.authenticated === false ? ' · 未登录' : ''}</option>)}</select></label></div>
      {kind === 'review' && <label className="group-review-source">审阅哪份改动<select aria-label="选择待审阅任务" value={source?.id || ''} onChange={(event) => update({ sourceTaskId: event.target.value })}><option value="">请选择已完成的代码任务</option>{sources.map((task) => <option key={task.id} value={task.id}>{task.member?.name || task.member_id} · {task.prompt.slice(0, 65)} · {short(task.diff_sha256)}</option>)}</select></label>}
      <div className="composer"><textarea ref={promptRef} rows={3} aria-label={kind === 'review' ? 'Agent 审阅要求' : 'Agent 执行任务'} value={draft.prompt} maxLength={12000} placeholder={kind === 'review' ? '告诉审阅成员，重点检查哪些问题…' : '例如：实现导出功能，并运行相关测试。请说明实际修改与验证结果。'} onChange={(event) => update({ prompt: event.target.value })} /><div className="composer-footer"><span>{kind === 'review' ? '审阅保存的实际补丁 · 不自动合入' : '独立 Git 工作区 · 运行记录持续保存'}</span><button className="primary-button" type="submit" disabled={!valid || busy || loading}><Icon name="send" size={14} />{busy ? '正在提交' : kind === 'review' ? '开始独立审阅' : '分配任务'}</button></div></div>
      {selectedKind === 'model' && engine?.id !== 'member_model' && <p className="mission-form-note">此成员是 LLM 角色。本次明确使用 {engine?.label || engine?.id || 'CLI'} 独立执行器，执行器沿用自己的登录与模型；可在群成员中添加对应的 Codex 或 Claude Code Agent。</p>}
      {!members.length && <p className="mission-form-note">本群只有 Muse 成员。请添加 LLM 审阅者或 Codex / Claude Code 执行 Agent；Muse 任务在群聊中明确选择后派发。</p>}
      {kind === 'review' && source?.member_id === memberId && <p className="mission-form-note">请选择另一位成员独立审阅，执行者不能审阅自己的改动。</p>}
      {!available.length && !enginesLoading && <p className="mission-form-note">{kind === 'review' ? '请检查成员模型的本地网关连接，或在 Mac 登录可用的 CLI 执行引擎。' : '请在 Mac 检查 Codex 或 Claude Code 的安装、支持能力与登录状态，然后重新连接。沿用各自 CLI 已有登录。'}</p>}
      {enginesLoading && <p className="group-composer-note" role="status">正在检测本机 Agent 执行引擎…</p>}
      {engine?.error && <p className="mission-form-note">{engine.error}</p>}
      {engine && <details className="group-engine-explanation"><summary>执行引擎与权限说明</summary><p className="group-composer-note">{engine.id === 'member_model' ? `使用 ${selectedMember?.name || '当前成员'} 的 ${selectedMember?.model || '已保存模型'} 审阅固定补丁，不执行代码或工具。` : engine.id === 'codex' && /^(gpt-|o[1-9])[A-Za-z0-9._-]+$/.test(selectedMember?.model || '') ? `Codex 将使用成员模型 ${selectedMember.model}，沿用本机 Codex 登录。` : `${engine.label || engine.id} 使用自己的 CLI 模型与登录；群聊成员负责本次分工。`}{engine.limitation || ''}</p></details>}
    </form>
  </div>;
}
