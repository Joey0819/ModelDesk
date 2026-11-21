import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { workbenchApi } from '../api';
import { API_BASE, REMOTE_CLIENT } from '../api-base';
import { artifactCategory, deriveArtifacts, deriveRoomArtifacts } from '../artifacts';
import { groupApi } from '../group-api';
import { feishuApi } from '../feishu-api';
import { groupTaskApi } from '../group-task-api';
import { harnessApi } from '../harness-api';
import { temporaryMembers, teamRequest } from '../mission-teams';
import { roleModelName } from '../model-catalog-state';
import { TASK_LABELS, taskActive, taskDraftKey, taskEngineLabel, readTaskDraft, withFetchedPatch } from '../group-task-state';
import {
  MUSE_STATUS,
  canUseMuseReply,
  mergeMuseTasks,
  museReadiness,
  normalizeMuseSource,
  safeMuseThreadUrl,
} from '../muse-state';
import { resolveMuseVideoUrl } from '../muse-video';
import { museApi } from '../muse-api';
import {
  GROUP_HARNESS_MODES,
  GROUP_QUICK_RECIPES,
  GROUP_DELIVERY_STATUS,
  GROUP_DELIVERY_STEP_STATUS,
  applyMentionCandidate,
  buildMuseHandoffPrompt,
  buildGroupRuntimePrompt,
  detectComposerTrigger,
  detectMessageHandoffIntent,
  filterComposerSuggestions,
  formatQuoteReply,
  groupMemberKind,
  groupMemberKindLabel,
  groupSelection,
  groupRuntimeRequest,
  extractGroupDeliverable,
  groupActive,
  groupDeliveryMode,
  groupDispatchIntentMatches,
  groupDraftKey,
  groupMemberColor,
  groupMessages,
  groupRecipients,
  groupTimeline,
  groupMessageRequest,
  groupScopeKey,
  groupSubmissionMatches,
  mergeRoomJobs,
  readGroupDraft,
  readGroupSelection,
  scopedRooms,
} from '../group-state';
import { contextErrors } from '../platform-settings';
import { attachmentIssues } from '../attachment-files';
import { useProjects } from '../project-context';
import useGroupRoom from '../hooks/useGroupRoom';
import useGroupDispatch from '../hooks/useGroupDispatch';
import { Icon, Markdown, AgentOutput, ResultProvenance } from './WorkbenchResults';
import { CopyTextButton } from './ConversationActions';
import MissionTeamPicker from './MissionTeamPicker';
import AttachmentComposer from './AttachmentComposer';
import ContextAttachments from './ContextAttachments';
import TaskContextControl from './TaskContextControl';
import ProjectMemoryDialog from './ProjectMemoryDialog';
import McpTaskControls from './McpTaskControls';
import { BackgroundDelegateSetting } from './AutonomyTaskCenter';
import HandoffReceipt from './HandoffReceipt';
import './TaskContextControl.css';
import JobAttachments from './JobAttachments';
import GroupAgentTasks from './GroupAgentTasks';
import GroupRoutines from './GroupRoutines';
import GroupFeishuDialog from './GroupFeishuDialog';
import DeliveryOutputOptions from './DeliveryOutputOptions';
import { machineInfo, hostIsMacos } from '../machine';
import DocumentOutputOptions from './DocumentOutputOptions';
import { DeliveryMediaProgress } from './MediaDelivery';
import './GroupWorkspace.css';

const statuses = { queued: '等待发言', running: '正在讨论', complete: '本轮已完成', error: '本轮失败', cancelled: '已停止', interrupted: '已中断' };
const dateLabel = (date) => date ? new Date(date).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '';
const workModes = [['discussion', '群聊与协同', 'chat'], ['code', '代码 Agent', 'tasks'], ['review', '补丁审阅', 'council']];
const compactModeLabels = { discussion: '群聊', code: '代码', review: '审阅' };

function GroupTaskPrompt({ task, name, engine, copyLabel }) {
  const automatic = Boolean(task.parent_job_id);
  return <div className={`group-user-message${automatic ? ' group-auto-task-prompt' : ''}`}>
    <header><strong>{automatic ? '自动分配' : '你'}</strong><time>{dateLabel(task.created_at)}</time><CopyTextButton text={task.prompt || ''} label={copyLabel} /></header>
    <div className="group-turn-recipients"><span>@{name}</span>{engine && <span>{engine}</span>}</div>
    {automatic ? <details className="group-task-prompt-details"><summary>查看任务说明与引用上下文</summary><pre>{task.prompt || ''}</pre></details> : <Markdown>{task.prompt || ''}</Markdown>}
  </div>;
}

export function GroupMuseTurn({ task: rawTask, projectId, roomId, onUseReply, onOpenMuse, onRetryMuse, onOpenArtifacts }) {
  const task = normalizeMuseSource(rawTask);
  const usable = canUseMuseReply(task, projectId, roomId);
  const videoUrl = resolveMuseVideoUrl(task);
  return <article className="group-room-turn group-muse-turn" aria-label={`Muse 群聊记录 ${dateLabel(task.created_at)}`}>
    <GroupTaskPrompt task={task} name="Muse" copyLabel="复制交给 Muse 的任务" />
    <section className="group-member-message" style={{ '--member-color': '#946028' }}><span className="group-member-avatar" aria-hidden="true">M</span><div><header><strong>{task.member?.name || 'Muse'}</strong><small>{task.harness_generated ? '旧版未验证内容' : 'Muse 本机桥接'}</small><small>{MUSE_STATUS[task.status] || task.status}</small></header>
      {videoUrl && (
        <div className="muse-video-card" aria-label="Muse 已回传视频成片">
          <div className="muse-video-header">
            <span><strong>Muse 返回的视频链接</strong></span>
            <div className="muse-video-header-actions">
              {onOpenArtifacts && <button type="button" className="subtle-button" onClick={() => onOpenArtifacts({ ...task, video_url: videoUrl, mode: 'muse_task' }, true)}><Icon name="panel" size={13} />在成果栏预览</button>}
              <a className="primary-button muse-video-download" href={videoUrl} download>下载原始视频</a>
            </div>
          </div>
          <video className="muse-inline-video" src={videoUrl} controls playsInline preload="metadata" loop />
        </div>
      )}
      {task.response ? <Markdown>{task.response}</Markdown> : <p className="result-note">{task.monitoring ? '正在等待 Muse 窗口中的实际回复…' : '尚未读取到实际回复。'}</p>}
      {task.error && <p className="output-error">{task.error}</p>}
      {task.response && !usable && <p className="result-note">回复尚未确认完整，保留原始状态。</p>}
      <div className="group-message-actions">
        <CopyTextButton text={task.response || ''} label="复制 Muse 的发言" />
        {videoUrl && <a className="subtle-button" href={videoUrl} download>下载原始视频</a>}
        {usable && <button className="subtle-button" type="button" onClick={() => onUseReply(task)}>引用回复继续讨论</button>}
        {!usable && !task.monitoring && onRetryMuse && <button className="subtle-button group-inline-muse-btn" type="button" onClick={() => onRetryMuse(task)}>准备重试草稿</button>}
        <button className="subtle-button" type="button" onClick={onOpenMuse}>查看 Muse 任务</button>
      </div>
      <details className="group-turn-record"><summary>来源与交接记录</summary><p>此任务单独交给 Muse 原生应用，时间线保留桥接读取的实际状态与内容。</p><p>{task.verification || '来自原生窗口实际读取；外部动作是否成功仍需核查。'}</p>{safeMuseThreadUrl(task.thread_url) && <a href={safeMuseThreadUrl(task.thread_url)} target="_blank" rel="noopener noreferrer">打开原始 Muse 会话 ↗</a>}</details>
    </div></section>
  </article>;
}

export function GroupAgentTaskTurn({ task, onOpenTaskMode, onUseAgentOutput, onOpenCreative }) {
  const name = task.member?.name || task.member_id || 'Agent';
  return <article className="group-room-turn group-agent-turn" aria-label={`Agent 任务记录 ${dateLabel(task.created_at)}`}>
    <GroupTaskPrompt task={task} name={name} engine={taskEngineLabel(task)} copyLabel="复制交给 Agent 的任务" />
    <section className="group-member-message" style={{ '--member-color': '#2b7284' }}><span className="group-member-avatar" aria-hidden="true">⚙</span><div><header><strong>{name}</strong><small>{taskEngineLabel(task)}</small><small>{TASK_LABELS[task.status] || task.status}</small></header>{taskActive(task) && <p className="result-note"><span className="small-spinner" /> 正在隔离工作区执行…</p>}{task.skill_delivery?.assets?.length > 0 && <p className="result-note">平台已在源码完成后渲染实际视频（{task.skill_delivery.duration_seconds} 秒）。下方是 Agent 写源码时的记录，最终视频请查看本轮成果。</p>}{task.output ? <AgentOutput>{task.output}</AgentOutput> : !taskActive(task) && <p className="result-note">暂无文本输出，可查看补丁与运行记录。</p>}{task.error && <p className="output-error">{task.error}</p>}<div className="group-message-actions">{task.output && <CopyTextButton text={task.output} label={`复制 ${name} 的执行结果`} />}{task.output && onUseAgentOutput && <button className="subtle-button" type="button" onClick={() => onUseAgentOutput(task)}>引用结果继续讨论</button>}{task.kind === 'creative' ? onOpenCreative && <button className="subtle-button" type="button" onClick={onOpenCreative}>查看本轮动画成果</button> : <button className="subtle-button" type="button" onClick={() => onOpenTaskMode(task.kind === 'review' ? 'review' : 'code')}>查看补丁与审批</button>}</div></div></section>
  </article>;
}

export function GroupDeliveryModeSelector({ value, onChange, disabled = false }) {
  return <div className="group-delivery-mode" role="group" aria-label="任务处理方式">
    {[['auto', '自动完成'], ['discussion', '仅讨论']].map(([id, label]) => <button key={id} type="button" aria-pressed={value === id} className={value === id ? 'selected' : ''} disabled={disabled} onClick={() => onChange(id)}>{label}</button>)}
  </div>;
}

