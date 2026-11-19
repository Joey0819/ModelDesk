import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { workbenchApi } from '../api';
import { REMOTE_CLIENT } from '../api-base';
import { DOCUMENT_FORMATS } from '../document-export';
import { groupApi } from '../group-api';
import { newGroupRequestId } from '../group-state';
import {
  ROUTINE_RUN_STATES, ROUTINE_TRIGGERS, ROUTINE_WEEKDAYS,
  readRoutineStorage, routineDateLabel, routineDraft, routineSpec, routineStorageKey,
  routineTimed, routineTriggerLabel, writeRoutineStorage,
} from '../group-routine-state';
import { Icon } from './WorkbenchResults';
import WorkspaceDialog from './WorkspaceDialog';
import './GroupRoutines.css';

const fieldDescriptions = [
  ['goal', '预期成果', 3000, '例如：一页简报，列出本周进度、风险和待确认事项'],
  ['process', '执行步骤', 2500, '例如：读取本项目资料，汇总变化，并注明来源'],
  ['checks', '验收标准', 2500, '例如：核对数字和来源，明确标出缺失信息'],
];

const triggerRunLabels = { manual: '手动', daily: '每日定时', weekly: '每周定时', board_card: '看板新卡片' };
const uncertain = (error) => !error.status || error.status >= 500 || [401, 408, 425, 429].includes(error.status);

