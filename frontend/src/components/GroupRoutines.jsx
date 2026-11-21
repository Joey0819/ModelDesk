import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { workbenchApi } from '../api';
import { REMOTE_CLIENT } from '../api-base';
import { DOCUMENT_FORMATS } from '../document-export';
import { groupApi } from '../group-api';
import { newGroupRequestId } from '../group-state';
import {
  ROUTINE_BOARD_STATUSES,
  ROUTINE_FEEDBACK_VERDICTS,
  ROUTINE_INTERVAL_PRESETS,
  ROUTINE_RUN_STATES,
  ROUTINE_TRIGGERS,
  ROUTINE_WEEKDAYS,
  readRoutineStorage,
  routineDateLabel,
  routineDraft,
  routineDurationLabel,
  routineScheduled,
  routineSpec,
  routineStorageKey,
  routineTimed,
  routineTriggerLabel,
  writeRoutineStorage,
} from '../group-routine-state';
import RoutineDesigner, { ReadinessStrip } from './RoutineDesigner';
import { Icon } from './WorkbenchResults';
import WorkspaceDialog from './WorkspaceDialog';
import './GroupRoutines.css';

const fieldDescriptions = [
  ['goal', '预期成果', 3000, '先写清最终想得到的结果（而不是具体动作），例如：一页简报，列出本周进度、风险和待确认事项'],
  ['process', '执行步骤', 2500, '例如：读取本项目资料与看板进度，汇总变化并注明来源'],
  ['judgment', '决策判断与求助边界', 2000, '例如：两处时间或数字冲突时以看板为准；缺少关键信息时记录待补并问我，不要自行猜测'],
  ['checks', '验收标准', 2500, '例如：核对数字和来源，明确标出缺失信息；只有结果完整落盘才算完成'],
  ['examples', '真实案例与反例', 2000, '例如：上次客户改期未同步日程导致漏单 → 必须核对最终确认记录后再结束'],
];

const triggerRunLabels = {
  manual: '手动',
  daily: '每日定时',
  weekly: '每周定时',
  interval: '固定间隔',
  board_card: '看板新卡片',
  board_status: '看板状态变更',
};
const uncertain = (error) => !error.status || error.status >= 500 || [401, 408, 425, 429].includes(error.status);