export function GroupDeliveryProgress({ job, onOpenArtifacts }) {
  const delivery = job.result?.delivery || {};
  const active = groupActive(job);
  const hasOutput = deriveArtifacts({ ...job, mode: 'group' }, API_BASE).length > 0;
  const stopped = ['cancelled', 'interrupted', 'error'].includes(job.status);
  const state = stopped ? hasOutput ? 'partial' : 'blocked' : delivery.status || (active ? 'planning' : 'blocked');
  const filesAwaitReview = state === 'partial' && delivery.media?.length > 0 && delivery.validation?.mechanical_checks?.length > 0 && delivery.validation.mechanical_checks.every((check) => check.met === true) && delivery.steps?.every((step) => step.status === 'complete');
  const label = filesAwaitReview ? '文件已生成，内容待验收' : job.status === 'cancelled' ? '任务已停止' : job.status === 'interrupted' ? '任务已中断' : GROUP_DELIVERY_STATUS[state];
  const steps = Array.isArray(delivery.steps) ? delivery.steps : [];
  const planner = delivery.workflow?.planner;
  const plannerLabel = { pending: '正在等待 MCP 统筹计划', accepted: 'MCP 统筹计划已通过校验', rejected: 'MCP 统筹计划未通过校验', blocked: 'MCP 统筹未完成', cancelled: 'MCP 统筹已停止' }[planner?.status];
  const limitations = Array.isArray(delivery.limitations) ? delivery.limitations.filter((item) => typeof item === 'string') : [];
  const deliveryTitle = !active && ['blocked', 'partial'].includes(state) && delivery.title === '正在安排协作任务'
    ? '协作在安排阶段中断，可展开下方步骤查看详情'
    : delivery.title;
  return <section className={`group-delivery-progress group-delivery-${state}`} aria-label="自动交付进度" aria-live={active ? 'polite' : 'off'}>
    <header>{active ? <span className="small-spinner" /> : <Icon name="file" size={16} />}<strong>{label || '正在同步交付进度'}</strong>{hasOutput && <button className="subtle-button group-delivery-action" type="button" onClick={() => onOpenArtifacts(job, true)}><Icon name="panel" size={14} />{state === 'complete' ? '查看完整成果' : filesAwaitReview ? '查看文件与验收意见' : '查看部分成果'}</button>}</header>
    {deliveryTitle && <p className="group-delivery-title">{deliveryTitle}</p>}
    {plannerLabel && <p className="group-delivery-title">{plannerLabel}</p>}
    {steps.length > 0 && <details className="group-execution-details"><summary>分工与执行 · {steps.filter((step) => step.status === 'complete').length}/{steps.length} 已完成{steps.some((step) => step.status === 'error') ? ' · 有步骤失败' : ''}</summary><ol>{steps.map((step, index) => <li key={step.id || index} className={`group-delivery-step-${step.status}`}><div><strong>{step.member_name || step.member_id || '协作成员'}</strong><span>{GROUP_DELIVERY_STEP_STATUS[step.status] || '状态待同步'}</span></div>{step.instruction && <p>{step.instruction}</p>}<HandoffReceipt step={step} />{step.kind === 'mcp' && step.task_id && step.task_observation && <McpTaskControls step={step} />}{step.error && <p className="output-error">{step.error}</p>}</li>)}</ol></details>}
    <details className="group-delivery-receipt"><summary>验收与素材{limitations.length > 0 ? ` · ${limitations.length} 条待处理说明` : ''}</summary><DeliveryMediaProgress job={job} />
    {limitations.length > 0 && <ul className="group-delivery-limitations">{limitations.map((limit, index) => <li key={index}>{limit}</li>)}</ul>}</details>
    {!active && !hasOutput && <p>尚未生成完整成果。请查看执行步骤与失败原因；讨论发言保留在下方记录中。</p>}
    {active && !steps.length && <p>正在分析你的要求，安排群内模型与可用 Agent。</p>}
  </section>;
}

export function GroupLiveActivityBar({ activeJob, museTasks = [], agentTasks = [], onScrollToLatest, onOpenMuse, onOpenCodeTasks }) {
  const activeMuse = (Array.isArray(museTasks) ? museTasks : []).filter((task) => task.monitoring || ['queued', 'running'].includes(task.status));
  const activeAgents = (Array.isArray(agentTasks) ? agentTasks : []).filter(taskActive);
  const jobRunning = groupActive(activeJob);
  if (!jobRunning && !activeMuse.length && !activeAgents.length) return null;
  const deliverySteps = Array.isArray(activeJob?.result?.delivery?.steps) ? activeJob.result.delivery.steps : [];
  const runningStep = deliverySteps.find((step) => step.status === 'running');
  return <div className="group-live-activity-bar">
    <div className="group-live-badges" role="status" aria-label="实时协同中">
      <span className="small-spinner" aria-hidden="true" />
      {jobRunning && (
        <span className="group-live-badge group-live-llm">
          {runningStep ? `${runningStep.member_name || '成员'} 正在执行` : groupDeliveryMode(activeJob) === 'auto' ? '自动统筹与交付中' : '群内模型依次发言中'}
        </span>
      )}
      {activeMuse.length > 0 && (
        <button type="button" className="group-live-badge group-live-muse" onClick={onOpenMuse} disabled={!onOpenMuse}>
          Muse 窗口同步中 ({activeMuse.length})
        </button>
      )}
      {activeAgents.length > 0 && (
        <button type="button" className="group-live-badge group-live-agent" onClick={() => onOpenCodeTasks(activeAgents[0].kind === 'review' ? 'review' : 'code')} disabled={!onOpenCodeTasks}>
          代码 Agent 运行中 ({activeAgents.length})
        </button>
      )}
    </div>
    <div className="group-live-actions">
      {onScrollToLatest && <button type="button" className="subtle-button" onClick={onScrollToLatest}>定位进度 ↓</button>}
    </div>
  </div>;
}

export function TaskPrompt({ text = '' }) {
  if (text.length <= 360) return <Markdown>{text}</Markdown>;
  return <details className="group-original-prompt"><summary><strong>任务要求</strong><span>{text.replace(/\s+/g, ' ').slice(0, 160)}…</span><small>展开完整要求 · {text.length} 字符</small></summary><Markdown>{text}</Markdown></details>;
}

export function GroupTurn({ job, room, onOpenArtifacts, onSendToMuse, onHandOffToAgent, onQuoteReply }) {
  const messages = groupMessages(job, room);
  const recipients = groupRecipients(job, room);
  const automatic = groupDeliveryMode(job) === 'auto';
  const MessageContainer = automatic ? 'details' : 'div';
  const hasDiscussionOutput = !automatic && deriveArtifacts({ ...job, mode: 'group' }).length > 0;
  return <article className="group-room-turn" data-group-job={job.id} tabIndex={-1} aria-label={`群聊记录 ${dateLabel(job.created_at)}`}>
    <div className="group-user-message"><header><strong>你</strong><time>{dateLabel(job.created_at)}</time><CopyTextButton text={job.prompt} label="复制这条提问" /></header><div className="group-turn-recipients">{recipients.map((member) => <span key={member.id}>@{member.name}</span>)}{job.rounds > 1 && <span>共 {job.rounds} 轮深研</span>}</div><TaskPrompt text={job.prompt} /><JobAttachments job={job} />{job.group_muse_source && <details className="group-muse-provenance"><summary>引用了 Muse 实际回复</summary><p>{job.group_muse_source.prompt}</p>{safeMuseThreadUrl(job.group_muse_source.thread_url) && <a href={safeMuseThreadUrl(job.group_muse_source.thread_url)} target="_blank" rel="noopener noreferrer">打开原始 Muse 会话 ↗</a>}<Markdown>{job.group_muse_source.response}</Markdown>{job.group_muse_source.truncated && <p>引用超过保存预算，完整回复保留在原始 Muse 任务中。</p>}<p>{job.group_muse_source.verification}</p><CopyTextButton text={job.group_muse_source.response} label="复制引用的 Muse 回复" /></details>}</div>
    {automatic && <GroupDeliveryProgress job={job} onOpenArtifacts={onOpenArtifacts} />}
    {messages.length > 0 && <MessageContainer className={automatic ? "group-delivery-discussion" : "group-discussion-messages"}>
    {automatic && <summary>查看讨论与规划记录（{messages.length} 条）</summary>}
    {messages.map((message, index) => {
      const handoff = detectMessageHandoffIntent(message.content || '');
      return <section className="group-member-message" style={{ '--member-color': groupMemberColor(message.member_id || message.model) }} key={`${message.member_id || message.model}-${message.round}-${index}`}><span className="group-member-avatar" aria-hidden="true">{message.member_name?.slice(0, 1) || 'A'}</span><div><header><strong>{message.member_name}</strong><small>{message.execution_label ? [message.execution_label, message.execution_model].filter(Boolean).join(' · ') : roleModelName({ id: message.model })}</small>{message.phase && <small className="group-phase-badge">{message.phase === 'plan' ? '统筹规划' : message.phase === 'synthesis' ? '统筹汇总' : '成员讨论'}</small>}{message.round > 1 && <small>第 {message.round} 轮</small>}</header>{message.error && <p className="output-error">发言失败：{message.error}</p>}{message.content && <Markdown>{message.content}</Markdown>}{message.incomplete && <p className="result-note">发言达到输出长度限制，内容可能不完整。</p>}<div className="group-message-actions"><CopyTextButton text={message.content || ''} label={`复制 ${message.member_name} 的发言`} />{message.content && onQuoteReply && <button className="subtle-button" type="button" onClick={() => onQuoteReply(message, job)}>@{message.member_name} 追问</button>}{!automatic && message.content && onSendToMuse && handoff.isVideoStoryboard && <button className="subtle-button group-inline-muse-btn group-smart-handoff" type="button" onClick={() => onSendToMuse(message.content, job)}><span className="group-muse-mark">M</span>一键交给 @Muse 生成视频</button>}{!automatic && message.content && onSendToMuse && !handoff.isVideoStoryboard && <button className="subtle-button group-inline-muse-btn" type="button" onClick={() => onSendToMuse(message.content, job)}><span className="group-muse-mark">M</span>准备 Muse 任务</button>}{!automatic && message.content && onHandOffToAgent && handoff.isCodeProposal && <button className="subtle-button group-smart-handoff" type="button" onClick={() => onHandOffToAgent(message.content, message.member_id, job.id)}>交给 @Codex 执行修改</button>}{!automatic && message.content && onHandOffToAgent && !handoff.isCodeProposal && <button className="subtle-button" type="button" onClick={() => onHandOffToAgent(message.content, message.member_id, job.id)}>交由代码 Agent</button>}</div></div></section>;
    })}
    </MessageContainer>}
    {hasDiscussionOutput && <button className="subtle-button group-discussion-preview" type="button" onClick={() => onOpenArtifacts(job, true)}><Icon name="panel" size={14} />查看本轮发言</button>}
    {!automatic && groupActive(job) && <p className="group-turn-running" role="status"><span className="small-spinner" />成员正在依次阅读上下文并发言…</p>}
    {job.error && <p className="output-error" role="alert">{job.error}</p>}
    {!automatic && !groupActive(job) && !messages.length && <p className="result-note">本轮没有收到模型发言。记录已保存，可以继续发送消息。</p>}
    <details className="group-turn-record"><summary>{job.result?.partial_failure ? automatic ? '本轮有未完成项' : '本轮部分发言失败' : statuses[job.status] || job.status} · 来源与运行记录</summary><ResultProvenance job={job} />{job.context_history && <p>本轮实际引用 {job.context_history.used_turns ?? 0} 轮会话历史；引用受上下文预算限制。</p>}<ul>{(job.events || []).filter((event) => ['log', 'error', 'tool_result'].includes(event.type)).map((event, index) => <li key={event.seq || index}>{event.message || event.text || `工具：${event.tool}`}</li>)}</ul></details>
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
    <div className="group-members-dialog-body"><MissionTeamPicker projectId={room.project_id} team={team} models={models} catalog={catalog} onChange={chooseTeam} onPending={setPending} onOpenAgentSettings={onOpenAgentSettings} editSignal={addRequested ? 1 : 0} allowGroupAgents requireSaved />
      <p className="group-members-help">先保存协作组，再应用到本群。其他群聊继续使用各自的成员；过去的发言、任务和审阅保留原成员身份。正在执行的任务完成或停止后才可应用。</p>
      {error && <p className="output-error" role="alert">{error}</p>}
    </div>
    <footer><button type="button" className="subtle-button" disabled={saving} onClick={onClose}>取消</button><button type="button" className="primary-button" disabled={saving || pending || !team?.id} onClick={apply}>{saving ? '正在应用' : '应用到当前群聊'}</button></footer>
  </dialog>;
}

