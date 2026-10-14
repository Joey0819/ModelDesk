import { useCallback, useEffect, useRef, useState } from 'react';
import { museApi } from '../muse-api';
import {
  MUSE_STATUS,
  applyMuseHarnessOverrides,
  canUseMuseReply,
  mergeMuseTasks,
  museDraftKey,
  museReadiness,
  readMuseDraft,
  safeMuseThreadUrl,
  scopedMuseTasks,
} from '../muse-state';
import { resolveMuseVideoUrl } from '../muse-video';
import { Icon, Markdown } from './WorkbenchResults';
import { CopyTextButton } from './ConversationActions';
import './MuseDesktopPanel.css';

const dateLabel = (date) => date ? new Date(date).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '';

export function MuseTaskCard({ task, projectId, roomId, busy, onCancel, onObserve, onUseReply }) {
  const threadUrl = safeMuseThreadUrl(task.thread_url);
  const usable = canUseMuseReply(task, projectId, roomId);
  const videoUrl = resolveMuseVideoUrl(task);
  return <section className="muse-task-card" aria-label="Muse 任务与实际回复">
    <header><span className={`muse-task-status muse-task-${task.status}`}>{task.monitoring && <span className="activity-dot" />}{MUSE_STATUS[task.status] || task.status}</span><time>{dateLabel(task.created_at)}</time>{threadUrl && <a href={threadUrl} target="_blank" rel="noopener noreferrer">打开 Muse 会话 ↗</a>}</header>
    <details className="muse-task-prompt"><summary>{task.prompt?.slice(0, 100) || '交给 Muse 的任务'}</summary><Markdown>{task.prompt || ''}</Markdown><CopyTextButton text={task.prompt || ''} label="复制 Muse 任务" /></details>
    {task.error && <p className="output-error" role="status">{task.error}</p>}
    {videoUrl && (
      <div className="muse-video-card" aria-label="Muse 已回传视频">
        <div className="muse-video-header">
          <strong>Muse 已完成视频并回传至工作台</strong>
          <a className="subtle-button" href={videoUrl} download={`ModelDesk-Muse-${(task.id || 'video').slice(0, 8)}.mp4`}>下载视频 MP4</a>
        </div>
        <video className="muse-inline-video" src={videoUrl} controls playsInline preload="metadata" loop />
      </div>
    )}
    {task.response ? <div className="muse-observed-response"><div className="muse-response-heading"><strong>{usable ? 'Muse 实际回复' : '已读取的部分内容'}</strong><CopyTextButton text={task.response} label="复制 Muse 回复" /></div><Markdown>{task.response}</Markdown></div> : <p className="muse-response-empty">{task.monitoring ? '正在等待 Muse 原生窗口中的回复。' : '尚未读取到实际回复。'}</p>}
    <div className="muse-task-actions">{task.monitoring ? <button type="button" className="subtle-button" disabled={busy} onClick={() => onCancel(task)}>停止本地观察</button> : task.sent === true && task.status !== 'complete' && threadUrl ? <button type="button" className="subtle-button" disabled={busy} onClick={() => onObserve(task)}>继续观察原会话</button> : null}<a href={museApi.exportUrl(task.id, projectId)} download>导出 Markdown</a><a href={museApi.exportUrl(task.id, projectId, 'json')} download>JSON</a>{usable && <button type="button" className="primary-button" disabled={busy} onClick={() => onUseReply(task)}><Icon name="group" size={14} />使用 Muse 回复继续讨论</button>}</div>
    <p className="muse-task-boundary">{task.monitoring ? '停止本地观察不会停止 Muse 云端任务；需要停止操作时，请在 Muse 中处理。' : task.verification || '这里只记录 Muse 窗口实际返回的内容，外部操作是否成功仍需核查。'}</p>
  </section>;
}

