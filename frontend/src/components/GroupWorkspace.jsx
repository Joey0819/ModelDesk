import { useCallback, useEffect, useRef, useState } from 'react';
import { groupApi } from '../group-api';
import { canUseMuseReply, mergeMuseTasks, MUSE_STATUS, safeMuseThreadUrl } from '../muse-state';
import { museApi } from '../muse-api';
import { groupActive, groupDraftKey, groupMemberColor, groupMessages, groupRecipients, groupTimeline, groupMessageRequest, groupScopeKey, readGroupDraft, readGroupSelection, scopedRooms } from '../group-state';
import { contextErrors } from '../platform-settings';
import { attachmentIssues } from '../attachment-files';
import { useProjects } from '../project-context';
import useGroupRoom from '../hooks/useGroupRoom';
import { Icon, Markdown, ResultProvenance } from './WorkbenchResults';
import { CopyTextButton } from './ConversationActions';
import MissionTeamPicker from './MissionTeamPicker';
import AttachmentComposer from './AttachmentComposer';
import ContextAttachments from './ContextAttachments';
import JobAttachments from './JobAttachments';
import GroupAgentTasks from './GroupAgentTasks';
import './GroupWorkspace.css';

const statuses = { queued: '等待发言', running: '正在讨论', complete: '本轮已完成', error: '本轮失败', cancelled: '已停止', interrupted: '已中断' };
const dateLabel = (date) => date ? new Date(date).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '';

export function GroupMuseTurn({ task, projectId, roomId, onUseReply, onOpenMuse }) {
  const usable = canUseMuseReply(task, projectId, roomId);
  return <article className="group-room-turn group-muse-turn" aria-label={`Muse 群聊记录 ${dateLabel(task.created_at)}`}>
    <div className="group-user-message"><header><strong>你</strong><time>{dateLabel(task.created_at)}</time><CopyTextButton text={task.prompt || ''} label="复制交给 Muse 的任务" /></header><div className="group-turn-recipients"><span>@Muse</span></div><Markdown>{task.prompt || ''}</Markdown></div>
    <section className="group-member-message" style={{ '--member-color': '#946028' }}><span className="group-member-avatar" aria-hidden="true">M</span><div><header><strong>Muse</strong><small>Mac 本机桥接</small><small>{MUSE_STATUS[task.status] || task.status}</small></header>{task.response ? <Markdown>{task.response}</Markdown> : <p className="result-note">{task.monitoring ? '正在等待 Muse 实际回复…' : '尚未读取到实际回复。'}</p>}{task.error && <p className="output-error">{task.error}</p>}{task.response && !usable && <p className="result-note">回复尚未确认完整，保留原始状态。</p>}<div className="group-message-actions"><CopyTextButton text={task.response || ''} label="复制 Muse 的发言" />{usable && <button className="subtle-button" type="button" onClick={() => onUseReply(task)}>引用回复继续讨论</button>}<button className="subtle-button" type="button" onClick={onOpenMuse}>查看 Muse 任务</button></div><details className="group-turn-record"><summary>来源与交接记录</summary><p>此任务单独发送给 Muse，群聊历史和附件未自动传入。</p><p>{task.verification || '来自原生窗口实际读取；外部动作是否成功仍需核查。'}</p>{safeMuseThreadUrl(task.thread_url) && <a href={safeMuseThreadUrl(task.thread_url)} target="_blank" rel="noopener noreferrer">打开原始 Muse 会话 ↗</a>}</details></div></section>
  </article>;
}