function GroupHeader({ rooms, roomId, roomTitle, selectRoom, onOpenNavigation, workMode, onWorkModeChange, memberCount, onOpenRoster, onOpenMuse, onOpenTodo, onOpenRoutines, feishuConnected, onOpenFeishu, onOpenFeishuSettings, focusHistory, onToggleFocusHistory, artifactsVisible, onToggleResults, resultAvailable }) {
  const moreDialog = useRef(null);
  const hasRoom = Boolean(roomId);
  return <header className="workspace-header group-compact-header">
    <button className="mobile-menu icon-button" aria-label="打开导航" onClick={onOpenNavigation}><Icon name="menu" /></button>
    <h1 className="group-sr-only">{roomTitle || '项目群聊'}</h1>
    <div className="group-room-switcher">
      <Icon name="group" size={17} />
      <select aria-label="打开项目群聊" title={roomTitle} value={roomId || ''} onChange={(event) => selectRoom(event.target.value || null)}>
        <option value="">{hasRoom ? '＋ 新建群聊' : '新建项目群聊'}</option>
        {hasRoom && !rooms.some((room) => room.id === roomId) && <option value={roomId}>{roomTitle || '正在读取群聊'}</option>}
        {rooms.map((room) => <option key={room.id} value={room.id}>{room.title}</option>)}
      </select>
    </div>
    {hasRoom && onOpenFeishu && <button type="button" className={`subtle-button group-feishu-trigger${feishuConnected ? ' connected' : ''}`} aria-haspopup="dialog" aria-description={feishuConnected ? '已接入' : undefined} onClick={onOpenFeishu}>
      <span className="feishu-header-dot" aria-hidden="true" />飞书机器人
    </button>}
    {!hasRoom && onOpenFeishuSettings && <button type="button" className="subtle-button group-feishu-setup-shortcut" onClick={onOpenFeishuSettings}>飞书机器人</button>}
    {hasRoom && <>
      <nav className="group-work-mode" aria-label="群聊协作方式">
        {workModes.map(([id, label, icon]) => <button key={id} type="button" className={workMode === id ? 'selected' : ''} aria-current={workMode === id ? 'page' : undefined} onClick={() => onWorkModeChange(id)}>
          <Icon name={icon} size={15} />{label}
        </button>)}
      </nav>
      <div className="group-header-actions">
        {onOpenRoster && <button type="button" className="subtle-button group-roster-trigger" aria-haspopup="dialog" aria-label={`成员与分工，${memberCount} 位成员`} onClick={onOpenRoster}><Icon name="group" size={16} /><span>成员 {memberCount}</span></button>}
        {onOpenTodo && <button type="button" className="subtle-button group-todo-trigger" onClick={onOpenTodo}><Icon name="dashboard" size={15} />任务看板</button>}
        {onOpenRoutines && <button type="button" className="subtle-button group-routines-trigger" aria-haspopup="dialog" onClick={onOpenRoutines}><Icon name="clock" size={15} />群聊自动化</button>}
        <button type="button" className="subtle-button group-result-trigger" disabled={!resultAvailable && !artifactsVisible} aria-expanded={artifactsVisible} onClick={onToggleResults}><Icon name="panel" size={15} />成果</button>
        <button type="button" className={`subtle-button group-more-trigger${focusHistory ? ' selected' : ''}`} aria-haspopup="dialog" aria-label="更多群聊操作" onClick={() => moreDialog.current?.showModal()}><Icon name="settings" size={15} /><span className="group-more-label">更多</span><span className="group-current-mode">{compactModeLabels[workMode]}<Icon name="chevron" size={11} /></span></button>
      </div>
      <dialog ref={moreDialog} className="group-options-dialog group-more-dialog" aria-labelledby="group-more-heading">
        <header><h2 id="group-more-heading">群聊操作</h2><button className="icon-button" type="button" aria-label="关闭群聊操作" onClick={() => moreDialog.current?.close()}><Icon name="close" size={18} /></button></header>
        <div className="group-options-body">
          <div className="group-more-modes" role="group" aria-label="群聊协作方式">
            {workModes.map(([id, label, icon]) => <button key={id} type="button" className="subtle-button" aria-pressed={workMode === id} onClick={() => { moreDialog.current?.close(); onWorkModeChange(id); }}><Icon name={icon} size={15} />{label}</button>)}
          </div>
          <div className="group-more-actions">
            {onOpenTodo && <button type="button" className="subtle-button" onClick={() => { moreDialog.current?.close(); onOpenTodo(); }}><Icon name="dashboard" size={15} />任务看板</button>}
            {onOpenRoutines && <button type="button" className="subtle-button" aria-haspopup="dialog" onClick={() => { moreDialog.current?.close(); onOpenRoutines(); }}><Icon name="clock" size={15} />群聊自动化</button>}
            {onOpenMuse && <button type="button" className="subtle-button" onClick={() => { moreDialog.current?.close(); onOpenMuse(); }}><span className="group-muse-mark">M</span>Muse 面板</button>}
            <button type="button" className="subtle-button" onClick={() => { moreDialog.current?.close(); onToggleFocusHistory(); }}><Icon name="expand" size={15} />{focusHistory ? '退出专注' : '专注阅读'}</button>
            <button type="button" className="subtle-button" onClick={() => { moreDialog.current?.close(); selectRoom(null); }}><Icon name="plus" size={15} />新建群聊</button>
          </div>
        </div>
      </dialog>
    </>}
  </header>;
}