function RoutineEditor({ draft, setDraft, composerDraft, busy, error, conflict, onSave, onBack, onRemove }) {
  const [confirmingRemoval, setConfirmingRemoval] = useState(false);
  const removalNotice = useRef(null);
  useLayoutEffect(() => { if (confirmingRemoval) removalNotice.current?.focus({ preventScroll: true }); }, [confirmingRemoval]);
  const spec = draft.specification;
  const trigger = spec.trigger;
  const timed = routineTimed(trigger.kind);
  const locked = Boolean(busy || draft.requestId || confirmingRemoval);
  const update = (patch) => setDraft({ ...draft, specification: { ...spec, ...patch } });
  const updateTrigger = (patch) => update({ trigger: { ...trigger, ...patch } });
  const canUseComposer = !draft.id && !spec.goal.trim() && composerDraft.prompt.trim();

  return <form className="group-routine-editor" onSubmit={confirmingRemoval ? (event) => event.preventDefault() : onSave}>
    <div className="group-routines-body">
      <div className="group-routines-editor-heading">
        <h3 tabIndex={-1} data-routine-heading>{draft.id ? '编辑例程' : '新建例程'}</h3>
        {canUseComposer && <button type="button" className="subtle-button" disabled={locked} onClick={() => update({
          goal: composerDraft.prompt.trim(),
          document_formats: spec.document_formats.length ? spec.document_formats : composerDraft.documentFormats || [],
        })}><Icon name="copy" size={14} />使用群聊草稿</button>}
      </div>
      {draft.requestId && !busy && <p className="group-routine-feedback">上一次保存的回执尚未确认。重试会沿用同一次请求。</p>}
      <fieldset className="group-routine-fields group-routine-description" disabled={locked}>
        <legend className="group-sr-only">例程说明</legend>
        <label>例程名称<input value={spec.name} required maxLength={120} placeholder="例如：每周项目简报" onChange={(event) => update({ name: event.target.value })} /></label>
        {fieldDescriptions.map(([id, label, maxLength, placeholder]) => <label key={id}>{label}<textarea rows={2} value={spec[id]} required={id === 'goal'} maxLength={maxLength} placeholder={placeholder} onChange={(event) => update({ [id]: event.target.value })} /></label>)}
      </fieldset>
      <fieldset className="group-routine-fields group-routine-settings" disabled={locked}>
        <legend>触发与交付</legend>
        <label>触发方式<select value={trigger.kind} onChange={(event) => updateTrigger({ kind: event.target.value })}>
          {Object.entries(ROUTINE_TRIGGERS).map(([kind, label]) => <option key={kind} value={kind}>{label}</option>)}
        </select></label>
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
        {trigger.kind === 'board_card' && <>
          <label>卡片标签<input value={trigger.tag} required maxLength={100} placeholder="例如：周报" spellCheck={false} onChange={(event) => updateTrigger({ tag: event.target.value })} /></label>
          <p className="group-routine-hint">只在本群看板手动新建卡片时检查标签。标签须完全相同，之后添加标签不会触发。{spec.allow_native && ' 此例程只接受本机新建的卡片。'}</p>
        </>}
        <fieldset className="group-routine-formats">
          <legend>同时生成文件 <span>未选时按目标生成</span></legend>
          {DOCUMENT_FORMATS.map(({ id, label }) => <label key={id}>
            <input type="checkbox" checked={spec.document_formats.includes(id)} onChange={(event) => update({
              document_formats: DOCUMENT_FORMATS.filter((item) => item.id === id ? event.target.checked : spec.document_formats.includes(item.id)).map((item) => item.id),
            })} />{id === 'html' ? 'HTML' : label}
          </label>)}
        </fieldset>
        {!REMOTE_CLIENT && <div className="group-routine-native">
          <label><input type="checkbox" checked={spec.allow_native} onChange={(event) => update({ allow_native: event.target.checked })} />允许调用本机 Agent</label>
          {spec.allow_native && <p className="group-routine-hint">使用本群已连接的执行器。代码修改仍需你的审阅与合入审批。</p>}
        </div>}
      </fieldset>
    </div>
    <footer className="group-routine-editor-footer">
      {error && <p role="alert" className="group-routine-error">{error}</p>}
      {conflict && <details className="group-routine-conflict"><summary>查看服务端最新配置</summary>
        <dl><dt>例程名称</dt><dd>{conflict.name}</dd><dt>预期成果</dt><dd>{conflict.goal}</dd><dt>执行步骤</dt><dd>{conflict.process}</dd><dt>验收标准</dt><dd>{conflict.checks}</dd><dt>触发方式</dt><dd>{routineTriggerLabel(conflict)}</dd><dt>文件</dt><dd>{(conflict.document_formats || []).join('、') || '无'}</dd><dt>本机 Agent</dt><dd>{conflict.allow_native ? '允许' : '关闭'}</dd></dl>
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

function RoutineCard({ routine, busy, pendingRun, opened, onRun, onEdit, onToggle, onOpenJob }) {
  const { trigger } = routine;
  const scheduled = routineTimed(trigger.kind);
  const timezone = scheduled ? trigger.timezone : undefined;
  const next = routineDateLabel(routine.next_run_at, timezone);
  const nativeOnRemote = REMOTE_CLIENT && routine.allow_native;
  const runs = routine.recent_runs || [];
  const latest = runs[0];
  const canTrigger = trigger.kind !== 'manual' || routine.enabled;
  const disabled = Boolean(busy || nativeOnRemote);
  return <article className="group-routine-card">
    <header><h3>{routine.name}</h3>
      {canTrigger && <span className="group-routine-chip" data-enabled={routine.enabled}>{routine.enabled ? '已启用' : '未启用'}</span>}
    </header>
    <p className="group-routine-goal">{routine.goal}</p>
    <div className="group-routine-trigger"><Icon name={trigger.kind === 'board_card' ? 'dashboard' : 'clock'} size={14} /><span>{routineTriggerLabel(routine)}</span></div>
    {routine.enabled && scheduled && next && <p className="group-routine-hint">下次运行 <time dateTime={routine.next_run_at}>{next}</time></p>}
    {routine.team_changed && <p className="group-routine-hint">群成员已更新。请检查并保存此例程，再运行或启用。</p>}
    {nativeOnRemote ? <p className="group-routine-hint">使用本机 Agent 的例程请在桌面端管理。</p> : <div className="group-routine-actions">
      <button type="button" className="subtle-button group-routine-run" disabled={disabled || (routine.team_changed && !pendingRun)} onClick={onRun}>{pendingRun ? '重试运行请求' : '运行一次'}</button>
      {canTrigger && <button type="button" className="subtle-button" disabled={disabled || (routine.team_changed && !routine.enabled)} onClick={onToggle}>{routine.enabled ? '暂停' : '启用'}</button>}
      <button type="button" className="subtle-button" disabled={Boolean(disabled || pendingRun)} onClick={onEdit}>编辑</button>
    </div>}
    {runs.length > 0 && <details className="group-routine-history" open={opened || undefined}>
      <summary><span>最近运行</span><strong>{ROUTINE_RUN_STATES[latest.state] || '状态待同步'}</strong></summary>
      <ol>{runs.map((run) => <li key={run.id}>
        <div className="group-routine-run-heading">
          <strong>{ROUTINE_RUN_STATES[run.state] || '状态待同步'}</strong>
          <span>{triggerRunLabels[run.trigger_kind]} · <time dateTime={run.created_at}>{routineDateLabel(run.created_at, timezone)}</time></span>
        </div>
        {run.reason && <p className="group-routine-hint">{run.reason}</p>}
        {run.job_id && <button type="button" className="subtle-button" disabled={Boolean(busy)} onClick={() => onOpenJob(run.job_id)}>查看运行记录</button>}
      </li>)}</ol>
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
  const editing = Boolean(draft);
  useLayoutEffect(() => {
    const next = editing ? frame.current?.querySelector('[data-routine-heading]') : title.current;
    next?.focus({ preventScroll: true });
  }, [editing]);
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
      const routines = response.routines;
      if (!Array.isArray(routines) || !routines.every(belongsToRoom)) throw new Error('群聊服务未返回本群的例程列表。');
      setRoutines(routines);
      setReadError('');
      const savedDraft = readRoutineStorage(draftKey);
      if (savedDraft?.requestId && routines.some((routine) => routine.client_request_id === savedDraft.requestId)) {
        writeRoutineStorage(draftKey, null);
        setDraft((current) => current?.requestId === savedDraft.requestId ? null : current);
        setError('');
        setNotice('已确认例程保存成功。');
      }
      for (const routine of routines) {
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
      return routines;
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
    if (mutating.current) return;
    mutating.current = true;
    setBusy(action);
    clearMessage();
    try {
      await operation();
    } catch (failure) {
      if (alive.current) setError(failure.message);
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

  const run = (routine) => void mutate('run', async () => {
    const key = pendingKey(routine.id);
    const requestId = runReceipts[routine.id] || readRoutineStorage(key) || newGroupRequestId();
    setRunReceipts((current) => ({ ...current, [routine.id]: requestId }));
    writeRoutineStorage(key, requestId);
    let receipt;
    try {
      receipt = await groupApi.runRoutine(roomId, projectId, routine.id, { client_request_id: requestId });
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
    saveDraft(routineDraft(routine));
  };
  return <WorkspaceDialog onDismiss={onDismiss} focusRef={title} aria-labelledby="group-routines-title" className="group-options-dialog group-routines-dialog">
    <div className="group-routines-frame" ref={frame}>
      <header className="group-routines-header">
        <span className="group-routines-icon"><Icon name="clock" size={21} /></span>
        <div><h2 id="group-routines-title" ref={title} tabIndex={-1}>群聊自动化</h2><p title={room.title}>{room.title}</p></div>
        <button type="button" className="icon-button" aria-label="关闭群聊自动化" onClick={onDismiss}><Icon name="close" size={18} /></button>
      </header>
      {draft ? <RoutineEditor draft={draft} setDraft={saveDraft} composerDraft={composerDraft} busy={busy} error={error} conflict={conflict} onSave={save} onRemove={remove}
        onBack={() => { clearMessage(); saveDraft(null); }} /> : <div className="group-routines-body">
        {notice && <p className="group-routine-feedback" role="status">{notice}</p>}
        {error && <p className="group-routine-error" role="alert">{error}</p>}
        {readError && <div className="group-routine-error group-routine-read-error" role="alert">
          <span>{readError}</span><button type="button" className="subtle-button" onClick={() => refresh(true)}>刷新例程</button>
        </div>}
        {!routines && !readError && <p className="group-routine-loading" role="status"><span className="small-spinner" />正在读取例程…</p>}
        {routines?.length === 0 && <section className="group-routine-empty">
          <h3>把重复的工作交给本群</h3>
          <p>保存成果要求、执行步骤和验收标准。先试跑一次，再按时间或看板新卡片触发。</p>
          <button type="button" className="primary-button" onClick={() => beginEdit()}><Icon name="plus" size={15} />新建例程</button>
        </section>}
        {routines?.length > 0 && <>
          <div className="group-routines-list-heading"><h3>本群例程</h3><button type="button" className="primary-button" disabled={Boolean(busy)} onClick={() => beginEdit()}><Icon name="plus" size={15} />新建例程</button></div>
          <div className="group-routines-list">{routines.map((routine) => {
            const pendingRun = runReceipts[routine.id] || readRoutineStorage(pendingKey(routine.id));
            return <RoutineCard key={routine.id} routine={routine} busy={busy} opened={opened === routine.id} pendingRun={pendingRun}
              onRun={() => run(routine)} onToggle={() => toggle(routine)} onEdit={() => beginEdit(routine)} onOpenJob={openJob} />;
          })}</div>
        </>}
      </div>}
    </div>
  </WorkspaceDialog>;
}