export function GroupTurn({ job, room, onOpenArtifacts }) {
  const messages = groupMessages(job, room);
  const recipients = groupRecipients(job, room);
  return <article className="group-room-turn" aria-label={`群聊记录 ${dateLabel(job.created_at)}`}>
    <div className="group-user-message"><header><strong>你</strong><time>{dateLabel(job.created_at)}</time><CopyTextButton text={job.prompt} label="复制这条提问" /></header><div className="group-turn-recipients">{recipients.map((member) => <span key={member.id}>@{member.name}</span>)}</div><Markdown>{job.prompt}</Markdown><JobAttachments job={job} />{job.group_muse_source && <details className="group-muse-provenance"><summary>引用了 Muse 实际回复</summary><p>{job.group_muse_source.prompt}</p>{safeMuseThreadUrl(job.group_muse_source.thread_url) && <a href={safeMuseThreadUrl(job.group_muse_source.thread_url)} target="_blank" rel="noopener noreferrer">打开原始 Muse 会话 ↗</a>}<Markdown>{job.group_muse_source.response}</Markdown>{job.group_muse_source.truncated && <p>引用超过保存预算，完整回复保留在原始 Muse 任务中。</p>}<p>{job.group_muse_source.verification}</p><CopyTextButton text={job.group_muse_source.response} label="复制引用的 Muse 回复" /></details>}</div>
    {messages.map((message, index) => <section className="group-member-message" style={{ '--member-color': groupMemberColor(message.member_id || message.model) }} key={`${message.member_id || message.model}-${message.round}-${index}`}><span className="group-member-avatar" aria-hidden="true">{message.member_name?.slice(0, 1) || 'A'}</span><div><header><strong>{message.member_name}</strong><small>{message.model}</small>{message.round > 1 && <small>第 {message.round} 轮</small>}</header>{message.error && <p className="output-error">发言失败：{message.error}</p>}{message.content && <Markdown>{message.content}</Markdown>}{message.incomplete && <p className="result-note">发言达到输出长度限制，内容可能不完整。</p>}<div className="group-message-actions"><CopyTextButton text={message.content || ''} label={`复制 ${message.member_name} 的发言`} /><button className="subtle-button" type="button" onClick={() => onOpenArtifacts(job, true)}><Icon name="panel" size={14} />查看本轮成果</button></div></div></section>)}
    {groupActive(job) && <p className="group-turn-running" role="status"><span className="small-spinner" />成员正在依次阅读上下文并发言…</p>}
    {job.error && <p className="output-error" role="alert">{job.error}</p>}
    {!groupActive(job) && !messages.length && <p className="result-note">本轮没有收到模型发言。记录已保存，可以继续发送消息。</p>}
    <details className="group-turn-record"><summary>{job.result?.partial_failure ? '本轮部分发言失败' : statuses[job.status] || job.status} · 来源与运行记录</summary><ResultProvenance job={job} />{job.context_history && <p>本轮实际引用 {job.context_history.used_turns ?? 0} 轮会话历史；引用受上下文预算限制。</p>}<ul>{(job.events || []).filter((event) => ['log', 'error', 'tool_result'].includes(event.type)).map((event, index) => <li key={event.seq || index}>{event.message || event.text || `工具：${event.tool}`}</li>)}</ul></details>
  </article>;
}

function GroupTeamSettings({ room, models, catalog, addRequested, onOpenAgentSettings, onApplied, onClose }) {
  const dialog = useRef(null);
  const [team, setTeam] = useState(room.team_snapshot);
  const [pending, setPending] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const chooseTeam = useCallback((value) => setTeam(value), []);
  useEffect(() => { dialog.current?.showModal(); }, []);
  const apply = async () => {
    if (pending || saving || !team?.id) return;
    setSaving(true); setError('');
    try { const updated = await groupApi.updateTeam(room.id, room.project_id, { team_id: team.id, expected_revision: room.team_revision }); await onApplied(updated); onClose(); }
    catch (failure) { setError(failure.message); }
    finally { setSaving(false); }
  };
  return <dialog ref={dialog} className="group-members-dialog" aria-labelledby="group-members-heading" onCancel={(event) => { if (saving) event.preventDefault(); else onClose(); }}>
    <header><div><h2 id="group-members-heading">管理群成员</h2><p>{room.title} · 选择模型，命名 Agent，设置职责</p></div><button type="button" className="icon-button" aria-label="关闭群成员设置" disabled={saving} onClick={onClose}><Icon name="close" size={18} /></button></header>
    <div className="group-members-dialog-body"><MissionTeamPicker projectId={room.project_id} team={team} models={models} catalog={catalog} onChange={chooseTeam} onPending={setPending} onOpenAgentSettings={onOpenAgentSettings} editSignal={addRequested ? 1 : 0} requireSaved />
      <p className="group-members-help">先保存协作组，再应用到本群。其他群聊继续使用各自的成员；过去的发言、任务和审阅保留原成员身份。正在执行的任务完成或停止后才可应用。</p>
      {error && <p className="output-error" role="alert">{error}</p>}
    </div>
    <footer><button type="button" className="subtle-button" disabled={saving} onClick={onClose}>取消</button><button type="button" className="primary-button" disabled={saving || pending || !team?.id} onClick={apply}>{saving ? '正在应用' : '应用到当前群聊'}</button></footer>
  </dialog>;
}