function GroupRoom({ roomId, projectId, initialJob, header, onOpenTodo, status, models, context, onContextChange, onOpenArtifacts, onClearArtifacts, onCloseArtifacts, artifactsVisible, onResultShortcutChange, onOpenProjects, onOpenAgentSettings, renderIntegration, focusHistory, onToggleFocusHistory }) {
  const machine = machineInfo(status);
  const { assets, transfers } = useProjects();
  const [workMode, setWorkMode] = useState(() => { const value = localStorage.getItem(`modeldesk.group.mode.${projectId || 'none'}.${roomId}`); return !initialJob && ['code', 'review'].includes(value) ? value : 'discussion'; });
  useEffect(() => { localStorage.setItem(`modeldesk.group.mode.${projectId || 'none'}.${roomId}`, workMode); }, [workMode, projectId, roomId]);
  const [saved] = useState(() => readGroupDraft(localStorage, projectId, roomId));
  const [prompt, setPrompt] = useState(saved.prompt);
  const [recipientIds, setRecipientIds] = useState(saved.recipientIds);
  const [harnessMode, setHarnessMode] = useState(saved.harnessMode || 'relay');
  const [deliveryMode, setDeliveryMode] = useState(saved.deliveryMode);
  const [backgroundMode, setBackgroundMode] = useState(saved.backgroundMode);
  const [outputOptions, setOutputOptions] = useState({ outputFormat: saved.outputFormat, imageCount: saved.imageCount, mediaProvider: saved.mediaProvider });
  const [documentOptions, setDocumentOptions] = useState({ documentFormats: saved.documentFormats, documentLayout: saved.documentLayout });
  const [contextTurns, setContextTurns] = useState(saved.contextTurns);
  const [contextMode, setContextMode] = useState(saved.contextMode);
  const [attachmentPending, setAttachmentPending] = useState(false);
  const settingsDialog = useRef(null);
  const museDialog = useRef(null);
  const rosterDialog = useRef(null);
  const [showLatest, setShowLatest] = useState(false);
  const artifactKey = `modeldesk.group.artifact.${projectId || 'none'}.${roomId}`;
  const [selectedArtifactId, setSelectedArtifactId] = useState(() => initialJob?.id || localStorage.getItem(artifactKey));
  const [restoredResult, setRestoredResult] = useState(null);
  useEffect(() => {
    if (selectedArtifactId) localStorage.setItem(artifactKey, selectedArtifactId);
    else localStorage.removeItem(artifactKey);
  }, [selectedArtifactId, artifactKey]);
  const [memberSettings, setMemberSettings] = useState(null);
  const [museTasks, setMuseTasks] = useState([]);
  const [agentTasks, setAgentTasks] = useState([]);
  const [openedPatch, setOpenedPatch] = useState(null);
  const [patchError, setPatchError] = useState('');
  const [museHistoryError, setMuseHistoryError] = useState('');
  const [appliedRuntimeReceipt, setAppliedRuntimeReceipt] = useState(null);
  const appliedRuntimeReceiptRef = useRef(null);
  const [engines, setEngines] = useState([]);
  const [museProbe, setMuseProbe] = useState(null);
  const [harnessNotice, setHarnessNotice] = useState('');
  const [timelineFilter, setTimelineFilter] = useState('all');
  const [routinesOpen, setRoutinesOpen] = useState(false);
  const [feishuAccess, setFeishuAccess] = useState(null);
  const [feishuError, setFeishuError] = useState('');
  const [feishuOpen, setFeishuOpen] = useState(false);
  const feishuRequest = useRef(null);
  const [routineLocation, setRoutineLocation] = useState(null);
  const [cursorPosition, setCursorPosition] = useState(null);
  const [suggestionIndex, setSuggestionIndex] = useState(0);
  const [dismissedTriggerKey, setDismissedTriggerKey] = useState('');
  const composerInputRef = useRef(null);
  const pendingHarnessJobRef = useRef(null);
  const initialResultRevealRef = useRef(Boolean(initialJob));
  const explicitPreview = useRef(null);
  const resultShortcut = useRef(null);
  const museMonitoring = museTasks.some((task) => task.monitoring);

  useEffect(() => {
    let disposed = false;
    groupTaskApi.engines().then((result) => { if (!disposed) setEngines(result.engines || []); }).catch(() => {});
    if (!REMOTE_CLIENT) museApi.status().then((result) => { if (!disposed) setMuseProbe(result); }).catch(() => {});
    return () => { disposed = true; };
  }, []);

  const loadFeishu = useCallback(() => {
    feishuRequest.current?.abort();
    const controller = new AbortController();
    feishuRequest.current = controller;
    feishuApi.roomAccess(roomId, projectId, controller.signal).then((result) => {
      if (controller.signal.aborted) return;
      if (result?.room_id !== roomId || (result.project_id || null) !== (projectId || null)) throw new Error();
      setFeishuAccess(result);
      setFeishuError('');
    }).catch(() => {
      if (!controller.signal.aborted) setFeishuError('暂时无法读取本群的飞书连接状态。');
    });
  }, [projectId, roomId]);
  const refreshFeishu = useCallback(() => {
    setFeishuAccess(null);
    setFeishuError('');
    loadFeishu();
  }, [loadFeishu]);
  useEffect(() => {
    loadFeishu();
    window.addEventListener('modeldesk-feishu-changed', refreshFeishu);
    return () => {
      feishuRequest.current?.abort();
      window.removeEventListener('modeldesk-feishu-changed', refreshFeishu);
    };
  }, [loadFeishu, refreshFeishu]);
  const openFeishu = () => {
    refreshFeishu();
    setFeishuOpen(true);
  };

  const refreshMuseAndAgents = useCallback(async () => {
    try {
      const tasks = await museApi.list(projectId, roomId);
      setMuseTasks((previous) => mergeMuseTasks(previous, tasks, projectId, roomId));
      setMuseHistoryError('');
    } catch (failure) {
      setMuseHistoryError(failure.message);
    }
    if (projectId && roomId) {
      try {
        const list = await groupTaskApi.list(projectId, roomId);
        setAgentTasks(Array.isArray(list) ? list.filter((item) => item.project_id === projectId && item.room_id === roomId) : []);
      } catch { /* Keep existing agent tasks on transient error. */ }
    }
  }, [projectId, roomId]);

  const runtimeDispatch = useGroupDispatch({ projectId, roomId, channel: 'composer', agentTasks, museTasks, onRefresh: refreshMuseAndAgents });
  const { pending: runtimePending, accepted: runtimeAccepted, busy: runtimeDispatching, error: runtimeError, warning: runtimeWarning, acknowledge: acknowledgeRuntime } = runtimeDispatch;

  useEffect(() => {
    let disposed = false;
    const run = async () => { if (!disposed) await refreshMuseAndAgents(); };
    run();
    const timer = setInterval(run, museMonitoring ? 2500 : 10000);
    return () => { disposed = true; clearInterval(timer); };
  }, [refreshMuseAndAgents, museMonitoring]);

  const integrationRef = useRef(null);
  const roomState = useGroupRoom(projectId, roomId, initialJob);
  const attachmentRef = useRef(null);
  const timelineRef = useRef(null);
  const nearBottom = useRef(true);
  const olderPosition = useRef(null);
  const { room, jobs, activeJob, loading, busy, error, accepted, submission, submissionError, acknowledge } = roomState;
  const [appliedReceipt, setAppliedReceipt] = useState(null);
  const appliedReceiptRef = useRef(null);
  const restoredJob = restoredResult?.job;
  const history = useMemo(() => ({
    roomId,
    roomTitle: room?.title || '项目群聊',
    jobs: restoredJob ? mergeRoomJobs([restoredJob], jobs, projectId, roomId) : jobs,
    museTasks: museTasks.map((task) => ({ ...task, video_url: resolveMuseVideoUrl(task) })),
    agentTasks: agentTasks.map((task) => withFetchedPatch(task, openedPatch)),
  }), [roomId, room?.title, jobs, projectId, restoredJob, museTasks, agentTasks, openedPatch]);
  const roomResults = useMemo(() => deriveRoomArtifacts(history), [history]);
  const selectedPreview = roomResults.find((artifact) => artifact.origin_job.id === selectedArtifactId)?.origin_job;
  const latestResult = roomResults.find((artifact) => ['deliverable', 'media', 'code'].includes(artifactCategory(artifact))) || roomResults[0];
  const missingSavedResult = Boolean(selectedArtifactId && !selectedPreview && !jobs.some((job) => job.id === selectedArtifactId) && restoredResult?.id !== selectedArtifactId);
  useEffect(() => {
    if (loading || !missingSavedResult) return;
    let disposed = false;
    workbenchApi.getJob(selectedArtifactId).then((job) => {
      const inRoom = job.id === selectedArtifactId && job.group_room_id === roomId && (job.project_id || null) === (projectId || null);
      if (!disposed) setRestoredResult({ id: selectedArtifactId, job: inRoom ? job : null });
    }).catch(() => {
      if (!disposed) setRestoredResult({ id: selectedArtifactId, job: null });
    });
    return () => { disposed = true; };
  }, [loading, missingSavedResult, selectedArtifactId, projectId, roomId]);
  const withRoomHistory = useCallback((target) => {
    return { ...target, room_history: history };
  }, [history]);
  const showResult = useCallback((target) => {
    explicitPreview.current = target;
    setPatchError('');
    setSelectedArtifactId(target.id);
    onOpenArtifacts(target, true);
    if (target.mode === 'group_agent' && target.diff_sha256 && !target.diff) {
      groupTaskApi.get(target.id, projectId, roomId).then((detail) => {
        if (explicitPreview.current === target) setOpenedPatch(detail);
      }).catch((failure) => {
        if (explicitPreview.current === target) setPatchError(failure.message);
      });
    }
  }, [onOpenArtifacts, projectId, roomId]);
  const openTurnArtifact = (job) => showResult(withRoomHistory(job));
  const availableResult = selectedPreview || (missingSavedResult ? null : latestResult?.origin_job);
  const resultAvailable = Boolean(availableResult);
  useEffect(() => {
    resultShortcut.current = availableResult ? withRoomHistory(availableResult) : null;
  }, [availableResult, withRoomHistory]);
  const openResultShortcut = useCallback(() => {
    if (!resultShortcut.current) return false;
    showResult(resultShortcut.current);
    return true;
  }, [showResult]);
  useEffect(() => {
    if (!resultAvailable || !onResultShortcutChange) return;
    onResultShortcutChange(openResultShortcut);
    return () => onResultShortcutChange(null);
  }, [onResultShortcutChange, openResultShortcut, resultAvailable]);
  const closeTurnArtifact = () => {
    explicitPreview.current = null;
    setPatchError('');
    onCloseArtifacts?.();
  };
  const toggleResults = () => {
    if (artifactsVisible) closeTurnArtifact();
    else if (availableResult) openTurnArtifact(availableResult);
  };
  const members = useMemo(() => room?.team_snapshot?.members || [], [room]);
  const deliveryMembers = useMemo(() => members.filter((member) => member.mcp_can_plan !== true), [members]);
  const { runtimeMember, discussionMembers: selectedMembers, selectionError } = groupSelection(deliveryMembers, recipientIds);
  const runtimeKind = runtimeMember ? groupMemberKind(runtimeMember) : null;
  const selectedMcp = deliveryMembers.some((member) => groupMemberKind(member) === 'mcp' && recipientIds.includes(member.id));
  const runtimeEngine = engines.find((item) => item.id === runtimeKind);
  const runtimeReadiness = museReadiness(museProbe);
  const coordinatorModel = room?.team_snapshot?.coordinator_model;
  const routeLabel = (id) => roleModelName(status?.models?.find((model) => model.id === id) || { id });
  const coordinatorMember = members.find((member) => member.id === room?.team_snapshot?.coordinator_member_id);
  const usesMcpPlanner = Boolean(coordinatorMember && deliveryMode === 'auto' && !recipientIds.length);
  const runtimeReady = runtimeKind === 'muse' ? (!REMOTE_CLIENT && runtimeReadiness.ready) : Boolean(projectId && runtimeEngine?.available && runtimeEngine.authenticated !== false);
  const activeTrigger = useMemo(() => detectComposerTrigger(prompt, cursorPosition), [prompt, cursorPosition]);
  const triggerKey = activeTrigger ? `${activeTrigger.type}:${activeTrigger.start}:${activeTrigger.query}` : '';
  const composerSuggestions = useMemo(
    () => (activeTrigger && triggerKey !== dismissedTriggerKey ? filterComposerSuggestions(activeTrigger, deliveryMembers) : []),
    [activeTrigger, triggerKey, dismissedTriggerKey, deliveryMembers]
  );

  const applyQuickRecipe = useCallback((recipe, trigger = null) => {
    if (!recipe) return;
    if (trigger) {
      setPrompt((current) => `${current.slice(0, trigger.start)}${current.slice(trigger.end)}`.replace(/^\s+/, ''));
    }
    if (recipe.deliveryMode) setDeliveryMode(recipe.deliveryMode);
    if (recipe.outputFormat) setOutputOptions((current) => ({ ...current, outputFormat: recipe.outputFormat }));
    if (recipe.harnessMode) setHarnessMode(recipe.harnessMode);
    if (recipe.preferKind) {
      const preferred = deliveryMembers.find((item) => groupMemberKind(item) === recipe.preferKind);
      if (preferred && recipe.deliveryMode !== 'auto') {
        setRecipientIds([preferred.id]);
      } else {
        setRecipientIds([]);
      }
    } else {
      setRecipientIds((current) => current.filter((id) => deliveryMembers.some((item) => item.id === id && groupMemberKind(item) === 'model')));
    }
    setHarnessNotice(`已应用快捷协作配方：${recipe.label}（${recipe.detail}）`);
    requestAnimationFrame(() => composerInputRef.current?.focus());
  }, [deliveryMembers]);

  const selectComposerSuggestion = useCallback((candidate) => {
    if (!candidate) return;
    if (candidate.type === 'recipe') {
      applyQuickRecipe(candidate.recipe, activeTrigger);
    } else if (candidate.type === 'mention') {
      const applied = applyMentionCandidate(prompt, activeTrigger, candidate, recipientIds, deliveryMembers);
      setPrompt(applied.prompt);
      setRecipientIds(applied.recipientIds);
    }
    setDismissedTriggerKey(triggerKey);
    setSuggestionIndex(0);
    requestAnimationFrame(() => composerInputRef.current?.focus());
  }, [activeTrigger, applyQuickRecipe, deliveryMembers, prompt, recipientIds, triggerKey]);

  const quoteReplyToMember = useCallback((message) => {
    if (!message?.content) return;
    const matchedMember = members.find((item) => item.id === message.member_id || item.model === message.model);
    if (matchedMember && groupMemberKind(matchedMember) === 'model') {
      setDeliveryMode('discussion');
      setRecipientIds([matchedMember.id]);
    }
    setPrompt((current) => formatQuoteReply(message, current));
    requestAnimationFrame(() => {
      const el = composerInputRef.current;
      if (el) {
        el.focus();
        el.setSelectionRange(el.value.length, el.value.length);
      }
    });
  }, [members]);

  useEffect(() => { onContextChange(saved.context); onClearArtifacts(); }, [saved, onContextChange, onClearArtifacts]);
  useEffect(() => {
    if ((context.project_id || null) === (projectId || null)) {
      localStorage.setItem(groupDraftKey(projectId, roomId), JSON.stringify({ prompt, recipientIds, harnessMode, deliveryMode, backgroundMode, ...outputOptions, ...documentOptions, contextTurns, contextMode, context }));
      if (appliedReceipt) acknowledge(appliedReceipt);
      if (appliedRuntimeReceipt) acknowledgeRuntime(appliedRuntimeReceipt);
    }
  }, [prompt, recipientIds, harnessMode, deliveryMode, backgroundMode, outputOptions, documentOptions, contextTurns, contextMode, context, projectId, roomId, appliedReceipt, acknowledge, appliedRuntimeReceipt, acknowledgeRuntime]);
  useEffect(() => {
    if (initialResultRevealRef.current && initialJob && workMode === 'discussion') {
      initialResultRevealRef.current = false;
      explicitPreview.current = initialJob;
      onOpenArtifacts(withRoomHistory(initialJob), true);
    } else if (artifactsVisible && explicitPreview.current) {
      onOpenArtifacts(withRoomHistory(selectedPreview || withFetchedPatch(explicitPreview.current, openedPatch)), false);
    }
  }, [artifactsVisible, selectedPreview, initialJob, workMode, onOpenArtifacts, withRoomHistory, openedPatch]);
  useEffect(() => {
    const element = timelineRef.current;
    if (!element) return;
    if (olderPosition.current) { element.scrollTop = element.scrollHeight - olderPosition.current.height + olderPosition.current.top; olderPosition.current = null; }
    else if (nearBottom.current) element.scrollTop = element.scrollHeight;
  }, [jobs, museTasks, agentTasks, workMode]);
  useEffect(() => {
    if (!routineLocation || loading || workMode !== 'discussion') return;
    const frame = requestAnimationFrame(() => {
      const turn = timelineRef.current?.querySelector(`[data-group-job="${CSS.escape(routineLocation)}"]`);
      if (!turn) return;
      turn.scrollIntoView({ block: 'start' });
      turn.focus({ preventScroll: true });
      setRoutineLocation(null);
    });
    return () => cancelAnimationFrame(frame);
  }, [routineLocation, jobs, loading, workMode, timelineFilter]);

  const openRoutineJob = (job) => {
    roomState.includeJob(job);
    setTimelineFilter('all');
    setWorkMode('discussion');
    setRoutineLocation(job.id);
  };

  const prepareMuseDraft = useCallback((text, sourceJob = null) => {
    const member = members.find((item) => groupMemberKind(item) === 'muse');
    if (!member) { setHarnessNotice('请先在“添加 Agent”中加入 Muse 原生 Agent，再准备任务。'); return; }
    setRecipientIds([member.id]);
    setPrompt(sourceJob ? buildMuseHandoffPrompt(text, sourceJob, room) : text);
    setHarnessNotice('已准备 Muse 任务草稿。检查内容后，点击“派发 Muse”才会操作原生应用。');
  }, [members, room]);

  const handOffToAgent = useCallback((specText, preferredMemberId, sourceRunId) => {
    const currentDraft = readTaskDraft(localStorage, projectId, roomId);
    const targetMember = deliveryMembers.find((item) => ['codex', 'claude'].includes(groupMemberKind(item)) && item.id !== preferredMemberId) || deliveryMembers.find((item) => groupMemberKind(item) !== 'muse' && item.id !== preferredMemberId);
    localStorage.setItem(taskDraftKey(projectId, roomId), JSON.stringify({
      ...currentDraft,
      memberId: targetMember?.id || currentDraft.memberId,
      engine: ['codex', 'claude'].includes(groupMemberKind(targetMember)) ? targetMember.kind : currentDraft.engine || 'codex',
      prompt: specText.slice(0, 11000),
      sourceRunId: sourceRunId || '',
    }));
    setWorkMode('code');
    setHarnessNotice('已将群内方案填入代码 Agent 执行器，确认成员与引擎后即可分配任务。');
  }, [projectId, roomId, deliveryMembers]);

  useEffect(() => {
    const pending = pendingHarnessJobRef.current;
    if (!pending) return;
    const matched = jobs.find((item) => item.id === pending.jobId);
    if (!matched || groupActive(matched)) return;
    let disposed = false;
    queueMicrotask(() => {
      if (disposed || pendingHarnessJobRef.current !== pending) return;
      pendingHarnessJobRef.current = null;
      if (matched.status !== 'complete') return;
      const deliverable = extractGroupDeliverable(matched, room);
      if (matched.result?.partial_failure || !deliverable) { setHarnessNotice('本轮存在失败、不完整输出或没有可用结论，已保留原始记录。请检查成员发言后自行准备任务，不自动生成执行草稿。'); return; }
      if (prompt.trim()) { setHarnessNotice('讨论已完成，当前输入草稿已保留。可在成员发言下点击“准备 Muse 任务”或“交由代码 Agent”。'); return; }
      if (pending.mode === 'discuss_muse') {
        prepareMuseDraft(pending.userPrompt, matched);
      } else if (pending.mode === 'discuss_agent') {
        handOffToAgent(`根据群聊讨论结论执行以下任务：\n${pending.userPrompt}\n\n群内成员方案总结：\n${deliverable}`, undefined, matched.id);
      }
    });
    return () => { disposed = true; };
  }, [jobs, prepareMuseDraft, handOffToAgent, room, prompt]);

  const errors = [
    ...(selectionError ? [selectionError] : []),
    ...(selectedMcp && deliveryMode !== 'auto' ? ['MCP Agent 需要在任务设置中选择“自动完成”。'] : []),
    ...(REMOTE_CLIENT && usesMcpPlanner ? ['MCP Agent 统筹需要在本机启动。'] : []),
    ...contextErrors(context), ...attachmentIssues(assets, context.attachment_ids, context.include_project_assets),
    ...((context.project_id || null) !== (projectId || null) ? ['正在切换群聊资料。'] : []),
    ...(!runtimeMember && recipientIds.some((id) => !deliveryMembers.some((member) => member.id === id)) ? ['部分收件成员不在这个群聊，请重新选择。'] : []),
    ...(!runtimeMember && status && selectedMembers.some((member) => !status.models?.some((model) => model.id === member.model && !model.unavailable)) ? ['指定成员的模型当前未接入，请选择可用成员或检查“Agent 与连接”。'] : []),
    ...(!runtimeMember && !recipientIds.length && coordinatorModel && status && !status.models?.some((model) => model.id === coordinatorModel && !model.unavailable) ? [usesMcpPlanner ? '编辑与验收模型当前未连接，请检查“Agent 与连接”。' : '统筹 LLM 当前未连接，请检查“Agent 与连接”。'] : []),
    ...(!runtimeMember && !status ? ['正在读取模型连接状态。'] : []),
    ...(room && !loading && !runtimeMember && !selectedMembers.length && !(deliveryMode === 'auto' && deliveryMembers.some((member) => ['image', 'mcp'].includes(groupMemberKind(member)) && (!recipientIds.length || recipientIds.includes(member.id)))) && !(coordinatorModel && !recipientIds.length) ? ['本群还没有 LLM 成员，请添加模型成员，或明确选择一位运行时 Agent。'] : []),
  ];
  const canSend = Boolean(room && !submission && !runtimePending && prompt.trim() && (runtimeMember ? runtimeReady : !errors.length) && !loading && !busy && !runtimeDispatching && !activeJob && !attachmentPending && !transfers);
  const canRetry = Boolean(room && submission && !accepted && !busy);
  const canRetryRuntime = Boolean(room && runtimePending && !runtimeAccepted && !runtimeDispatching);
  const pendingRuntimeName = members.find((member) => member.id === runtimePending?.body.member_id)?.name;
  const runtimeIntent = useMemo(() => runtimeMember && prompt.trim() && (runtimeKind === 'muse' || projectId)
    ? { ...groupRuntimeRequest({ member: runtimeMember, projectId, roomId, prompt }), contextTurns }
    : null, [runtimeMember, runtimeKind, projectId, roomId, prompt, contextTurns]);
  const activeHarness = GROUP_HARNESS_MODES.find((item) => item.id === harnessMode) || GROUP_HARNESS_MODES[0];
  const messageBody = useMemo(() => groupMessageRequest({
    prompt, recipientIds, context, contextTurns, contextMode,
    rounds: deliveryMode === 'discussion' ? activeHarness.rounds || 1 : 1,
    deliveryMode, backgroundMode, ...outputOptions, ...documentOptions,
  }), [prompt, recipientIds, context, contextTurns, contextMode, activeHarness, deliveryMode, backgroundMode, outputOptions, documentOptions]);

  useEffect(() => {
    if (!accepted || !submission || appliedReceiptRef.current === submission.id || (context.project_id || null) !== (projectId || null)) return;
    let disposed = false;
    queueMicrotask(() => {
      if (disposed) return;
      appliedReceiptRef.current = submission.id;
      const { body, harnessMode: sentHarness } = submission;
      const sameDraft = groupSubmissionMatches(submission, messageBody, harnessMode);
      if (sameDraft) {
        setPrompt((current) => current.trim() === body.prompt ? '' : current);
        setContextMode('continue');
        onContextChange((current) => ({
          ...current,
          attachment_ids: current.attachment_ids.filter((id) => !body.attachment_ids?.includes(id)),
          ...(current.muse_task_id === body.muse_task_id ? { muse_task_id: null, muse_task_label: '', muse_harness_response: '' } : {}),
        }));
      }
      if (body.delivery_mode === 'auto') globalThis.dispatchEvent?.(new Event('modeldesk-autonomy-changed'));
      if (!sameDraft) {
        setHarnessNotice('上次发送已确认，可在群聊记录查看。当前草稿已保留。');
      } else if (groupActive(accepted) && body.delivery_mode === 'auto') {
        setHarnessNotice(body.background_mode ? `后台任务已收到，可在“后台任务”查看。${machine.short}需保持唤醒并联网。` : '任务已收到，可在本轮查看进度。');
      } else if (body.delivery_mode === 'discussion' && ['discuss_muse', 'discuss_agent'].includes(sentHarness) && (groupActive(accepted) || accepted.status === 'complete')) {
        pendingHarnessJobRef.current = { jobId: accepted.id, mode: sentHarness, userPrompt: body.prompt };
        setHarnessNotice(sentHarness === 'discuss_muse' ? '群聊已收到消息。讨论结束后会准备 Muse 草稿，由你确认派发。' : '群聊已收到消息。讨论结束后会准备代码 Agent 草稿，由你确认派发。');
      } else {
        setHarnessNotice('发送已确认，可在群聊记录查看。');
      }
      nearBottom.current = true;
      setShowLatest(false);
      setAppliedReceipt(submission.id);
    });
    return () => { disposed = true; };
  }, [accepted, submission, context.project_id, projectId, messageBody, harnessMode, onContextChange, machine.short]);

  useEffect(() => {
    if (!room || !runtimeAccepted || !runtimePending || appliedRuntimeReceiptRef.current === runtimePending.id || (context.project_id || null) !== (projectId || null)) return;
    let disposed = false;
    queueMicrotask(() => {
      if (disposed) return;
      appliedRuntimeReceiptRef.current = runtimePending.id;
      const sameDraft = groupDispatchIntentMatches(runtimePending, runtimeIntent);
      if (sameDraft) setPrompt('');
      if (runtimePending.target === 'muse') {
        setMuseTasks((previous) => mergeMuseTasks(previous, [runtimeAccepted], projectId, roomId));
      } else {
        setAgentTasks((previous) => [...previous.filter((task) => task.id !== runtimeAccepted.id), runtimeAccepted]);
      }
      setHarnessNotice(sameDraft ? '派发已确认，可在本群查看任务记录。' : '上次派发已确认，当前草稿已保留。');
      nearBottom.current = true;
      setShowLatest(false);
      setAppliedRuntimeReceipt(runtimePending.id);
    });
    return () => { disposed = true; };
  }, [room, runtimeAccepted, runtimePending, runtimeIntent, context.project_id, projectId, roomId]);

  const send = async (event) => {
    event.preventDefault();
    if (!canSend) return;
    setHarnessNotice('');
    if (runtimeIntent) {
      const request = groupRuntimeRequest({ member: runtimeMember, projectId, roomId, prompt: buildGroupRuntimePrompt(prompt, jobs, room, agentTasks, museTasks, contextTurns) });
      await runtimeDispatch.send({ ...request, intent: runtimeIntent });
      return;
    }
    await roomState.send(messageBody, harnessMode);
  };

  const useMuseReply = (task) => {
    if (canUseMuseReply(task, projectId, roomId)) {
      setRecipientIds([]);
      onContextChange((current) => ({
        ...current,
        muse_task_id: task.id,
        muse_task_label: task.prompt?.slice(0, 100) || 'Muse 实际回复',
      }));
    }
  };
  const useAgentOutput = (task) => {
    if (!task?.output) return;
    setRecipientIds([]);
    const label = task.member?.name || task.member_id || 'Agent';
    setPrompt((current) => `${current ? current + '\n\n' : ''}请结合 ${label}（${taskEngineLabel(task)}）的执行结果继续讨论：\n> ${task.output.slice(0, 1200).replace(/\n/g, '\n> ')}\n`);
  };
  const refreshRuntimeStatus = () => {
    groupTaskApi.engines().then((result) => setEngines(result.engines || [])).catch((failure) => setHarnessNotice(failure.message));
    if (!REMOTE_CLIENT) museApi.status().then(setMuseProbe).catch((failure) => setHarnessNotice(failure.message));
  };
  const openMuse = () => {
    setWorkMode('discussion');
    requestAnimationFrame(() => {
      museDialog.current?.showModal();
      const panel = integrationRef.current;
      const details = panel?.querySelector('details');
      if (details) details.open = true;
      panel?.scrollIntoView({ block: 'nearest' });
      details?.querySelector('summary')?.focus();
    });
  };
  const applyMembers = async (updated) => {
    setRecipientIds((previous) => previous.filter((id) => updated.team_snapshot.members.some((member) => member.id === id && member.mcp_can_plan !== true)));
    await roomState.refresh();
  };
  const scrollToLatest = () => {
    const element = timelineRef.current;
    if (element) element.scrollTop = element.scrollHeight;
    nearBottom.current = true;
    setShowLatest(false);
  };
  const locateProgress = () => {
    if (!activeJob) { scrollToLatest(); return; }
    setTimelineFilter('all');
    requestAnimationFrame(() => {
      const turn = timelineRef.current?.querySelector(`[data-group-job="${CSS.escape(activeJob.id)}"]`);
      if (turn) {
        turn.scrollIntoView({ block: 'start' });
        turn.focus({ preventScroll: true });
      }
      else scrollToLatest();
    });
  };
  const loadOlder = async () => {
    const element = timelineRef.current;
    if (element) olderPosition.current = { height: element.scrollHeight, top: element.scrollTop };
    const loaded = await roomState.loadOlder();
    if (!loaded) olderPosition.current = null;
  };

  const allTimelineItems = groupTimeline(jobs, museTasks, projectId, roomId, agentTasks);
  const timelineItems = timelineFilter === 'all'
    ? allTimelineItems
    : allTimelineItems.filter((item) => item.type === timelineFilter);

  const openMemberSettings = (add) => {
    rosterDialog.current?.close();
    setMemberSettings({ room, add });
  };
  const toggleReading = () => {
    if (!focusHistory && artifactsVisible) closeTurnArtifact();
    onToggleFocusHistory();
  };
  const targetLabel = recipientIds.length ? deliveryMembers.filter((member) => recipientIds.includes(member.id)).map((member) => `@${member.name}`).join('、') || '重新选择成员' : '全组协作';
  const modeLabel = runtimeMember ? '单独派发' : deliveryMode === 'auto' ? backgroundMode ? '自动完成 · 后台托管' : '自动完成' : '仅讨论';
  const contextLabel = contextMode === 'fresh' ? '新任务' : '继续任务';

  return <>
    <GroupHeader {...header} onOpenTodo={room && onOpenTodo ? () => onOpenTodo(room) : undefined} roomId={roomId} roomTitle={room?.title || header.roomTitle} workMode={workMode} onWorkModeChange={setWorkMode} memberCount={members.length} onOpenRoster={room ? () => rosterDialog.current?.showModal() : undefined} onOpenMuse={renderIntegration ? openMuse : undefined} onOpenRoutines={room ? () => setRoutinesOpen(true) : undefined} feishuConnected={feishuAccess?.status === 'connected' && feishuAccess.mode !== 'none'} onOpenFeishu={openFeishu} focusHistory={focusHistory} onToggleFocusHistory={toggleReading} artifactsVisible={artifactsVisible} onToggleResults={toggleResults} resultAvailable={resultAvailable} />
    {room && routinesOpen && <GroupRoutines room={room} composerDraft={{ prompt, documentFormats: documentOptions.documentFormats }} onDismiss={() => setRoutinesOpen(false)} onOpenJob={openRoutineJob} onRefreshRoom={roomState.refresh} />}
    {feishuOpen && <GroupFeishuDialog room={room || { id: roomId, title: header.roomTitle }} projectId={projectId} access={feishuAccess} accessError={feishuError} onRetry={refreshFeishu} onOpenFeishuSettings={() => onOpenAgentSettings?.('feishu')} onClose={() => setFeishuOpen(false)} />}
    {header.error && <div className="error-banner" role="alert">{header.error}<button type="button" className="subtle-button" onClick={header.refresh}>重试</button></div>}
    {artifactsVisible && patchError && <div className="error-banner" role="alert">读取补丁失败：{patchError}<button type="button" className="subtle-button" onClick={() => showResult(explicitPreview.current)}>重新读取补丁</button></div>}
    {room && <dialog className="group-options-dialog group-roster-dialog" ref={rosterDialog} aria-labelledby="group-roster-heading">
      <header><div><h2 id="group-roster-heading">成员与分工</h2>{(coordinatorMember || coordinatorModel) && <p>统筹 {coordinatorMember?.name || routeLabel(coordinatorModel)}{coordinatorMember && coordinatorModel ? ` · 编辑与验收 ${routeLabel(coordinatorModel)}` : ''}</p>}</div><button type="button" className="icon-button" aria-label="关闭成员与分工" onClick={() => rosterDialog.current?.close()}><Icon name="close" size={18} /></button></header>
      <div className="group-options-body">
        <div className="group-roster-actions">
          <button type="button" className="subtle-button" onClick={() => openMemberSettings(true)}><Icon name="plus" size={14} />添加 Agent</button>
          <button type="button" className="subtle-button" onClick={() => openMemberSettings(false)}><Icon name="settings" size={14} />管理成员</button>
        </div>
        <div className="group-roster-grid">{members.map((member) => <article key={member.id}>
          <strong>{member.name}</strong>
          <small>{groupMemberKindLabel(member)}{member.model && ` · ${routeLabel(member.model)}`}</small>
          {member.role && <p>{member.role}</p>}
        </article>)}</div>
      </div>
    </dialog>}
    {memberSettings && <GroupTeamSettings room={memberSettings.room} models={models} catalog={status?.models || []} addRequested={memberSettings.add} onOpenAgentSettings={onOpenAgentSettings} onApplied={applyMembers} onClose={() => setMemberSettings(null)} />}
    {workMode !== 'discussion' && room ? <GroupAgentTasks projectId={projectId} room={room} kind={workMode} onKindChange={setWorkMode} onOpenProjects={onOpenProjects} onOpenArtifacts={openTurnArtifact} status={status} /> : <>
      {renderIntegration && <dialog ref={museDialog} className="group-options-dialog group-muse-dialog" aria-label="Muse 任务与连接">
        <header><div><strong>Muse 任务与连接</strong><p>查看原生任务、实际回复和媒体。</p></div><button type="button" className="subtle-button" onClick={() => museDialog.current?.close()}>关闭</button></header>
        <div ref={integrationRef} className="group-options-body">{renderIntegration({ projectId, roomId, onUseReply: useMuseReply })}</div>
      </dialog>}
      <div className="group-room-timeline" ref={timelineRef} onScroll={(event) => { const element = event.currentTarget; nearBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 100; setShowLatest(!nearBottom.current); }}>
        {error && <div className="error-banner" role="alert">{error}<button className="subtle-button" type="button" onClick={roomState.refresh}>重新连接</button></div>}
        {harnessNotice && <div className="group-harness-banner" role="status"><span>{harnessNotice}</span><button type="button" className="icon-button" aria-label="关闭提示" onClick={() => setHarnessNotice('')}><Icon name="close" size={13} /></button></div>}
        {(museTasks.length > 0 || agentTasks.length > 0) && (
          <div className="group-timeline-filter" role="toolbar" aria-label="筛选群聊时间线">
            {[
              ['all', `全部记录 (${allTimelineItems.length})`],
              ['discussion', `模型讨论 (${jobs.length})`],
              ...(museTasks.length ? [['muse', `Muse (${museTasks.length})`]] : []),
              ...(agentTasks.length ? [['agent_task', `代码 Agent (${agentTasks.length})`]] : []),
            ].map(([id, label]) => (
              <button key={id} type="button" className={timelineFilter === id ? 'selected' : ''} aria-pressed={timelineFilter === id} onClick={() => setTimelineFilter(id)}>{label}</button>
            ))}
          </div>
        )}
        {loading ? <p className="mission-loading" role="status"><span className="small-spinner" />正在读取群聊记录…</p> : <>
          {roomState.before && <button className="group-load-older subtle-button" type="button" disabled={roomState.olderLoading} onClick={loadOlder}>{roomState.olderLoading ? '正在加载' : '查看更早的消息'}</button>}
          {!allTimelineItems.length && room && <div className="group-empty-conversation"><Icon name="group" size={28} /><h2>给项目群发第一条消息</h2><p>描述你要的成果，或输入 @ 点名成员。完成后可以从对应消息打开成果。</p></div>}
          {museHistoryError && <p className="result-note" role="status">Muse 历史暂未同步：{museHistoryError}</p>}
          {timelineItems.map(({ type, value }) => {
            if (type === 'muse') {
              return <GroupMuseTurn key={`muse-${value.id}`} task={value} projectId={projectId} roomId={roomId} onUseReply={useMuseReply} onOpenMuse={openMuse} onOpenArtifacts={openTurnArtifact} onRetryMuse={(failedTask) => prepareMuseDraft(failedTask.prompt)} />;
            }
            if (type === 'agent_task') {
              const parentJob = history.jobs.find((job) => job.id === value.parent_job_id);
              return <GroupAgentTaskTurn key={`agent-${value.id}`} task={value} onOpenTaskMode={setWorkMode} onUseAgentOutput={useAgentOutput} onOpenCreative={parentJob && deriveArtifacts(parentJob).length ? () => openTurnArtifact(parentJob) : undefined} />;
            }
            return <GroupTurn key={value.id} job={value} room={room || { team_snapshot: { members: [] } }} onOpenArtifacts={openTurnArtifact} onSendToMuse={prepareMuseDraft} onHandOffToAgent={handOffToAgent} onQuoteReply={quoteReplyToMember} />;
          })}
        </>}
      </div>
      <div className="group-room-compose">
        <GroupLiveActivityBar
          activeJob={activeJob}
          museTasks={museTasks}
          agentTasks={agentTasks}
          onScrollToLatest={locateProgress}
          onOpenMuse={renderIntegration ? openMuse : undefined}
          onOpenCodeTasks={setWorkMode}
        />
        {showLatest && composerSuggestions.length === 0 && <button type="button" className="group-jump-latest subtle-button" onClick={scrollToLatest}>回到最新消息 ↓</button>}
        {roomState.streamError && activeJob && <p className="stream-warning">实时连接暂时中断，正在通过后台记录同步。</p>}
        {context.muse_task_id && <div className="group-muse-source-chip"><strong>Muse 回复待引用</strong><span>{context.muse_task_label || '已选定本群的已完成回复'}</span><button type="button" className="icon-button" aria-label="移除 Muse 回复引用" onClick={() => onContextChange((current) => ({ ...current, muse_task_id: null, muse_task_label: '', muse_harness_response: '' }))}><Icon name="close" size={14} /></button></div>}
        <div className="group-compose-summary">
          <button type="button" className="subtle-button group-config-summary" aria-haspopup="dialog" aria-label={`任务设置，${targetLabel}，${modeLabel}，${contextLabel}`} title={`${targetLabel} · ${modeLabel} · ${contextLabel}`} onClick={() => settingsDialog.current?.showModal()}>
            <Icon name="settings" size={15} /><span>任务设置</span><small>{targetLabel} · {modeLabel}{!runtimeMember && contextMode === 'fresh' ? ' · 新任务' : ''}</small>
          </button>
          {!runtimeMember && <AttachmentComposer compact ref={attachmentRef} attachmentIds={context.attachment_ids} onChange={(ids) => onContextChange({ ...context, attachment_ids: ids })} includeShared={context.include_project_assets} onIncludeShared={(include) => onContextChange({ ...context, include_project_assets: include })} disabled={busy || Boolean(activeJob)} onPendingChange={setAttachmentPending} />}
        </div>
        <dialog ref={settingsDialog} className="group-options-dialog" aria-labelledby="group-options-heading">
          <header><h2 id="group-options-heading">任务设置</h2><button type="button" className="subtle-button" onClick={() => settingsDialog.current?.close()}>完成</button></header>
          <div className="group-options-body">
            <h3>参与成员与成果</h3>
        <div className="group-compose-top-row">
          <div className="group-recipient-picker" role="group" aria-label="选择回复成员">
            <span>发送给</span>
            <button type="button" aria-pressed={!recipientIds.length} className={!recipientIds.length ? 'selected' : ''} onClick={() => setRecipientIds([])}>{deliveryMode === 'auto' ? '@协作组' : '@所有 LLM'}</button>
            {deliveryMembers.map((member) => {
              const runtime = !['model', 'image', 'mcp'].includes(groupMemberKind(member));
              const selected = recipientIds.includes(member.id);
              return <button type="button" key={member.id} aria-pressed={selected} className={`${selected ? 'selected' : ''} ${runtime ? 'group-agent-recipient' : ''}`} title={`${groupMemberKindLabel(member)} · ${runtime ? '选择后需点击明确派发按钮' : routeLabel(member.model)}`} onClick={() => { if (groupMemberKind(member) === 'mcp') setDeliveryMode('auto'); setRecipientIds((previous) => runtime ? (selected ? [] : [member.id]) : [...previous.filter((id) => deliveryMembers.some((item) => item.id === id && ['model', 'image', 'mcp'].includes(groupMemberKind(item))) && id !== member.id), ...(selected ? [] : [member.id])]); }}>@{member.name}<small>{groupMemberKindLabel(member)}</small></button>;
            })}
          </div>
          {!runtimeMember && (
            <GroupDeliveryModeSelector value={deliveryMode} onChange={setDeliveryMode} />
          )}
          {!runtimeMember && deliveryMode === 'discussion' && (
            <label className="group-harness-select" title="选择本群多智能体 Harness 协作编排方式">
              <span>Harness</span>
              <select aria-label="群组 Harness 编排模式" value={harnessMode} onChange={(event) => setHarnessMode(event.target.value)}>
                {GROUP_HARNESS_MODES.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
              </select>
            </label>
          )}
        </div>
        {!runtimeMember && deliveryMode === 'auto' && <BackgroundDelegateSetting checked={backgroundMode} onChange={setBackgroundMode} disabled={busy || Boolean(activeJob)} hostShort={machine.short} />}
        {!runtimeMember && deliveryMode === 'auto' && <DeliveryOutputOptions value={outputOptions} onChange={setOutputOptions} disabled={busy || Boolean(activeJob)} machine={machine} />}
        {!runtimeMember && deliveryMode === 'auto' && <DocumentOutputOptions value={documentOptions} onChange={setDocumentOptions} disabled={busy || Boolean(activeJob)} />}
        <div className="group-quick-recipe-bar" role="toolbar" aria-label="快捷协作配方">
          <span>快捷配方</span>
          {GROUP_QUICK_RECIPES.map((recipe) => (
            <button
              key={recipe.id}
              type="button"
              className="group-recipe-chip"
              title={recipe.detail}
              onClick={() => { settingsDialog.current?.close(); applyQuickRecipe(recipe); }}
            >
              <code>{recipe.command}</code>
              <span>{recipe.label}</span>
            </button>
          ))}
        </div>
            {!runtimeMember && <><div className="group-context-heading"><h3>任务记忆</h3><ProjectMemoryDialog /></div><TaskContextControl value={contextMode} onChange={setContextMode} disabled={busy || Boolean(activeJob)} jobs={jobs} /></>}
            <details className="group-advanced-context"><summary>工具与更多上下文</summary>
              <section className="group-message-settings"><label>最近对话上下文<select aria-label="群聊上下文轮数" value={contextTurns} onChange={(event) => setContextTurns(Number(event.target.value))}>{[1, 4, 6, 8, 12].map((count) => <option key={count} value={count}>{count} 轮</option>)}</select></label><p>仅引用当前群聊的实际记录；过长内容按模型上下文预算截断。</p><ContextAttachments mode="group" value={context} onChange={onContextChange} jobs={jobs} disabled={busy || Boolean(activeJob)} onOpenProjects={onOpenProjects} /></section>
            </details>
            <p className="group-options-note">自动协作会安排本群可用成员执行并收集实际成果。代码改动仍需审阅和合入审批。输入 @ 可点名成员，输入 / 可选择快捷配方。</p>
          </div>
        </dialog>
        {composerSuggestions.length > 0 && (
          <div className="group-autocomplete-popover">
            <div className="group-autocomplete-header">
              <strong>{activeTrigger?.type === 'mention' ? '选择要 @点名的成员（↑↓ 选择，Enter 确认）' : '选择 /快捷协作配方（↑↓ 选择，Enter 应用）'}</strong>
              <button type="button" className="icon-button" aria-label="关闭候选菜单" onClick={() => setDismissedTriggerKey(triggerKey)}>
                <Icon name="close" size={12} />
              </button>
            </div>
            <div id="group-composer-suggestions" role="listbox" aria-label={activeTrigger?.type === 'mention' ? '点名成员候选列表' : '快捷配方候选列表'}>{composerSuggestions.map((item, index) => {
              const selected = index === Math.min(suggestionIndex, composerSuggestions.length - 1);
              return (
                <button
                  key={item.id}
                  id={`group-composer-option-${index}`}
                  type="button"
                  role="option"
                  aria-selected={selected}
                  className={`group-autocomplete-option ${selected ? 'selected' : ''}`}
                  onMouseEnter={() => setSuggestionIndex(index)}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => selectComposerSuggestion(item)}
                >
                  <strong>{item.type === 'mention' ? `@${item.name}` : item.name}</strong>
                  <small>{item.badge}</small>
                </button>
              );
            })}</div>
          </div>
        )}
        <form className="composer group-composer" onSubmit={send} onDragOver={(event) => { if (event.dataTransfer.types.includes('Files')) event.preventDefault(); }} onDrop={(event) => attachmentRef.current?.handleDrop(event)}>
          <textarea
            ref={composerInputRef}
            aria-label="群聊消息"
            aria-autocomplete="list"
            aria-controls={composerSuggestions.length ? 'group-composer-suggestions' : undefined}
            aria-activedescendant={composerSuggestions.length ? `group-composer-option-${Math.min(suggestionIndex, composerSuggestions.length - 1)}` : undefined}
            maxLength={12000}
            value={prompt}
            onChange={(event) => {
              setPrompt(event.target.value);
              setCursorPosition(event.target.selectionStart);
              setDismissedTriggerKey('');
              setSuggestionIndex(0);
            }}
            onClick={(event) => setCursorPosition(event.currentTarget.selectionStart)}
            onKeyUp={(event) => setCursorPosition(event.currentTarget.selectionStart)}
            onPaste={(event) => attachmentRef.current?.handlePaste(event)}
            onKeyDown={(event) => {
              if (composerSuggestions.length > 0 && !event.nativeEvent.isComposing) {
                const safeIndex = Math.min(suggestionIndex, composerSuggestions.length - 1);
                if (event.key === 'ArrowDown') {
                  event.preventDefault();
                  setSuggestionIndex((current) => (current + 1) % composerSuggestions.length);
                  return;
                }
                if (event.key === 'ArrowUp') {
                  event.preventDefault();
                  setSuggestionIndex((current) => (current - 1 + composerSuggestions.length) % composerSuggestions.length);
                  return;
                }
                if (event.key === 'Enter' || event.key === 'Tab') {
                  event.preventDefault();
                  selectComposerSuggestion(composerSuggestions[safeIndex]);
                  return;
                }
                if (event.key === 'Escape') {
                  event.preventDefault();
                  setDismissedTriggerKey(triggerKey);
                  return;
                }
              }
              if (!runtimeMember && event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                send(event);
              }
            }}
            rows={focusHistory ? 1 : 2}
            placeholder={runtimeMember ? `描述要交给 ${runtimeMember.name} 的任务…` : deliveryMode === 'auto' ? '描述你需要的成果…' : '给项目群发消息…'}
          />
          <div className="composer-footer">
            <span className="group-input-hint">@ 点名 · / 配方</span>
            {activeJob && <span>可先写下一条消息</span>}
            {activeJob ? <button className="stop-button" type="button" disabled={busy} onClick={roomState.cancel}><Icon name="stop" size={15} />停止本轮</button> : <button className="primary-button" type="submit" disabled={!canSend}><Icon name="send" size={15} />{busy || runtimeDispatching ? '正在提交' : runtimeMember ? `派发 ${runtimeKind === 'muse' ? 'Muse' : runtimeKind === 'codex' ? 'Codex' : 'Claude Code'}` : deliveryMode === 'auto' ? '开始完成任务' : '发送消息'}</button>}
          </div>
        </form>
        {(submissionError || canRetry || runtimeError || runtimeWarning || canRetryRuntime) && <div className="error-banner" role={submissionError || runtimeError || runtimeWarning ? 'alert' : 'status'}>
          <span>{runtimeWarning ? `${runtimeWarning} 其他任务可在另一个群聊或单项协作继续。` : canRetryRuntime ? `上次${pendingRuntimeName ? `交给 ${pendingRuntimeName} 的任务` : '派发'}仍待确认。重试会沿用原任务。` : runtimeError || submissionError || '上次发送仍待确认。'}</span>
          {canRetry && <button className="subtle-button" type="button" onClick={() => roomState.send()}>重试上次发送</button>}
          {canRetryRuntime && <button className="subtle-button" type="button" onClick={() => runtimeDispatch.send()}>{runtimeWarning ? '重新检查上次派发' : '重试上次派发'}</button>}
        </div>}
        {(!runtimeMember && (errors.length > 0 || attachmentPending || transfers > 0)) && <p className="mission-form-note">{attachmentPending || transfers > 0 ? '请等待资料准备完成。' : errors[0]}</p>}
        {runtimeMember && <p className="group-runtime-boundary">{runtimeKind === 'muse' ? (hostIsMacos(status) ? (REMOTE_CLIENT ? 'Muse 原生窗口操作需要在 Mac 端派发。' : runtimeReadiness.ready ? '将操作本机 Muse。回复与文件仅在桥接实际读取后显示；附件不会自动传入原生应用。' : `${runtimeReadiness.label}：${runtimeReadiness.detail} 请打开 Muse 面板检查连接。`) : 'Muse 原生窗口操作仅在 Mac 上可用；当前主机不是 Mac，无法派发 Muse 任务。') : !projectId ? '代码 Agent 需要选择本地 Git 项目。' : !runtimeReady ? (runtimeEngine?.error || '请在“Agent 与连接”检查本机执行器安装与登录状态。') : '将在隔离 Git 工作区执行，改动需要独立审阅和你的合入审批。附件不会自动传入执行器。'} <button type="button" className="subtle-button" onClick={refreshRuntimeStatus}>重新检查执行器</button></p>}

      </div>
    </>}
  </>;
}

