import { useCallback, useEffect, useRef, useState } from 'react';
import { autonomyApi } from '../autonomy-api';
import { AUTONOMY_STATES, autonomyActive, autonomyCounts, autonomyUnread, scopedAutonomyTasks } from '../autonomy-state';
import { Icon } from './WorkbenchResults';
import './AutonomyTaskCenter.css';

const timeLabel = (value) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
};

export function BackgroundDelegateSetting({ checked, onChange, disabled = false }) {
  return <div className="background-delegate-setting">
    <label><input type="checkbox" checked={checked} disabled={disabled} onChange={(event) => onChange(event.target.checked)} /><strong>后台托管</strong></label>
    <p>离开当前页面后继续执行；遇到可恢复问题时，最多 2 次安全续跑。外部任务状态不确定时等待你处理，不重复派发。Mac 需要保持唤醒并联网，代码合入仍需审批。</p>
  </div>;
}

export function AutonomyTaskList({ tasks = [], loading = false, error = '', actionError = '', busyId, onRefresh, onOpenTask, onRead, onPause }) {
  return <div className="autonomy-task-list">
    {error && <div className="autonomy-connection-error" role="alert"><p>{error}</p>{tasks.length > 0 && <small>以下为上次同步的状态，尚未确认最新进度。</small>}<button type="button" className="subtle-button" onClick={onRefresh}>重新连接</button></div>}
    {actionError && <p className="output-error" role="alert">{actionError}</p>}
    {loading && !tasks.length ? <p className="autonomy-empty" role="status"><span className="small-spinner" />正在读取服务端保存的任务…</p>
      : !tasks.length && !error ? <div className="autonomy-empty"><Icon name="tasks" size={25} /><strong>这个项目还没有后台任务</strong><p>在项目群的任务设置中启用“后台托管”，提交任务后可在此查看进度、成果或待处理事项。</p></div> : null}
    {tasks.map((task) => <article key={task.id} className={`autonomy-task autonomy-task-${task.state}`}>
      <header><strong>{task.title || '未命名任务'}</strong><span className="autonomy-state">{AUTONOMY_STATES[task.state]}</span>{autonomyUnread(task) && <span className="autonomy-unread">未读</span>}</header>
      {task.reason && <p>{task.reason}</p>}
      <div className="autonomy-task-meta"><time dateTime={task.updated_at}>{timeLabel(task.updated_at)}</time>{Number.isFinite(task.attempts) && Number.isFinite(task.max_attempts) && <span>安全续跑 {task.attempts}/{task.max_attempts}</span>}{task.state === 'retry_wait' && task.next_action_at && <span>下次检查：{timeLabel(task.next_action_at)}</span>}</div>
      {task.state === 'paused' && <p className="autonomy-task-note">已停止后续自动续跑；已启动的执行不会因此取消。</p>}
      <div className="autonomy-task-actions"><button type="button" className="primary-button" disabled={busyId === task.id} onClick={() => onOpenTask(task)}>{busyId === task.id ? '正在处理…' : task.state === 'complete' ? '查看成果' : '查看任务'}</button>{autonomyUnread(task) && <button type="button" className="subtle-button" disabled={busyId === task.id} onClick={() => onRead(task)}>标为已读</button>}{autonomyActive(task) && <button type="button" className="subtle-button" disabled={busyId === task.id} onClick={() => onPause(task)} title="仅停止后续自动续跑；停止当前执行请打开任务">暂停托管</button>}</div>
    </article>)}
  </div>;
}

export default function AutonomyTaskCenter({ projectId, projectName, onOpenTask, onOpenFeishu }) {
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [actionError, setActionError] = useState('');
  const [busyId, setBusyId] = useState(null);
  const dialog = useRef(null);
  const alive = useRef(false);
  const inFlight = useRef(false);
  const counts = autonomyCounts(tasks);
  const refresh = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      const result = await autonomyApi.list(projectId);
      if (alive.current) { setTasks(scopedAutonomyTasks(result, projectId)); setError(''); }
    } catch (failure) { if (alive.current) setError(failure.message); }
    finally { inFlight.current = false; if (alive.current) setLoading(false); }
  }, [projectId]);
  useEffect(() => {
    alive.current = true;
    refresh();
    const timer = setInterval(refresh, 5000);
    const changed = () => refresh();
    const openError = (event) => { setActionError(typeof event.detail === 'string' ? event.detail : '暂时无法打开通知对应的任务。'); dialog.current?.showModal(); };
    globalThis.addEventListener?.('modeldesk-autonomy-changed', changed);
    globalThis.addEventListener?.('modeldesk-autonomy-open-error', openError);
    return () => { alive.current = false; clearInterval(timer); globalThis.removeEventListener?.('modeldesk-autonomy-changed', changed); globalThis.removeEventListener?.('modeldesk-autonomy-open-error', openError); };
  }, [refresh]);
  const act = async (task, operation) => {
    if (busyId) return;
    setBusyId(task.id); setActionError('');
    try {
      if (operation === 'open') { await onOpenTask(task); if (alive.current) dialog.current?.close(); }
      else await autonomyApi[operation](task.id, projectId);
      await refresh();
    } catch (failure) { if (alive.current) setActionError(failure.message); }
    finally { if (alive.current) setBusyId(null); }
  };
  return <>
    <button type="button" className="autonomy-center-trigger" onClick={() => { dialog.current?.showModal(); refresh(); }} aria-haspopup="dialog" title={error ? '后台任务暂未同步，点击重新连接' : `运行中 ${counts.active} · 需要处理 ${counts.attention} · 未读 ${counts.unread}`}><Icon name="tasks" size={15} /><span>后台任务</span>{counts.unread > 0 && <b aria-label={`${counts.unread} 条未读任务通知`}>{counts.unread}</b>}{counts.active > 0 && <span className="autonomy-trigger-running">{counts.active} 运行中</span>}{error && <span aria-label="后台任务连接失败">!</span>}</button>
    <dialog className="autonomy-center-dialog" ref={dialog} aria-label="后台任务中心">
      <header><div><strong>后台任务</strong><p>{projectName || '未关联项目'} · {counts.active} 运行中 · {counts.attention} 待处理</p></div><button type="button" className="icon-button" aria-label="关闭后台任务" onClick={() => dialog.current?.close()}><Icon name="close" size={18} /></button></header>
      <p className="autonomy-center-explanation">状态与未读通知保存在本机服务，刷新页面后可继续查看。关闭页面不会取消任务；Mac 休眠、关机或服务停止时无法继续执行。系统通知需要 ModelDesk 桌面应用保持运行。</p>
      {onOpenFeishu && <button type="button" className="subtle-button autonomy-feishu-link" onClick={() => { dialog.current?.close(); onOpenFeishu(); }}>配置飞书：从手机发指令并接收成果</button>}
      <AutonomyTaskList tasks={tasks} loading={loading} error={error} actionError={actionError} busyId={busyId} onRefresh={refresh} onOpenTask={(task) => act(task, 'open')} onRead={(task) => act(task, 'read')} onPause={(task) => act(task, 'pause')} />
    </dialog>
  </>;
}