function GroupRoom({ roomId, projectId, status, models, context, onContextChange, onOpenArtifacts, onClearArtifacts, onOpenProjects, onOpenAgentSettings, renderIntegration }) {
  const { assets, transfers } = useProjects();
  const [workMode, setWorkMode] = useState(() => { const value = localStorage.getItem(`modeldesk.group.mode.${projectId || 'none'}.${roomId}`); return ['code', 'review'].includes(value) ? value : 'discussion'; });
  useEffect(() => { localStorage.setItem(`modeldesk.group.mode.${projectId || 'none'}.${roomId}`, workMode); }, [workMode, projectId, roomId]);
  const [saved] = useState(() => readGroupDraft(localStorage, projectId, roomId));
  const [prompt, setPrompt] = useState(saved.prompt);
  const [recipientIds, setRecipientIds] = useState(saved.recipientIds);
  const [contextTurns, setContextTurns] = useState(saved.contextTurns);
  const [attachmentPending, setAttachmentPending] = useState(false);
  const [settings, setSettings] = useState(false);
  const [showLatest, setShowLatest] = useState(false);
  const [selectedArtifactId, setSelectedArtifactId] = useState(null);
  const [memberSettings, setMemberSettings] = useState(null);
  const [museTasks, setMuseTasks] = useState([]);
  const [museHistoryError, setMuseHistoryError] = useState('');
  const museMonitoring = museTasks.some((task) => task.monitoring);
  useEffect(() => {
    let disposed = false;
    const refresh = async () => {
      try { const tasks = await museApi.list(projectId, roomId); if (!disposed) { setMuseTasks((previous) => mergeMuseTasks(previous, tasks, projectId, roomId)); setMuseHistoryError(''); } }
      catch (failure) { if (!disposed) setMuseHistoryError(failure.message); }
    };
    refresh(); const timer = setInterval(refresh, museMonitoring ? 2500 : 12000);
    return () => { disposed = true; clearInterval(timer); };
  }, [projectId, roomId, museMonitoring]);
  const integrationRef = useRef(null);
  const roomState = useGroupRoom(projectId, roomId);
  const attachmentRef = useRef(null);
  const timelineRef = useRef(null);
  const nearBottom = useRef(true);
  const olderPosition = useRef(null);
  const { room, jobs, activeJob, loading, busy, error } = roomState;
  const lastJob = jobs.at(-1);
  const artifactJob = jobs.find((job) => job.id === selectedArtifactId) || lastJob;
  const openTurnArtifact = (job, reveal) => { setSelectedArtifactId(job.id); onOpenArtifacts(job, reveal); };
  const members = room?.team_snapshot?.members || [];
  const selectedMembers = recipientIds.length ? members.filter((member) => recipientIds.includes(member.id)) : members;
  useEffect(() => { onContextChange(saved.context); onClearArtifacts(); }, [saved, onContextChange, onClearArtifacts]);
  useEffect(() => {
    if ((context.project_id || null) === (projectId || null)) localStorage.setItem(groupDraftKey(projectId, roomId), JSON.stringify({ prompt, recipientIds, contextTurns, context }));
  }, [prompt, recipientIds, contextTurns, context, projectId, roomId]);
  useEffect(() => { if (workMode === 'discussion' && artifactJob) onOpenArtifacts(artifactJob, false); }, [artifactJob, onOpenArtifacts, workMode]);
  useEffect(() => { if (workMode !== 'discussion') onClearArtifacts(); }, [workMode, onClearArtifacts]);
  useEffect(() => {
    const element = timelineRef.current;
    if (!element) return;
    if (olderPosition.current) { element.scrollTop = element.scrollHeight - olderPosition.current.height + olderPosition.current.top; olderPosition.current = null; }
    else if (nearBottom.current) element.scrollTop = element.scrollHeight;
  }, [jobs, museTasks]);
  const errors = [
    ...contextErrors(context), ...attachmentIssues(assets, context.attachment_ids, context.include_project_assets),
    ...((context.project_id || null) !== (projectId || null) ? ['正在切换群聊资料。'] : []),
    ...(recipientIds.some((id) => !members.some((member) => member.id === id)) ? ['部分收件成员不在这个群聊，请重新选择。'] : []),
    ...(status && selectedMembers.some((member) => !status.models?.some((model) => model.id === member.model)) ? ['指定成员的模型当前未接入，请选择可用成员或检查“Agent 与连接”。'] : []),
    ...(!status ? ['正在读取模型连接状态。'] : []),
  ];
  const canSend = room && prompt.trim() && !errors.length && !loading && !busy && !activeJob && !attachmentPending && !transfers;
  const send = async (event) => {
    event.preventDefault(); if (!canSend) return;
    const sentPrompt = prompt;
    const job = await roomState.send(groupMessageRequest({ prompt, recipientIds, context, contextTurns }));
    if (job) { setPrompt((current) => current === sentPrompt ? '' : current); onContextChange((current) => ({ ...current, attachment_ids: current.attachment_ids.filter((id) => !context.attachment_ids.includes(id)), ...(current.muse_task_id === context.muse_task_id ? { muse_task_id: null, muse_task_label: '' } : {}) })); setSelectedArtifactId(job.id); nearBottom.current = true; setShowLatest(false); }
  };
  const useMuseReply = (task) => { if (canUseMuseReply(task, projectId, roomId)) onContextChange((current) => ({ ...current, muse_task_id: task.id, muse_task_label: task.prompt?.slice(0, 100) || 'Muse 实际回复' })); };
  const openMuse = () => { setWorkMode('discussion'); requestAnimationFrame(() => { const panel = integrationRef.current; const details = panel?.querySelector('details'); if (details) details.open = true; panel?.scrollIntoView({ block: 'nearest' }); details?.querySelector('summary')?.focus(); }); };
  const applyMembers = async (updated) => { setRecipientIds((previous) => previous.filter((id) => updated.team_snapshot.members.some((member) => member.id === id))); await roomState.refresh(); };
  const scrollToLatest = () => { const element = timelineRef.current; if (element) element.scrollTop = element.scrollHeight; nearBottom.current = true; setShowLatest(false); };
  const loadOlder = async () => {
    const element = timelineRef.current;
    if (element) olderPosition.current = { height: element.scrollHeight, top: element.scrollTop };
    const loaded = await roomState.loadOlder();
    if (!loaded) olderPosition.current = null;
  };
  return <>
    {room && <div className="group-member-toolbar"><span><Icon name="group" size={15} />{members.length} 名模型 Agent</span><button type="button" className="subtle-button" onClick={() => setMemberSettings({ room, add: true })}><Icon name="plus" size={14} />添加 Agent</button><button type="button" className="subtle-button" onClick={() => setMemberSettings({ room, add: false })}><Icon name="settings" size={14} />管理成员</button>{renderIntegration && <button type="button" className="subtle-button" onClick={openMuse}><span className="group-muse-mark">M</span>交给 Muse</button>}</div>}
    {room && <details className="group-roster"><summary><Icon name="group" size={16} /><strong>{room.team_snapshot.name}</strong><span>{members.length} 名成员 · 群内共享上下文</span><Icon name="chevron" size={13} /></summary><div>{members.map((member) => <article key={member.id}><strong>{member.name}</strong><small>{status?.models?.find((model) => model.id === member.model)?.label || member.model}</small><p>{member.role}</p></article>)}</div><p>成员变更需在“管理成员”中保存并应用。发送前可以指定一名或多名成员回复；Muse 通过本机桥接单独交接任务。</p></details>}
    {memberSettings && <GroupTeamSettings room={memberSettings.room} models={models} catalog={status?.models || []} addRequested={memberSettings.add} onOpenAgentSettings={onOpenAgentSettings} onApplied={applyMembers} onClose={() => setMemberSettings(null)} />}
    <nav className="group-work-mode" aria-label="群聊协作方式">{[['discussion', '持续讨论', 'chat'], ['code', '执行任务', 'tasks'], ['review', '独立审阅', 'council']].map(([id, label, icon]) => <button key={id} type="button" className={workMode === id ? 'selected' : ''} aria-current={workMode === id ? 'page' : undefined} onClick={() => setWorkMode(id)}><Icon name={icon} size={15} />{label}</button>)}</nav>
    {workMode !== 'discussion' && room ? <GroupAgentTasks projectId={projectId} room={room} kind={workMode} onKindChange={setWorkMode} onOpenProjects={onOpenProjects} onOpenArtifacts={onOpenArtifacts} /> : <>
    {renderIntegration && <div ref={integrationRef} className="group-native-integration">{renderIntegration({ projectId, roomId, onUseReply: useMuseReply })}</div>}
    <div className="group-room-timeline" ref={timelineRef} onScroll={(event) => { const element = event.currentTarget; nearBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 100; setShowLatest(!nearBottom.current); }}>
      {error && <div className="error-banner" role="alert">{error}<button className="subtle-button" type="button" onClick={roomState.refresh}>重新连接</button></div>}
      {loading ? <p className="mission-loading" role="status"><span className="small-spinner" />正在读取群聊记录…</p> : <>
        {roomState.before && <button className="group-load-older subtle-button" type="button" disabled={roomState.olderLoading} onClick={loadOlder}>{roomState.olderLoading ? '正在加载' : '查看更早的消息'}</button>}
        {!jobs.length && !museTasks.length && room && <div className="group-empty-conversation"><Icon name="group" size={32} /><h2>团队已就位，开始交流吧。</h2><p>向全体成员提问，或点击下方成员指定回复对象。这里的讨论会持续保存，随时回来接着聊。</p></div>}
        {museHistoryError && <p className="result-note" role="status">Muse 历史暂未同步：{museHistoryError}</p>}
        {groupTimeline(jobs, museTasks, projectId, roomId).map(({ type, value }) => type === 'muse' ? <GroupMuseTurn key={`muse-${value.id}`} task={value} projectId={projectId} roomId={roomId} onUseReply={useMuseReply} onOpenMuse={openMuse} /> : <GroupTurn key={value.id} job={value} room={room || { team_snapshot: { members: [] } }} onOpenArtifacts={openTurnArtifact} />)}
      </>}
    </div>
    <div className="group-room-compose">
      {showLatest && <button type="button" className="group-jump-latest subtle-button" onClick={scrollToLatest}>回到最新消息 ↓</button>}
      {roomState.streamError && activeJob && <p className="stream-warning">实时连接暂时中断，正在通过后台记录同步。</p>}
      {context.muse_task_id && <div className="group-muse-source-chip"><strong>Muse 回复待引用</strong><span>{context.muse_task_label || "已选定本群的已完成回复"}</span><button type="button" className="icon-button" aria-label="移除 Muse 回复引用" onClick={() => onContextChange((current) => ({ ...current, muse_task_id: null, muse_task_label: "" }))}><Icon name="close" size={14} /></button></div>}
      <div className="group-recipient-picker" role="group" aria-label="选择回复成员"><span>发送给</span><button type="button" aria-pressed={!recipientIds.length} className={!recipientIds.length ? 'selected' : ''} onClick={() => setRecipientIds([])}>@所有成员</button>{members.map((member) => <button type="button" key={member.id} aria-pressed={recipientIds.includes(member.id)} className={recipientIds.includes(member.id) ? 'selected' : ''} onClick={() => setRecipientIds((previous) => previous.includes(member.id) ? previous.filter((id) => id !== member.id) : [...previous, member.id])}>@{member.name}</button>)}{renderIntegration && <button type="button" className="group-muse-recipient" title="打开 Muse 原生任务交接，单独填写并发送" onClick={openMuse}>@Muse ↗</button>}</div>
      <form className="composer group-composer" onSubmit={send} onDragOver={(event) => { if (event.dataTransfer.types.includes('Files')) event.preventDefault(); }} onDrop={(event) => attachmentRef.current?.handleDrop(event)}>
        <AttachmentComposer ref={attachmentRef} attachmentIds={context.attachment_ids} onChange={(ids) => onContextChange({ ...context, attachment_ids: ids })} includeShared={context.include_project_assets} onIncludeShared={(include) => onContextChange({ ...context, include_project_assets: include })} disabled={busy || Boolean(activeJob)} onPendingChange={setAttachmentPending} />
        <textarea aria-label="群聊消息" maxLength={12000} value={prompt} onChange={(event) => setPrompt(event.target.value)} onPaste={(event) => attachmentRef.current?.handlePaste(event)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); send(event); } }} rows={3} placeholder={recipientIds.length ? `请 ${selectedMembers.map((member) => member.name).join('、')} 接着讨论…` : '给协作组发消息，继续当前项目的讨论…'} />
        <div className="composer-footer"><button className="subtle-button" type="button" aria-expanded={settings} onClick={() => setSettings(!settings)}><Icon name="memory" size={14} />上下文与工具</button><span>{activeJob ? '可先写下一条消息' : 'Enter 发送 · Shift + Enter 换行'}</span>{activeJob ? <button className="stop-button" type="button" disabled={busy} onClick={roomState.cancel}><Icon name="stop" size={15} />停止本轮</button> : <button className="primary-button" type="submit" disabled={!canSend}><Icon name="send" size={15} />{busy ? '正在发送' : '发送消息'}</button>}</div>
      </form>
      {(errors.length > 0 || attachmentPending || transfers > 0) && <p className="mission-form-note">{attachmentPending || transfers > 0 ? '请等待资料准备完成。' : errors[0]}</p>}
      {settings && <section className="group-message-settings"><label>最近对话上下文<select aria-label="群聊上下文轮数" value={contextTurns} onChange={(event) => setContextTurns(Number(event.target.value))}>{[1, 4, 6, 8, 12].map((count) => <option key={count} value={count}>{count} 轮</option>)}</select></label><p>仅引用当前群聊的实际记录；过长内容按模型上下文预算截断。发送对象以上方选择为准。</p><ContextAttachments mode="group" value={context} onChange={onContextChange} jobs={jobs} disabled={busy || Boolean(activeJob)} onOpenProjects={onOpenProjects} /></section>}
      <p className="group-composer-note">本群记录与草稿自动保存 · 本轮 {selectedMembers.length} 名成员回复 · 不同群聊分别记忆</p>
    </div>
    </>}
  </>;
}