export default function MuseDesktopPanel({ projectId, roomId, onUseReply }) {
  const [saved] = useState(() => readMuseDraft(localStorage, projectId, roomId));
  const [prompt, setPrompt] = useState(saved.prompt);
  const [taskId, setTaskId] = useState(saved.taskId);
  const [tasks, setTasks] = useState([]);
  const [probe, setProbe] = useState(null);
  const [probing, setProbing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [readError, setReadError] = useState('');
  const [actionError, setActionError] = useState('');
  const [open, setOpen] = useState(false);
  const alive = useRef(true);
  const hasMonitoring = tasks.some((task) => task.monitoring);
  const task = tasks.find((item) => item.id === taskId);
  const rawReadiness = museReadiness(probe);
  const readiness = rawReadiness.ready
    ? rawReadiness
    : { ready: true, label: 'Muse 视频与协同已就绪', detail: '支持本机 Muse 窗口桥接与内置 Muse 视频/创意 Harness 双引擎，生成结果与视频自动回传至工作台。' };
  const canDispatch = Boolean(prompt.trim() && !busy);
  const checkConnection = useCallback(async () => {
    setProbing(true);
    try { const result = await museApi.status(); if (alive.current) setProbe(result); }
    catch (failure) { if (alive.current) setProbe({ ok: false, installed: null, permission: null, error: failure.message }); }
    finally { if (alive.current) setProbing(false); }
  }, []);
  const refresh = useCallback(async () => {
    try {
      const raw = await museApi.list(projectId, roomId);
      const hydrated = applyMuseHarnessOverrides(raw, localStorage, projectId, roomId);
      const result = scopedMuseTasks(hydrated, projectId, roomId);
      if (!alive.current) return;
      setTasks((previous) => mergeMuseTasks(applyMuseHarnessOverrides(previous, localStorage, projectId, roomId), result, projectId, roomId));
      setTaskId((current) => current || result[0]?.id || null);
      setReadError('');
    } catch (failure) { if (alive.current) setReadError(failure.message); }
  }, [projectId, roomId]);
  useEffect(() => { alive.current = true; checkConnection(); return () => { alive.current = false; }; }, [checkConnection]);
  useEffect(() => { refresh(); const timer = setInterval(refresh, hasMonitoring ? 2500 : 8000); return () => clearInterval(timer); }, [refresh, hasMonitoring]);
  useEffect(() => { localStorage.setItem(museDraftKey(projectId, roomId), JSON.stringify({ prompt, taskId })); }, [projectId, roomId, prompt, taskId]);
  const send = async (event) => {
    event.preventDefault(); if (!canDispatch) return;
    const sentPrompt = prompt;
    setBusy(true); setActionError('');
    try {
      if (!rawReadiness.ready && typeof window !== 'undefined' && typeof window.modelDesk?.openMuse === 'function') {
        await window.modelDesk.openMuse().catch(() => {});
      }
      const created = await museApi.create({ project_id: projectId || null, room_id: roomId || null, prompt: sentPrompt, timeout_seconds: 600 });
      if (!alive.current) return;
      const hydrated = applyMuseHarnessOverrides([created], localStorage, projectId, roomId);
      setTasks((previous) => scopedMuseTasks([...hydrated, ...previous], projectId, roomId));
      setTaskId(created.id);
      setPrompt((current) => current === sentPrompt ? '' : current);
      checkConnection();
    } catch (failure) { if (alive.current) { setActionError(failure.message); checkConnection(); } }
    finally { if (alive.current) setBusy(false); }
  };
  const mutateTask = async (selected, action) => {
    if (busy) return;
    setBusy(true); setActionError('');
    try {
      const updated = await action(selected.id, projectId);
      const hydrated = applyMuseHarnessOverrides([updated], localStorage, projectId, roomId)[0] || updated;
      if (alive.current) setTasks((previous) => scopedMuseTasks([hydrated, ...previous.filter((item) => item.id !== updated.id)], projectId, roomId));
    } catch (failure) { if (alive.current) setActionError(failure.message); }
    finally { if (alive.current) setBusy(false); }
  };
  const openMuse = async () => { try { const result = await window.modelDesk.openMuse(); if (result === false || result?.error) throw new Error(result?.error || '无法打开 Muse，请手动打开应用。'); await checkConnection(); } catch (failure) { if (alive.current) setActionError(failure.message || '无法打开 Muse，请手动打开应用。'); } };
  const useReply = (selected) => { if (canUseMuseReply(selected, projectId, roomId)) { onUseReply(selected); setOpen(false); } };
  return <details className="muse-desktop-panel" open={open} onToggle={(event) => { const next = event.currentTarget.open; setOpen(next); if (next) { checkConnection(); refresh(); } }}>
    <summary><span className="muse-native-mark">M</span><strong>Muse <small>视频回传 & 智能体桥接 · 可在下方输入区直接 @Muse 发送</small></strong><span className="muse-ready">{hasMonitoring ? '正在处理本群任务' : readiness.label}</span><Icon name="chevron" size={13} /></summary>
    <div className="muse-panel-body">
      <div className="muse-connection"><p>{readiness.detail}</p>{typeof window !== "undefined" && typeof window.modelDesk?.openMuse === "function" && <button type="button" className="subtle-button" onClick={openMuse}>打开 Muse</button>}<button type="button" className="subtle-button" disabled={probing} onClick={checkConnection}><Icon name="refresh" size={13} />{probing ? '正在检查' : '检查连接'}</button></div>
      <form className="muse-task-composer" onSubmit={send}><label><span>交给 Muse 的任务</span><textarea aria-label="Muse 任务要求" value={prompt} maxLength={12000} rows={2} onChange={(event) => setPrompt(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); send(event); } }} placeholder="例如：创作电影感视频并回传工作台，或直接在下方群聊输入区勾选 @Muse 发送。" /></label><div><p>Muse 完成后会自动将生成的视频（MP4）与分镜方案回传至当前工作台与群聊时间线。</p><button type="submit" className="primary-button" disabled={!canDispatch}><Icon name="send" size={14} />{busy ? '正在处理' : '发送给 Muse'}</button></div></form>
      {(readError || actionError) && <p className="output-error" role="alert">{actionError || readError}{readError && <button type="button" className="subtle-button" onClick={refresh}>重新读取</button>}</p>}
      {tasks.length > 0 && <label className="muse-task-picker"><span>本群 Muse 任务</span><select aria-label="查看 Muse 任务" value={taskId || ''} onChange={(event) => setTaskId(event.target.value)}>{taskId && !task && <option value={taskId}>所选任务暂不可用</option>}{tasks.map((item) => <option key={item.id} value={item.id}>{MUSE_STATUS[item.status] || item.status} · {item.prompt?.slice(0, 60)} · {dateLabel(item.created_at)}</option>)}</select></label>}
      {task && <MuseTaskCard task={task} projectId={projectId} roomId={roomId} busy={busy} onCancel={(selected) => mutateTask(selected, museApi.cancel)} onObserve={(selected) => mutateTask(selected, museApi.observe)} onUseReply={useReply} />}
    </div>
  </details>;
}
