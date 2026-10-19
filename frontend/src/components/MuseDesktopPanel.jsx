import { useCallback, useEffect, useRef, useState } from 'react';
import { museApi } from '../muse-api';
import { REMOTE_CLIENT } from '../api-base';
import {
  MUSE_STATUS,
  normalizeMuseSource,
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

export function MuseTaskCard({ task: rawTask, projectId, roomId, busy, onCancel, onObserve, onRefreshMedia, onUseReply }) {
  const task = normalizeMuseSource(rawTask);
  const threadUrl = safeMuseThreadUrl(task.thread_url);
  const usable = canUseMuseReply(task, projectId, roomId);
  const videoUrl = resolveMuseVideoUrl(task);
  return <section className="muse-task-card" aria-label="Muse 任务与实际回复">
    <header><span className={`muse-task-status muse-task-${task.status}`}>{task.monitoring && <span className="activity-dot" />}{MUSE_STATUS[task.status] || task.status}</span><time>{dateLabel(task.created_at)}</time>{threadUrl && <a href={threadUrl} target="_blank" rel="noopener noreferrer">打开 Muse 会话 ↗</a>}</header>
    <details className="muse-task-prompt"><summary>{task.prompt?.slice(0, 100) || '交给 Muse 的任务'}</summary><Markdown>{task.prompt || ''}</Markdown><CopyTextButton text={task.prompt || ''} label="复制 Muse 任务" /></details>
    {task.skill_application && <details className="muse-task-prompt"><summary>本次 Skills · {task.skill_application.skills.map((skill) => skill.name).join('、')} · {task.sent === true ? '已随任务发送' : '已准备，尚未确认送达'}</summary><p>{task.skill_application.mode === 'adapted_guidance' ? `由 ${task.skill_application.prepared_by} 整理为 Muse 创作指引` : '按角色传递技能指令'}。使用 Muse 自身能力；未安装原生插件。</p>{task.skill_application.skills.map((skill) => <p key={skill.id}>{skill.name} · 版本 {(skill.revision || skill.sha256).slice(0, 12)}</p>)}<Markdown>{task.skill_application.guidance || ''}</Markdown><CopyTextButton text={task.skill_application.guidance || ''} label="复制本次 Skill 指引" /></details>}
    {task.error && <p className="output-error" role="status">{task.error}</p>}
    {videoUrl && (
      <div className="muse-video-card" aria-label="Muse 已回传视频">
        <div className="muse-video-header">
          <strong>Muse 返回的视频链接</strong>
          <a className="subtle-button" href={videoUrl} download>下载原始视频</a>
        </div>
        <video className="muse-inline-video" src={videoUrl} controls playsInline preload="metadata" loop />
      </div>
    )}
    {task.media_delivery?.errors?.length > 0 && <p className="output-error" role="status">{task.media_delivery.errors.join('；')}</p>}
    {task.media_delivery && <p>实际回收：{task.media_delivery.images} 张图片 · {task.media_delivery.videos} 个视频</p>}
    {task.response ? <div className="muse-observed-response"><div className="muse-response-heading"><strong>{usable ? 'Muse 实际回复' : '已读取的部分内容'}</strong><CopyTextButton text={task.response} label="复制 Muse 回复" /></div><Markdown>{task.response}</Markdown></div> : <p className="muse-response-empty">{task.monitoring ? '正在等待 Muse 原生窗口中的回复。' : '尚未读取到实际回复。'}</p>}
    <div className="muse-task-actions">{task.monitoring ? <button type="button" className="subtle-button" disabled={busy} onClick={() => onCancel(task)}>停止本地观察</button> : task.sent === true && task.status !== 'complete' && threadUrl ? <button type="button" className="subtle-button" disabled={busy} onClick={() => onObserve(task)}>继续观察原会话</button> : null}{task.status === 'complete' && onRefreshMedia && <button type="button" className="subtle-button" disabled={busy} onClick={() => onRefreshMedia(task)}>重新回收原会话附件</button>}<a href={museApi.exportUrl(task.id, projectId)} download>导出 Markdown</a><a href={museApi.exportUrl(task.id, projectId, 'json')} download>JSON</a>{usable && <button type="button" className="primary-button" disabled={busy} onClick={() => onUseReply(task)}><Icon name="group" size={14} />使用 Muse 回复继续讨论</button>}</div>
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
  const readiness = museReadiness(probe);
  const canDispatch = Boolean(prompt.trim() && !busy && readiness.ready && !REMOTE_CLIENT);
  const checkConnection = useCallback(async () => {
    setProbing(true);
    try { const result = await museApi.status(); if (alive.current) setProbe(result); }
    catch (failure) { if (alive.current) setProbe({ ok: false, installed: null, permission: null, error: failure.message }); }
    finally { if (alive.current) setProbing(false); }
  }, []);
  const refresh = useCallback(async () => {
    try {
      const raw = await museApi.list(projectId, roomId);
      const result = scopedMuseTasks(raw.map(normalizeMuseSource), projectId, roomId);
      if (!alive.current) return;
      setTasks((previous) => mergeMuseTasks(previous, result, projectId, roomId));
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
      const created = await museApi.create({ project_id: projectId || null, room_id: roomId || null, prompt: sentPrompt, timeout_seconds: 600 });
      if (!alive.current) return;
      const hydrated = [normalizeMuseSource(created)];
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
      const hydrated = normalizeMuseSource(updated);
      if (alive.current) setTasks((previous) => scopedMuseTasks([hydrated, ...previous.filter((item) => item.id !== updated.id)], projectId, roomId));
    } catch (failure) { if (alive.current) setActionError(failure.message); }
    finally { if (alive.current) setBusy(false); }
  };
  const openMuse = async () => { try { const result = await window.modelDesk.openMuse(); if (result === false || result?.error) throw new Error(result?.error || '无法打开 Muse，请手动打开应用。'); await checkConnection(); } catch (failure) { if (alive.current) setActionError(failure.message || '无法打开 Muse，请手动打开应用。'); } };
  const useReply = (selected) => { if (canUseMuseReply(selected, projectId, roomId)) { onUseReply(selected); setOpen(false); } };
  return <details className="muse-desktop-panel" open={open} onToggle={(event) => { const next = event.currentTarget.open; setOpen(next); if (next) { checkConnection(); refresh(); } }}>
    <summary><span className="muse-native-mark">M</span><strong>Muse <small>原生应用桥接 · 读取真实回复与成果链接</small></strong><span className={readiness.ready ? "muse-ready" : "muse-not-ready"}>{hasMonitoring ? '正在处理本群任务' : readiness.label}</span><Icon name="chevron" size={13} /></summary>
    <div className="muse-panel-body">
      <div className="muse-connection"><p>{readiness.detail}</p>{typeof window !== "undefined" && typeof window.modelDesk?.openMuse === "function" && <button type="button" className="subtle-button" onClick={openMuse}>打开 Muse</button>}<button type="button" className="subtle-button" disabled={probing} onClick={checkConnection}><Icon name="refresh" size={13} />{probing ? '正在检查' : '检查连接'}</button></div>
      <form className="muse-task-composer" onSubmit={send}><label><span>交给 Muse 的任务</span><textarea aria-label="Muse 任务要求" value={prompt} maxLength={12000} rows={2} onChange={(event) => setPrompt(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); send(event); } }} placeholder="例如：研究这个方案并说明实际交付结果。也可将 Muse 添加为群成员后明确派发任务。" /></label><div><p>任务通过本机 Muse 窗口发送。时间线保留实际读取的回复；仅展示真实返回的视频链接。</p><button type="submit" className="primary-button" disabled={!canDispatch}><Icon name="send" size={14} />{busy ? '正在处理' : '派发 Muse'}</button></div></form>
      {REMOTE_CLIENT && <p className="mission-form-note">Muse 原生窗口操作需要在 Mac 端派发。</p>}
      {(readError || actionError) && <p className="output-error" role="alert">{actionError || readError}{readError && <button type="button" className="subtle-button" onClick={refresh}>重新读取</button>}</p>}
      {tasks.length > 0 && <label className="muse-task-picker"><span>本群 Muse 任务</span><select aria-label="查看 Muse 任务" value={taskId || ''} onChange={(event) => setTaskId(event.target.value)}>{taskId && !task && <option value={taskId}>所选任务暂不可用</option>}{tasks.map((item) => <option key={item.id} value={item.id}>{MUSE_STATUS[item.status] || item.status} · {item.prompt?.slice(0, 60)} · {dateLabel(item.created_at)}</option>)}</select></label>}
      {task && <MuseTaskCard task={task} projectId={projectId} roomId={roomId} busy={busy} onCancel={(selected) => mutateTask(selected, museApi.cancel)} onObserve={(selected) => mutateTask(selected, museApi.observe)} onRefreshMedia={(selected) => mutateTask(selected, museApi.refreshMedia)} onUseReply={useReply} />}
    </div>
  </details>;
}