function RoutineEditor({
  roomId, projectId, draft, setDraft, composerDraft, busy, error, conflict,
  onSave, onBack, onRemove, onOpenDesigner,
}) {
  const [confirmingRemoval, setConfirmingRemoval] = useState(false);
  const [revisions, setRevisions] = useState(null);
  const [revisionsLoading, setRevisionsLoading] = useState(false);
  const removalNotice = useRef(null);
  useLayoutEffect(() => { if (confirmingRemoval) removalNotice.current?.focus({ preventScroll: true }); }, [confirmingRemoval]);
  const spec = draft.specification;
  const trigger = spec.trigger;
  const timed = routineTimed(trigger.kind);
  const locked = Boolean(busy || draft.requestId || confirmingRemoval);
  const update = (patch) => setDraft({ ...draft, specification: { ...spec, ...patch } });
  const updateTrigger = (patch) => update({ trigger: { ...trigger, ...patch } });
  const canUseComposer = !draft.id && !spec.goal.trim() && composerDraft.prompt.trim();

  const loadRevisions = async () => {
    if (!draft.id || revisions !== null || revisionsLoading) return;
    setRevisionsLoading(true);
    try {
      const data = await groupApi.routineRevisions(roomId, projectId, draft.id);
      setRevisions(data.revisions || []);
    } catch {
      setRevisions([]);
    } finally {
      setRevisionsLoading(false);
    }
  };

  return <form className="group-routine-editor" onSubmit={confirmingRemoval ? (event) => event.preventDefault() : onSave}>
    <div className="group-routines-body">
      <div className="group-routines-editor-heading">
        <h3 tabIndex={-1} data-routine-heading>{draft.id ? '编辑例程' : '新建例程'}</h3>
        <div className="group-routine-editor-tools">
          {canUseComposer && <button type="button" className="subtle-button" disabled={locked} onClick={() => update({
            goal: composerDraft.prompt.trim(),
            document_formats: spec.document_formats.length ? spec.document_formats : composerDraft.documentFormats || [],
          })}><Icon name="copy" size={14} />使用群聊草稿</button>}
          <button type="button" className="subtle-button" disabled={locked} onClick={() => onOpenDesigner('interview')}>
            <Icon name="dialogue" size={14} />AI 访谈完善
          </button>
          {spec.goal.trim() && <button type="button" className="subtle-button" disabled={locked} onClick={() => onOpenDesigner('optimize')}>
            <Icon name="refresh" size={14} />优化流程
          </button>}
          {spec.name.trim() && spec.goal.trim() && <button type="button" className="subtle-button" disabled={locked} onClick={() => onOpenDesigner('walkthrough')}>
            <Icon name="council" size={14} />案例演练
          </button>}
        </div>
      </div>

      <ReadinessStrip specification={spec} />

      {draft.requestId && !busy && <p className="group-routine-feedback">上一次保存的回执尚未确认。重试会沿用同一次请求。</p>}
      <fieldset className="group-routine-fields group-routine-description" disabled={locked}>
        <legend className="group-sr-only">例程说明</legend>
        <label>例程名称<input value={spec.name} required maxLength={120} placeholder="例如：每周项目简报" onChange={(event) => update({ name: event.target.value })} /></label>
        {fieldDescriptions.map(([id, label, maxLength, placeholder]) => <label key={id}>{label}<textarea rows={2} value={spec[id]} required={id === 'goal'} maxLength={maxLength} placeholder={placeholder} onChange={(event) => update({ [id]: event.target.value })} /></label>)}
      </fieldset>
      <fieldset className="group-routine-fields group-routine-settings" disabled={locked}>
        <legend>触发、追踪与交付</legend>
        <div className="group-routine-trigger-row">
          <label>触发方式<select value={trigger.kind} onChange={(event) => updateTrigger({ kind: event.target.value })}>
            {Object.entries(ROUTINE_TRIGGERS).map(([kind, label]) => <option key={kind} value={kind}>{label}</option>)}
          </select></label>
          <label>关联追踪编号字段（可选）
            <input value={spec.correlation_field} maxLength={80} placeholder="例如：客户邮箱、订单号、需求编号（用于跨运行关联与防重）" onChange={(event) => update({ correlation_field: event.target.value })} />
          </label>
        </div>
        {timed && <>
          <div className="group-routine-schedule">
            {trigger.kind === 'weekly' && <label>每周哪天<select value={trigger.weekday} onChange={(event) => updateTrigger({ weekday: Number(event.target.value) })}>
              {ROUTINE_WEEKDAYS.map((weekday, index) => <option key={weekday} value={index}>{weekday}</option>)}
            </select></label>}
            <label>运行时间<input type="time" value={trigger.time} required onChange={(event) => updateTrigger({ time: event.target.value })} /></label>
            <label>时区<input value={trigger.timezone} required maxLength={100} placeholder="Asia/Shanghai" spellCheck={false} autoComplete="off" onChange={(event) => updateTrigger({ timezone: event.target.value })} /></label>
          </div>
          <p className="group-routine-hint">按所选时区运行。服务恢复后会尝试补跑最近一次错过的计划。</p>
        </>}
        {trigger.kind === 'interval' && <>
          <div className="group-routine-interval">
            <label>间隔小时数<input type="number" min={1} max={720} required value={trigger.every_hours ?? 48} onChange={(event) => updateTrigger({ every_hours: Number(event.target.value) || 1 })} /></label>
            <div className="group-routine-presets">
              {ROUTINE_INTERVAL_PRESETS.map(([hours, label]) => <button
                key={hours}
                type="button"
                className="subtle-button"
                data-active={Number(trigger.every_hours) === hours}
                onClick={() => updateTrigger({ every_hours: hours })}
              >{label}</button>)}
            </div>
          </div>
          <p className="group-routine-hint">从启用时刻起按固定间隔运行（例如每 48 小时即每隔一天）。服务恢复后最多补跑最近一次。</p>
        </>}
        {trigger.kind === 'board_card' && <>
          <label>卡片标签<input value={trigger.tag} required maxLength={100} placeholder="例如：周报" spellCheck={false} onChange={(event) => updateTrigger({ tag: event.target.value })} /></label>
          <p className="group-routine-hint">只在本群看板手动新建卡片时检查标签。标签须完全相同，之后添加标签不会触发。{spec.allow_native && ' 此例程只接受本机新建的卡片。'}</p>
        </>}
        {trigger.kind === 'board_status' && <>
          <div className="group-routine-schedule">
            <label>进入看板列<select value={trigger.board_status || 'review'} onChange={(event) => updateTrigger({ board_status: event.target.value })}>
              {Object.entries(ROUTINE_BOARD_STATUSES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select></label>
            <label>限定标签（可选）<input value={trigger.tag || ''} maxLength={100} placeholder="留空表示该列全部手动卡片" spellCheck={false} onChange={(event) => updateTrigger({ tag: event.target.value })} /></label>
          </div>
          <p className="group-routine-hint">当本群看板手动卡片新建或拖入目标列时触发。自动同步的镜像卡片不会循环触发。</p>
        </>}
        <fieldset className="group-routine-formats">
          <legend>同时生成文件 <span>未选时按目标生成</span></legend>
          {DOCUMENT_FORMATS.map(({ id, label }) => <label key={id}>
            <input type="checkbox" checked={spec.document_formats.includes(id)} onChange={(event) => update({
              document_formats: DOCUMENT_FORMATS.filter((item) => item.id === id ? event.target.checked : spec.document_formats.includes(item.id)).map((item) => item.id),
            })} />{id === 'html' ? 'HTML' : label}
          </label>)}
        </fieldset>
        <div className="group-routine-native">
          <label><input type="checkbox" checked={Boolean(spec.auto_reflect)} onChange={(event) => update({ auto_reflect: event.target.checked })} />异常后自动生成改进建议草稿</label>
          <p className="group-routine-hint">运行出现异常时自动分析根因并生成建议卡片；仍需你审阅确认后才会更新例程。</p>
        </div>
        {!REMOTE_CLIENT && <div className="group-routine-native">
          <label><input type="checkbox" checked={spec.allow_native} onChange={(event) => update({ allow_native: event.target.checked })} />允许调用本机 Agent</label>
          {spec.allow_native && <p className="group-routine-hint">使用本群已连接的执行器。代码修改仍需你的审阅与合入审批。</p>}
        </div>}
      </fieldset>

      {draft.id && <details className="group-routine-revisions" onToggle={(event) => { if (event.currentTarget.open) void loadRevisions(); }}>
        <summary>查看版本历史（当前 v{draft.revision}）</summary>
        {revisionsLoading && <p className="group-routine-hint">正在读取版本记录…</p>}
        {revisions && revisions.length === 0 && <p className="group-routine-hint">暂无历史快照。</p>}
        {revisions && revisions.length > 0 && <ol>{revisions.map((item) => <li key={item.id}>
          <div>
            <strong>v{item.revision} · {item.change_note}</strong>
            <time dateTime={item.created_at}>{routineDateLabel(item.created_at)}</time>
          </div>
          {item.revision !== draft.revision && <button type="button" className="subtle-button" disabled={locked} onClick={() => setDraft({
            ...draft,
            specification: routineDraft(item.spec).specification,
          })}>载入此版本到编辑器</button>}
        </li>)}</ol>}
      </details>}
    </div>
    <footer className="group-routine-editor-footer">
      {error && <p role="alert" className="group-routine-error">{error}</p>}
      {conflict && <details className="group-routine-conflict"><summary>查看服务端最新配置</summary>
        <dl><dt>例程名称</dt><dd>{conflict.name}</dd><dt>预期成果</dt><dd>{conflict.goal}</dd><dt>执行步骤</dt><dd>{conflict.process}</dd><dt>决策判断</dt><dd>{conflict.judgment}</dd><dt>验收标准</dt><dd>{conflict.checks}</dd><dt>真实案例</dt><dd>{conflict.examples}</dd><dt>触发方式</dt><dd>{routineTriggerLabel(conflict)}</dd><dt>文件</dt><dd>{(conflict.document_formats || []).join('、') || '无'}</dd><dt>本机 Agent</dt><dd>{conflict.allow_native ? '允许' : '关闭'}</dd></dl>
      </details>}
      {confirmingRemoval ? <>
        <p className="group-routine-remove-note" ref={removalNotice} tabIndex={-1}>删除此例程后，自动触发会停止。已经创建的任务与成果仍可在群聊中查看。</p>
        <div><button type="button" className="subtle-button" disabled={Boolean(busy)} onClick={() => setConfirmingRemoval(false)}>取消删除</button>
          <button type="button" className="subtle-button group-routine-remove" disabled={Boolean(busy)} onClick={onRemove}>{busy ? '正在删除…' : '确认删除'}</button></div>
      </> : <div>
        {draft.id && (!REMOTE_CLIENT || !spec.allow_native) && <button type="button" className="subtle-button group-routine-remove group-routine-remove-trigger" disabled={Boolean(busy || conflict)} onClick={() => setConfirmingRemoval(true)}>删除例程</button>}
        <button type="button" className="subtle-button" disabled={Boolean(busy || draft.requestId)} onClick={onBack}>取消编辑</button>
        <button type="submit" className="primary-button" disabled={Boolean(busy || (REMOTE_CLIENT && spec.allow_native))}>
          {busy ? '正在保存…' : draft.requestId ? '重试保存' : conflict ? '保存我的修改' : '保存例程'}
        </button>
      </div>}
    </footer>
  </form>;
}

function RunFeedbackForm({ run, busy, onSubmit, onCancel }) {
  const [verdict, setVerdict] = useState(run.state === 'complete' ? 'good' : 'needs_improvement');
  const [note, setNote] = useState('');
  const [correlationKey, setCorrelationKey] = useState(run.correlation_key || '');
  const [markWaiting, setMarkWaiting] = useState(run.case_state === 'waiting_details');
  return <form className="group-routine-feedback-form" onSubmit={(event) => {
    event.preventDefault();
    onSubmit({
      verdict,
      note: note.trim(),
      source: 'user',
      correlation_key: correlationKey.trim() || null,
      case_state: markWaiting ? 'waiting_details' : (run.case_state === 'waiting_details' ? 'resolved' : null),
    });
  }}>
    <div className="group-routine-verdict-row">
      {Object.entries(ROUTINE_FEEDBACK_VERDICTS).map(([key, label]) => <label key={key}>
        <input type="radio" name={`verdict-${run.id}`} checked={verdict === key} onChange={() => setVerdict(key)} />
        <span>{label}</span>
      </label>)}
    </div>
    <textarea
      rows={2}
      value={note}
      maxLength={2000}
      required={verdict !== 'good'}
      placeholder={verdict === 'good' ? '可选：记下这次做得好的点或外部评价' : '具体说明哪里不满意、什么判断规则没说清，便于自动复盘改进'}
      onChange={(event) => setNote(event.target.value)}
    />
    <div className="group-routine-feedback-extras">
      <input
        value={correlationKey}
        maxLength={120}
        placeholder="关联编号（可选）"
        onChange={(event) => setCorrelationKey(event.target.value)}
      />
      <label className="group-routine-inline-check">
        <input type="checkbox" checked={markWaiting} onChange={(event) => setMarkWaiting(event.target.checked)} />
        标记为等待补充信息
      </label>
    </div>
    <div className="group-routine-feedback-actions">
      <button type="button" className="subtle-button" disabled={Boolean(busy)} onClick={onCancel}>取消</button>
      <button type="submit" className="primary-button" disabled={Boolean(busy)}>提交反馈</button>
    </div>
  </form>;
}

function RoutineCard({
  routine, busy, pendingRun, opened,
  onRun, onEdit, onToggle, onOpenJob, onReflect, onApplyProposal, onDismissProposal, onFeedback, onCaseState,
}) {
  const [feedbackRunId, setFeedbackRunId] = useState(null);
  const [runOptionsOpen, setRunOptionsOpen] = useState(false);
  const [correlationKey, setCorrelationKey] = useState('');
  const [inputNote, setInputNote] = useState('');
  const [resumeCase, setResumeCase] = useState(false);

  const { trigger } = routine;
  const scheduled = routineScheduled(trigger.kind);
  const timezone = routineTimed(trigger.kind) ? trigger.timezone : undefined;
  const next = routineDateLabel(routine.next_run_at, timezone);
  const nativeOnRemote = REMOTE_CLIENT && routine.allow_native;
  const runs = routine.recent_runs || [];
  const latest = runs[0];
  const canTrigger = trigger.kind !== 'manual' || routine.enabled;
  const disabled = Boolean(busy || nativeOnRemote);
  const telemetry = routine.telemetry;
  const proposals = routine.open_proposals || [];
  const hasSignals = Boolean(telemetry && (telemetry.total_runs > 0 || Object.values(telemetry.feedback_counts || {}).some((n) => n > 0)));

  return <article className="group-routine-card">
    <header>
      <h3>{routine.name}</h3>
      <div className="group-routine-card-chips">
        {routine.readiness && <span className="group-routine-chip">就绪 {routine.readiness.score}/{routine.readiness.total}</span>}
        {canTrigger && <span className="group-routine-chip" data-enabled={routine.enabled}>{routine.enabled ? '已启用' : '未启用'}</span>}
      </div>
    </header>
    <p className="group-routine-goal">{routine.goal}</p>
    <div className="group-routine-trigger"><Icon name={trigger.kind.startsWith('board') ? 'dashboard' : 'clock'} size={14} /><span>{routineTriggerLabel(routine)}</span></div>
    {routine.enabled && scheduled && next && <p className="group-routine-hint">下次运行 <time dateTime={routine.next_run_at}>{next}</time></p>}
    {routine.team_changed && <p className="group-routine-hint">群成员已更新。请检查并保存此例程，再运行或启用。</p>}

    {telemetry && telemetry.total_runs > 0 && <div className="group-routine-telemetry" aria-label="运行指标">
      <span>累计 {telemetry.total_runs} 次</span>
      {telemetry.success_rate != null && <span>成功率 {Math.round(telemetry.success_rate * 100)}%</span>}
      {telemetry.avg_run_seconds != null && <span>均耗时 {routineDurationLabel(telemetry.avg_run_seconds)}</span>}
      {(telemetry.feedback_counts?.needs_improvement + telemetry.feedback_counts?.wrong_judgment) > 0 && (
        <span className="group-routine-telemetry-alert">待改进反馈 {telemetry.feedback_counts.needs_improvement + telemetry.feedback_counts.wrong_judgment}</span>
      )}
      {telemetry.waiting_cases?.length > 0 && (
        <span className="group-routine-telemetry-alert">待补信息 {telemetry.waiting_cases.length} 项</span>
      )}
    </div>}

    {proposals.length > 0 && <section className="group-routine-proposals" aria-label="待审阅的自改进建议">
      {proposals.map((proposal) => <details key={proposal.id} className="group-routine-proposal-card" open>
        <summary>
          <strong>待确认改进建议</strong>
          <span>{proposal.trigger_source === 'auto_reflect' ? '异常自动复盘' : '手动复盘'} · {proposal.diff?.length || 0} 处修改</span>
        </summary>
        <p className="group-routine-proposal-diagnosis">{proposal.diagnosis}</p>
        {proposal.changes_summary?.length > 0 && <ul>{proposal.changes_summary.map((item, idx) => <li key={idx}>{item}</li>)}</ul>}
        {proposal.diff?.length > 0 && <div className="group-routine-diff">
          {proposal.diff.map((entry) => <div key={entry.field} className="group-routine-diff-item">
            <strong>{entry.label}</strong>
            {entry.before && <p className="group-routine-diff-before">原：{entry.before}</p>}
            <p className="group-routine-diff-after">新：{entry.after}</p>
          </div>)}
        </div>}
        {!nativeOnRemote && <div className="group-routine-actions">
          <button type="button" className="primary-button" disabled={disabled} onClick={() => onApplyProposal(proposal)}>采纳改进</button>
          <button type="button" className="subtle-button" disabled={disabled} onClick={() => onDismissProposal(proposal)}>忽略</button>
        </div>}
      </details>)}
    </section>}

    {nativeOnRemote ? <p className="group-routine-hint">使用本机 Agent 的例程请在桌面端管理。</p> : <div className="group-routine-actions">
      <button type="button" className="subtle-button group-routine-run" disabled={disabled || (routine.team_changed && !pendingRun)} onClick={() => onRun()}>{pendingRun ? '重试运行请求' : '运行一次'}</button>
      {routine.correlation_field && !pendingRun && <button type="button" className="subtle-button" disabled={disabled || routine.team_changed} onClick={() => setRunOptionsOpen((value) => !value)}>带编号运行</button>}
      {canTrigger && <button type="button" className="subtle-button" disabled={disabled || (routine.team_changed && !routine.enabled)} onClick={onToggle}>{routine.enabled ? '暂停' : '启用'}</button>}
      {hasSignals && <button type="button" className={`subtle-button${telemetry?.needs_reflection ? ' group-routine-reflect-due' : ''}`} disabled={disabled} onClick={onReflect}>
        {busy === `reflect:${routine.id}` ? '正在复盘…' : '复盘改进'}
      </button>}
      <button type="button" className="subtle-button" disabled={Boolean(disabled || pendingRun)} onClick={onEdit}>编辑</button>
    </div>}

    {runOptionsOpen && !nativeOnRemote && <form className="group-routine-run-options" onSubmit={(event) => {
      event.preventDefault();
      onRun({ correlation_key: correlationKey.trim() || null, input_note: inputNote.trim() || null, resume_case: resumeCase });
      setRunOptionsOpen(false);
      setInputNote('');
      setResumeCase(false);
    }}>
      <input value={correlationKey} maxLength={120} placeholder={`关联编号（${routine.correlation_field}）`} onChange={(event) => setCorrelationKey(event.target.value)} />
      <input value={inputNote} maxLength={1000} placeholder="本次补充说明（可选）" onChange={(event) => setInputNote(event.target.value)} />
      <label className="group-routine-inline-check">
        <input type="checkbox" checked={resumeCase} onChange={(event) => setResumeCase(event.target.checked)} />
        续办该编号的待补信息事项
      </label>
      <div className="group-routine-feedback-actions">
        <button type="button" className="subtle-button" onClick={() => setRunOptionsOpen(false)}>取消</button>
        <button type="submit" className="primary-button" disabled={disabled}>按此编号运行</button>
      </div>
    </form>}

    {runs.length > 0 && <details className="group-routine-history" open={opened || undefined}>
      <summary><span>最近运行</span><strong>{ROUTINE_RUN_STATES[latest.state] || '状态待同步'}</strong></summary>
      <ol>{runs.map((run) => {
        const terminal = ['complete', 'needs_attention', 'cancelled'].includes(run.state);
        const timing = [
          run.queue_seconds != null ? `排队 ${routineDurationLabel(run.queue_seconds)}` : '',
          run.run_seconds != null ? `耗时 ${routineDurationLabel(run.run_seconds)}` : '',
        ].filter(Boolean).join(' · ');
        return <li key={run.id}>
          <div className="group-routine-run-heading">
            <strong>{ROUTINE_RUN_STATES[run.state] || '状态待同步'}</strong>
            <span>{triggerRunLabels[run.trigger_kind] || run.trigger_kind} · <time dateTime={run.created_at}>{routineDateLabel(run.created_at, timezone)}</time></span>
          </div>
          {(timing || run.correlation_key || run.case_state === 'waiting_details') && <div className="group-routine-run-meta">
            {timing && <span>{timing}</span>}
            {run.correlation_key && <span className="group-routine-chip">#{run.correlation_key}</span>}
            {run.case_state === 'waiting_details' && <span className="group-routine-chip" data-waiting="true">等待补充信息</span>}
          </div>}
          {run.reason && <p className="group-routine-hint">{run.reason}</p>}
          <div className="group-routine-run-actions">
            {run.job_id && <button type="button" className="subtle-button" disabled={Boolean(busy)} onClick={() => onOpenJob(run.job_id)}>查看运行记录</button>}
            {terminal && !nativeOnRemote && <button type="button" className="subtle-button" disabled={Boolean(busy)} onClick={() => setFeedbackRunId((current) => current === run.id ? null : run.id)}>
              {feedbackRunId === run.id ? '收起反馈' : '记录反馈'}
            </button>}
            {run.case_state === 'waiting_details' && !nativeOnRemote && <button type="button" className="subtle-button" disabled={Boolean(busy)} onClick={() => onCaseState(run, 'resolved')}>
              标记已补齐
            </button>}
          </div>
          {run.feedback?.length > 0 && <ul className="group-routine-feedback-list">
            {run.feedback.map((entry) => <li key={entry.id}>
              <strong>{ROUTINE_FEEDBACK_VERDICTS[entry.verdict] || entry.verdict}</strong>
              {entry.note && <span>：{entry.note}</span>}
            </li>)}
          </ul>}
          {feedbackRunId === run.id && <RunFeedbackForm
            run={run}
            busy={busy}
            onCancel={() => setFeedbackRunId(null)}
            onSubmit={async (payload) => {
              const ok = await onFeedback(run, payload);
              if (ok) setFeedbackRunId(null);
            }}
          />}
        </li>;
      })}</ol>
    </details>}
  </article>;
}

export default function GroupRoutines({ room, composerDraft, onDismiss, onOpenJob, onRefreshRoom }) {
  const roomId = room.id;
  const projectId = room.project_id;
  const draftKey = routineStorageKey(projectId, roomId, 'draft');
  const [draft, setDraft] = useState(() => {
    const saved = readRoutineStorage(draftKey);
    return saved?.specification?.trigger ? saved : null;
  });
  const [designer, setDesigner] = useState(null);
  const [runReceipts, setRunReceipts] = useState({});
  const [routines, setRoutines] = useState(null);
  const [readError, setReadError] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [conflict, setConflict] = useState(null);
  const [busy, setBusy] = useState('');
  const [opened, setOpened] = useState(null);
  const alive = useRef(false);
  const mutating = useRef(false);
  const read = useRef({ sequence: 0, busy: false });
  const title = useRef(null);
  const frame = useRef(null);
  const editing = Boolean(draft) && !designer;
  useLayoutEffect(() => {
    if (designer) return;
    const next = editing ? frame.current?.querySelector('[data-routine-heading]') : title.current;
    next?.focus({ preventScroll: true });
  }, [editing, designer]);
  const saveDraft = (next) => { writeRoutineStorage(draftKey, next); setDraft(next); };
  const pendingKey = useCallback((id) => routineStorageKey(projectId, roomId, id), [projectId, roomId]);
  const belongsToRoom = useCallback((item) => item?.id && item.room_id === roomId && (item.project_id || null) === (projectId || null), [projectId, roomId]);
  const clearMessage = () => { setNotice(''); setError(''); setConflict(null); };

  const refresh = useCallback(async (force = false) => {
    if (read.current.busy && !force) return;
    const sequence = ++read.current.sequence;
    read.current.busy = true;
    try {
      const response = await groupApi.routines(roomId, projectId);
      if (!alive.current || read.current.sequence !== sequence) return;
      const items = response.routines;
      if (!Array.isArray(items) || !items.every(belongsToRoom)) throw new Error('群聊服务未返回本群的例程列表。');
      setRoutines(items);
      setReadError('');
      const savedDraft = readRoutineStorage(draftKey);
      if (savedDraft?.requestId && items.some((routine) => routine.client_request_id === savedDraft.requestId)) {
        writeRoutineStorage(draftKey, null);
        setDraft((current) => current?.requestId === savedDraft.requestId ? null : current);
        setError('');
        setNotice('已确认例程保存成功。');
      }
      for (const routine of items) {
        const key = pendingKey(routine.id);
        const requestId = readRoutineStorage(key);
        if (requestId && routine.recent_runs?.some((run) => run.client_request_id === requestId)) {
          writeRoutineStorage(key, null);
          setRunReceipts((current) => ({ ...current, [routine.id]: null }));
          setOpened(routine.id);
          setError('');
          setNotice('已确认运行请求。请在最近运行中查看进度和成果。');
        }
      }
      return items;
    } catch (failure) {
      if (alive.current && read.current.sequence === sequence) setReadError(failure.message);
    } finally {
      if (read.current.sequence === sequence) read.current.busy = false;
    }
  }, [belongsToRoom, draftKey, pendingKey, projectId, roomId]);

  useEffect(() => {
    const readState = read.current;
    alive.current = true;
    void refresh();
    const timer = setInterval(refresh, 4500);
    return () => {
      alive.current = false;
      readState.sequence += 1;
      readState.busy = false;
      clearInterval(timer);
    };
  }, [refresh]);

  const mutate = async (action, operation) => {
    if (mutating.current) return false;
    mutating.current = true;
    setBusy(action);
    clearMessage();
    try {
      await operation();
      return true;
    } catch (failure) {
      if (alive.current) setError(failure.message);
      return false;
    } finally {
      mutating.current = false;
      if (alive.current) setBusy('');
    }
  };

  const putRoutine = (item) => setRoutines((previous) => {
    const list = previous || [];
    return list.some((entry) => entry.id === item.id) ? list.map((entry) => entry.id === item.id ? item : entry) : [item, ...list];
  });
  const finishSave = (item, wasEdit) => {
    if (!belongsToRoom(item)) throw new Error('群聊服务未返回本群的有效保存回执。');
    if (!alive.current) return;
    saveDraft(null);
    putRoutine(item);
    setNotice(wasEdit ? '例程已更新。' : '例程已保存。可以先运行一次，再设置或启用自动触发。');
    void refresh(true);
  };

  const save = (event) => {
    event.preventDefault();
    const spec = routineSpec(draft.specification);
    if (!spec.name || !spec.goal) {
      setError('请填写例程名称和预期成果。');
      return;
    }
    if (Array.from(spec.goal).length > 3000) {
      setError('请将预期成果缩短至 3000 字以内。');
      return;
    }
    const totalLength = Array.from(spec.goal + spec.process + spec.judgment + spec.checks + spec.examples).length;
    if (totalLength > 7000) {
      setError('目标、步骤、判断规则、案例和验收标准合计不能超过 7000 字。');
      return;
    }
    const expectedRevision = conflict?.revision ?? draft.revision;
    void mutate('save', async () => {
      if (draft.id) {
        try {
          const saved = await groupApi.updateRoutine(roomId, projectId, draft.id, { ...spec, expected_revision: expectedRevision });
          finishSave(saved, true);
        } catch (failure) {
          if (failure.status === 409) {
            const latest = (await refresh(true))?.find((item) => item.id === draft.id);
            if (latest && !latest.team_changed && JSON.stringify(routineSpec(routineDraft(latest).specification)) === JSON.stringify(spec)) {
              finishSave(latest, true);
              return;
            }
            if (alive.current && latest) {
              setConflict(latest);
              throw new Error('例程已有其他修改，当前草稿已保留。请先核对服务端最新配置。');
            }
          }
          throw failure;
        }
        return;
      }

      const requestId = draft.requestId || newGroupRequestId();
      if (!draft.requestId) saveDraft({ ...draft, requestId });
      try {
        const saved = await groupApi.createRoutine(roomId, projectId, { ...spec, client_request_id: requestId });
        if (saved?.client_request_id !== requestId) throw new Error('未收到例程保存回执。');
        finishSave(saved, false);
      } catch (failure) {
        if (!alive.current) return;
        if (!uncertain(failure)) {
          saveDraft({ ...draft, requestId: undefined });
          throw failure;
        }
        const saved = (await refresh(true))?.find((item) => item.client_request_id === requestId);
        if (saved) finishSave(saved, false);
        else throw new Error('暂时无法确认例程是否已保存，可使用同一次回执重试。');
      }
    });
  };

  const run = (routine, extra = {}) => void mutate('run', async () => {
    const key = pendingKey(routine.id);
    const requestId = runReceipts[routine.id] || readRoutineStorage(key) || newGroupRequestId();
    setRunReceipts((current) => ({ ...current, [routine.id]: requestId }));
    writeRoutineStorage(key, requestId);
    const payload = {
      client_request_id: requestId,
      ...(extra.correlation_key ? { correlation_key: extra.correlation_key } : {}),
      ...(extra.input_note ? { input_note: extra.input_note } : {}),
      ...(extra.resume_case ? { resume_case: true } : {}),
    };
    let receipt;
    try {
      receipt = await groupApi.runRoutine(roomId, projectId, routine.id, payload);
      if (!receipt?.id || receipt.client_request_id !== requestId) throw new Error('未收到有效的运行回执。');
    } catch (failure) {
      if (!uncertain(failure)) {
        if (readRoutineStorage(key) === requestId) writeRoutineStorage(key, null);
        if (alive.current) setRunReceipts((current) => ({ ...current, [routine.id]: null }));
        void refresh(true);
        throw failure;
      }
      const latest = (await refresh(true))?.find((item) => item.id === routine.id);
      receipt = latest?.recent_runs?.find((item) => item.client_request_id === requestId);
      if (!receipt) throw new Error('暂时无法确认运行请求，可使用同一次回执重试。');
    }
    if (readRoutineStorage(key) === requestId) writeRoutineStorage(key, null);
    if (!alive.current) return;
    setRunReceipts((current) => ({ ...current, [routine.id]: null }));
    setOpened(routine.id);
    setNotice('已收到运行请求。请在最近运行中查看进度和成果。');
    setRoutines((previous) => previous?.map((item) => item.id === routine.id && !item.recent_runs?.some((entry) => entry.id === receipt.id)
      ? { ...item, recent_runs: [receipt, ...(item.recent_runs || [])] } : item));
    void refresh(true);
    void onRefreshRoom();
  });

  const toggle = (routine) => void mutate('toggle', async () => {
    const enabled = !routine.enabled;
    let updated;
    try {
      updated = await groupApi.setRoutineEnabled(roomId, projectId, routine.id, { enabled, expected_revision: routine.revision });
    } catch (failure) {
      updated = (await refresh(true))?.find((item) => item.id === routine.id && item.enabled === enabled);
      if (!updated) throw failure;
    }
    if (!belongsToRoom(updated) || updated.id !== routine.id || updated.enabled !== enabled) throw new Error('未收到有效的例程状态回执。');
    if (!alive.current) return;
    putRoutine(updated);
    setNotice(enabled ? '自动触发已启用。' : '已暂停自动触发。');
    void refresh(true);
  });

  const remove = () => void mutate('remove', async () => {
    try {
      const result = await groupApi.removeRoutine(roomId, projectId, draft.id, draft.revision);
      if (result?.removed !== true) throw new Error('未收到例程删除回执。');
    } catch (failure) {
      const latest = await refresh(true);
      if (!latest || latest.some((routine) => routine.id === draft.id)) throw failure;
    }
    if (!alive.current) return;
    saveDraft(null);
    setRoutines((previous) => previous?.filter((item) => item.id !== draft.id) || []);
    setNotice('例程已删除，群聊中的运行记录已保留。');
    void refresh(true);
  });

  const reflect = (routine) => void mutate(`reflect:${routine.id}`, async () => {
    const response = await groupApi.reflectRoutine(roomId, projectId, routine.id);
    if (!alive.current) return;
    if (response.routine && belongsToRoom(response.routine)) putRoutine(response.routine);
    setNotice(response.changed ? '已生成改进建议，请在卡片中审阅前后对比后再决定是否采纳。' : `复盘结论：${response.diagnosis || '当前配置暂无需调整。'}`);
    void refresh(true);
  });

  const applyProposal = (routine, proposal) => void mutate('apply-proposal', async () => {
    const response = await groupApi.applyRoutineProposal(roomId, projectId, routine.id, proposal.id, {
      expected_revision: routine.revision,
    });
    if (!alive.current) return;
    if (response.routine && belongsToRoom(response.routine)) putRoutine(response.routine);
    setNotice('已采纳改进建议并生成新版本，可在编辑页查看版本历史。');
    void refresh(true);
  });

  const dismissProposal = (routine, proposal) => void mutate('dismiss-proposal', async () => {
    const response = await groupApi.dismissRoutineProposal(roomId, projectId, routine.id, proposal.id);
    if (!alive.current) return;
    if (response.routine && belongsToRoom(response.routine)) putRoutine(response.routine);
    setNotice('已忽略该改进建议。');
    void refresh(true);
  });

  const submitFeedback = (routine, item, payload) => mutate('feedback', async () => {
    const response = await groupApi.recordRoutineFeedback(roomId, projectId, routine.id, item.id, payload);
    if (!alive.current) return;
    if (response.routine && belongsToRoom(response.routine)) putRoutine(response.routine);
    setNotice(payload.verdict === 'good' ? '反馈已记录。' : '反馈已记录。可以点击「复盘改进」让团队提炼新的判断规则。');
    void refresh(true);
  });

  const setCaseState = (routine, item, caseState) => void mutate('case-state', async () => {
    const response = await groupApi.setRoutineCaseState(roomId, projectId, routine.id, item.id, { case_state: caseState });
    if (!alive.current) return;
    if (response.routine && belongsToRoom(response.routine)) putRoutine(response.routine);
    setNotice(caseState === 'resolved' ? '已标记为信息已补齐。' : '已标记为等待补充信息。');
    void refresh(true);
  });

  const openJob = (id) => void mutate('open', async () => {
    const job = await workbenchApi.getJob(id);
    if (job.id !== id || job.group_room_id !== roomId || (job.project_id || null) !== (projectId || null)) {
      throw new Error('没有找到属于本群的运行记录。');
    }
    if (!alive.current) return;
    onDismiss();
    onOpenJob(job);
  });

  const beginEdit = (routine) => {
    clearMessage();
    setDesigner(null);
    saveDraft(routineDraft(routine));
  };

  const openDesigner = (mode = 'interview', existingDraft = null) => {
    clearMessage();
    setDesigner({ mode, initialDraft: existingDraft });
  };

  return <WorkspaceDialog onDismiss={onDismiss} focusRef={title} aria-labelledby="group-routines-title" className="group-options-dialog group-routines-dialog">
    <div className="group-routines-frame" ref={frame}>
      <header className="group-routines-header">
        <span className="group-routines-icon"><Icon name="clock" size={21} /></span>
        <div><h2 id="group-routines-title" ref={title} tabIndex={-1}>群聊自动化</h2><p title={room.title}>{room.title}</p></div>
        <button type="button" className="icon-button" aria-label="关闭群聊自动化" onClick={onDismiss}><Icon name="close" size={18} /></button>
      </header>
      {designer ? <RoutineDesigner
        roomId={roomId}
        projectId={projectId}
        initialDraft={designer.initialDraft}
        initialMode={designer.mode}
        onBack={() => { setDesigner(null); }}
        onApply={(designedDraft) => {
          setDesigner(null);
          saveDraft(designedDraft);
          setNotice('已将访谈整理好的例程填入编辑器，核对并保存即可。');
        }}
      /> : draft ? <RoutineEditor
        roomId={roomId}
        projectId={projectId}
        draft={draft}
        setDraft={saveDraft}
        composerDraft={composerDraft}
        busy={busy}
        error={error}
        conflict={conflict}
        onSave={save}
        onRemove={remove}
        onOpenDesigner={(mode) => openDesigner(mode, draft)}
        onBack={() => { clearMessage(); saveDraft(null); }}
      /> : <div className="group-routines-body">
        {notice && <p className="group-routine-feedback" role="status">{notice}</p>}
        {error && <p className="group-routine-error" role="alert">{error}</p>}
        {readError && <div className="group-routine-error group-routine-read-error" role="alert">
          <span>{readError}</span><button type="button" className="subtle-button" onClick={() => refresh(true)}>刷新例程</button>
        </div>}
        {!routines && !readError && <p className="group-routine-loading" role="status"><span className="small-spinner" />正在读取例程…</p>}
        {routines?.length === 0 && <section className="group-routine-empty">
          <h3>把重复的工作交给本群</h3>
          <p>先从最终目标出发，写清执行步骤、判断边界和验收标准。可以先让 AI 访谈梳理并演练案例，再按时间、固定间隔或看板变化自动运行。</p>
          <div className="group-routine-actions">
            <button type="button" className="primary-button" onClick={() => beginEdit()}><Icon name="plus" size={15} />新建例程</button>
            <button type="button" className="subtle-button group-routine-design-cta" onClick={() => openDesigner('interview')}><Icon name="dialogue" size={15} />AI 访谈设计</button>
          </div>
          <p className="group-routine-empty-hint">不确定怎么拆分步骤与判断边界？推荐使用「AI 访谈设计」口述目标，自动整理后再微调保存。</p>
        </section>}
        {routines?.length > 0 && <>
          <div className="group-routines-list-heading">
            <h3>本群例程</h3>
            <div className="group-routine-editor-tools">
              <button type="button" className="subtle-button" disabled={Boolean(busy)} onClick={() => openDesigner('interview')}><Icon name="dialogue" size={15} />AI 访谈设计</button>
              <button type="button" className="primary-button" disabled={Boolean(busy)} onClick={() => beginEdit()}><Icon name="plus" size={15} />新建例程</button>
            </div>
          </div>
          <div className="group-routines-list">{routines.map((routine) => {
            const pendingRun = runReceipts[routine.id] || readRoutineStorage(pendingKey(routine.id));
            return <RoutineCard
              key={routine.id}
              routine={routine}
              busy={busy}
              opened={opened === routine.id}
              pendingRun={pendingRun}
              onRun={(extra) => run(routine, extra)}
              onToggle={() => toggle(routine)}
              onEdit={() => beginEdit(routine)}
              onOpenJob={openJob}
              onReflect={() => reflect(routine)}
              onApplyProposal={(proposal) => applyProposal(routine, proposal)}
              onDismissProposal={(proposal) => dismissProposal(routine, proposal)}
              onFeedback={(item, payload) => submitFeedback(routine, item, payload)}
              onCaseState={(item, state) => setCaseState(routine, item, state)}
            />;
          })}</div>
        </>}
      </div>}
    </div>
  </WorkspaceDialog>;
}