export default function GroupWorkspace({ initialRoomId, initialTeam, initialJob, renderIntegration, status, models, context, onContextChange, onOpenNavigation, onOpenTodo, onOpenProjects, onOpenAgentSettings, onOpenArtifacts, onClearArtifacts, onCloseArtifacts, onResultShortcutChange, artifactsVisible = false }) {
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
  const [focusHistory, setFocusHistory] = useState(() => localStorage.getItem('modeldesk.group.focusHistory') === 'on');
  const alive = useRef(true);
  const chooseTeam = useCallback((next) => setTeam(next), []);
  const toggleFocusHistory = useCallback(() => {
    setFocusHistory((current) => {
      const next = !current;
      localStorage.setItem('modeldesk.group.focusHistory', next ? 'on' : 'off');
      return next;
    });
  }, []);
  const refresh = useCallback(async () => {
    try { const result = await groupApi.list(activeProjectId); if (alive.current) { setRooms(scopedRooms(result, activeProjectId)); setError(''); } }
    catch (failure) { if (alive.current) setError(failure.message); }
    finally { if (alive.current) setLoading(false); }
  }, [activeProjectId]);
  useEffect(() => { alive.current = true; refresh(); const timer = setInterval(refresh, 15000); return () => { alive.current = false; clearInterval(timer); }; }, [refresh]);
  useEffect(() => { localStorage.setItem(groupScopeKey(activeProjectId), JSON.stringify({ roomId })); }, [activeProjectId, roomId]);
  const selectRoom = (id) => { onClearArtifacts(); setRoomId(id); };
  const defaultMembers = useMemo(() => temporaryMembers((models || []).slice(0, 4)), [models]);
  const canQuickCreate = !team?.id && !creating && !loading && !teamPending && Boolean(title.trim()) && defaultMembers.length >= 2;
  const quickCreateRoom = async () => {
    if (!canQuickCreate) return;
    setCreating(true); setError('');
    try {
      const createdTeam = await harnessApi.createTeam(teamRequest({
        name: `${title.trim().slice(0, 97)}协作组`,
        description: '快速创建的默认协作组，可在群顶部「成员」随时调整',
        members: defaultMembers,
      }, activeProjectId));
      const savedTeam = createdTeam.team || createdTeam;
      if (alive.current) setTeam(savedTeam);
      const room = await groupApi.create({ title: title.trim(), project_id: activeProjectId || null, team_id: savedTeam.id });
      if (alive.current) { setRooms((previous) => scopedRooms([room, ...previous], activeProjectId)); selectRoom(room.id); }
    } catch (failure) { if (alive.current) setError(failure.message); }
    finally { if (alive.current) setCreating(false); }
  };
  const createRoom = async (event) => {
    event.preventDefault();
    if (!team?.id) {
      if (canQuickCreate) await quickCreateRoom();
      return;
    }
    if (!title.trim() || teamPending || creating) return;
    setCreating(true); setError('');
    try {
      const room = await groupApi.create({ title: title.trim(), project_id: activeProjectId || null, team_id: team.id });
      if (alive.current) { setRooms((previous) => scopedRooms([room, ...previous], activeProjectId)); selectRoom(room.id); }
    } catch (failure) { if (alive.current) setError(failure.message); }
    finally { if (alive.current) setCreating(false); }
  };
  const selectedRoom = rooms.find((room) => room.id === roomId);
  const header = { rooms, roomTitle: selectedRoom?.title, selectRoom, onOpenNavigation, error, refresh };
  return <main className={`workspace group-workspace ${focusHistory ? 'group-focus-mode' : ''}`}>
    {roomId ? <GroupRoom onOpenTodo={onOpenTodo} key={`${activeProjectId || 'unassigned'}:${roomId}:${initialJob?.group_room_id === roomId ? initialJob.id : ''}`} header={header} initialJob={initialJob?.group_room_id === roomId && (initialJob.project_id || null) === (activeProjectId || null) ? initialJob : null} roomId={roomId} projectId={activeProjectId} status={status} models={models} onOpenAgentSettings={onOpenAgentSettings} context={context} onContextChange={onContextChange} onOpenArtifacts={onOpenArtifacts} onClearArtifacts={onClearArtifacts} onCloseArtifacts={onCloseArtifacts} onResultShortcutChange={onResultShortcutChange} artifactsVisible={artifactsVisible} onOpenProjects={onOpenProjects} renderIntegration={renderIntegration} focusHistory={focusHistory} onToggleFocusHistory={toggleFocusHistory} /> : <>
      <GroupHeader {...header} onOpenFeishuSettings={!REMOTE_CLIENT && onOpenAgentSettings ? () => onOpenAgentSettings('feishu') : undefined} />
      {error && <div className="error-banner" role="alert">{error}<button type="button" className="subtle-button" onClick={refresh}>重试</button></div>}
      <div className="group-create-scroll">
        {rooms.length > 0 && (
          <nav className="group-existing-rooms-bar" aria-label="当前项目已有群聊">
            <span className="group-existing-rooms-label">当前项目已有群聊：</span>
            <div className="group-existing-rooms-list">
              {rooms.slice(0, 6).map((room) => (
                <button
                  key={room.id}
                  type="button"
                  className="group-existing-room-pill"
                  onClick={() => selectRoom(room.id)}
                >
                  <Icon name="group" size={13} />
                  <span>{room.title}</span>
                </button>
              ))}
              {rooms.length > 6 && <span className="group-existing-rooms-more">等 {rooms.length} 个（可在顶部切换）</span>}
            </div>
          </nav>
        )}
        <form className="group-create-room" onSubmit={createRoom}>
          <div className="mission-introduction"><h2>创建项目群聊</h2><p>{activeProject?.name || '独立项目'} · 选择一起讨论或执行任务的成员。</p></div>
          <label className="group-title-input"><span>群聊名称</span><input aria-label="群聊名称" value={title} maxLength={100} placeholder="例如：产品方案讨论、发布前审查" onChange={(event) => setTitle(event.target.value)} /></label>
          <MissionTeamPicker projectId={activeProjectId} team={team} models={models} catalog={status?.models || []} onChange={chooseTeam} onPending={setTeamPending} onOpenAgentSettings={onOpenAgentSettings} allowGroupAgents requireSaved />
          <div className={`group-create-actions${!team?.id ? ' needs-team' : ''}`}>
            <p>{team?.id ? '建群后会保存当前成员与分工，已有讨论不受后续编辑影响。' : '还未选定已保存协作组：可直接一键按上方默认成员建群，或点击上方「成员与分工」自定义成员。'}</p>
            {team?.id
              ? <button type="submit" className="primary-button" disabled={creating || loading || !title.trim() || !team?.id || teamPending}><Icon name="plus" size={15} />{creating ? '正在创建' : '创建群聊'}</button>
              : <button type="button" className="primary-button" disabled={!canQuickCreate} onClick={quickCreateRoom}><Icon name="plus" size={15} />{creating ? '正在创建' : '一键建群（保存默认协作组）'}</button>}
          </div>
        </form>
      </div>
    </>}
  </main>;
}