export default function GroupWorkspace({ initialRoomId, initialTeam, renderIntegration, status, models, context, onContextChange, onOpenNavigation, onOpenProjects, onOpenAgentSettings, onOpenArtifacts, onClearArtifacts, onToggleArtifacts }) {
  const { activeProjectId, activeProject } = useProjects();
  const initialProjectTeam = initialTeam?.id && (initialTeam.project_id || null) === activeProjectId ? initialTeam : null;
  const [roomId, setRoomId] = useState(() => initialProjectTeam ? null : initialRoomId || readGroupSelection(localStorage, activeProjectId));
  const [rooms, setRooms] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [creating, setCreating] = useState(false);
  const [team, setTeam] = useState(initialProjectTeam);
  const [teamPending, setTeamPending] = useState(false);
  const [title, setTitle] = useState(initialProjectTeam ? `${initialProjectTeam.name}项目群` : '项目协作群');
  const alive = useRef(true);
  const chooseTeam = useCallback((next) => setTeam(next), []);
  const refresh = useCallback(async () => {
    try { const result = await groupApi.list(activeProjectId); if (alive.current) { setRooms(scopedRooms(result, activeProjectId)); setError(''); } }
    catch (failure) { if (alive.current) setError(failure.message); }
    finally { if (alive.current) setLoading(false); }
  }, [activeProjectId]);
  useEffect(() => { alive.current = true; refresh(); const timer = setInterval(refresh, 15000); return () => { alive.current = false; clearInterval(timer); }; }, [refresh]);
  useEffect(() => { localStorage.setItem(groupScopeKey(activeProjectId), JSON.stringify({ roomId })); }, [activeProjectId, roomId]);
  const selectRoom = (id) => { onClearArtifacts(); setRoomId(id); };
  const createRoom = async (event) => {
    event.preventDefault(); if (!team?.id || !title.trim() || teamPending || creating) return;
    setCreating(true); setError('');
    try {
      const room = await groupApi.create({ title: title.trim(), project_id: activeProjectId || null, team_id: team.id });
      if (alive.current) { setRooms((previous) => scopedRooms([room, ...previous], activeProjectId)); selectRoom(room.id); }
    } catch (failure) { if (alive.current) setError(failure.message); }
    finally { if (alive.current) setCreating(false); }
  };
  const selectedRoom = rooms.find((room) => room.id === roomId);
  return <main className="workspace group-workspace">
    <header className="workspace-header"><div className="header-title"><button className="mobile-menu icon-button" aria-label="打开导航" onClick={onOpenNavigation}><Icon name="menu" /></button><span className="mode-header-icon"><Icon name="group" size={22} /></span><div><h1 title={selectedRoom?.title}>{selectedRoom?.title || '项目群聊'}</h1><span>{activeProject?.name || '独立项目'} · 和不同模型持续协作</span></div></div><div className="mission-header-actions"><button className="subtle-button" type="button" onClick={() => selectRoom(null)}><Icon name="plus" size={15} />新建群聊</button>{roomId && <button className="subtle-button" type="button" onClick={onToggleArtifacts}><Icon name="panel" size={15} />成果</button>}</div></header>
    <div className="group-room-switcher"><Icon name="chat" size={16} /><select aria-label="打开项目群聊" value={roomId || ''} onChange={(event) => selectRoom(event.target.value || null)}><option value="">新建群聊</option>{roomId && !selectedRoom && <option value={roomId}>正在读取已保存群聊</option>}{rooms.map((room) => <option key={room.id} value={room.id}>{room.title} · {room.team_snapshot?.name || '协作组'}</option>)}</select><span>{rooms.length} 个群聊</span></div>
    {error && <div className="error-banner" role="alert">{error}<button type="button" className="subtle-button" onClick={refresh}>重试</button></div>}
    {roomId ? <GroupRoom key={`${activeProjectId || 'unassigned'}:${roomId}`} roomId={roomId} projectId={activeProjectId} status={status} models={models} onOpenAgentSettings={onOpenAgentSettings} context={context} onContextChange={onContextChange} onOpenArtifacts={onOpenArtifacts} onClearArtifacts={onClearArtifacts} onOpenProjects={onOpenProjects} renderIntegration={renderIntegration} /> : <div className="group-create-scroll"><form className="group-create-room" onSubmit={createRoom}><div className="mission-introduction"><h2>一个项目，多个协作群。</h2><p>把不同模型组成研究组、代码审查组或内容团队。成员有自己的职责，在同一个群里持续交流。</p></div><label className="group-title-input"><span>群聊名称</span><input aria-label="群聊名称" value={title} maxLength={100} placeholder="例如：产品方案讨论、发布前审查" onChange={(event) => setTitle(event.target.value)} /></label><MissionTeamPicker projectId={activeProjectId} team={team} models={models} catalog={status?.models || []} onChange={chooseTeam} onPending={setTeamPending} onOpenAgentSettings={onOpenAgentSettings} requireSaved /><div className="group-create-actions"><p>{team?.id ? '建群后会保存当前成员与分工，已有讨论不受后续编辑影响。' : '选择已有协作组，或新建并保存协作组后开始。'}</p><button type="submit" className="primary-button" disabled={creating || loading || !title.trim() || !team?.id || teamPending}><Icon name="plus" size={15} />{creating ? '正在创建' : '创建群聊'}</button></div></form></div>}
  </main>;
}
