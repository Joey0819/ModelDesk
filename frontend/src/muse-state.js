export const MUSE_STATUS = { queued: '排队等待', dispatching: '正在发送', running: '正在观察回复', waiting_user: '需要你处理', complete: '已读取回复', error: '未完成', uncertain: '送达或回复未确认', interrupted: '观察已中断', stopped_monitoring: '已停止本地观察' };
export const museDraftKey = (projectId, roomId) => `modeldesk.muse.v1:${projectId || 'unassigned'}:${roomId || 'unassigned'}`;
export function readMuseDraft(storage, projectId, roomId) {
  try {
    const value = JSON.parse(storage.getItem(museDraftKey(projectId, roomId)) || '{}');
    return { prompt: typeof value.prompt === 'string' ? value.prompt : '', taskId: typeof value.taskId === 'string' ? value.taskId : null };
  } catch { return { prompt: '', taskId: null }; }
}
export function scopedMuseTasks(items, projectId, roomId) {
  return (Array.isArray(items) ? items : []).filter((task) => (task.project_id || null) === (projectId || null) && (task.room_id || null) === (roomId || null))
    .sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')));
}
export function museReadiness(probe) {
  if (!probe) return { ready: false, label: '正在检查连接', detail: '读取 Muse 原生窗口状态。' };
  if (probe.installed == null) return { ready: false, label: '连接尚未确认', detail: probe.error || '请检查本地桥接服务后重新连接。' };
  if (!probe.installed) return { ready: false, label: '未找到 Muse', detail: probe.error || '请先安装并打开 Muse Mac。' };
  if (probe.permission !== true) return { ready: false, label: '需要辅助功能授权', detail: probe.error || '在 macOS 系统设置 → 隐私与安全性 → 辅助功能中允许 ModelDesk 桥接，再检查连接。' };
  if (!probe.ok) return { ready: false, label: '暂不可用', detail: probe.error || '暂时无法读取 Muse 原生窗口，请打开 Muse 后重试。' };
  if (!probe.composer_ready) return { ready: false, label: '等待 Muse 输入窗口', detail: '辅助功能已授权。请打开 Muse 的可用输入窗口，再检查连接。' };
  return { ready: true, label: '原生输入窗口已就绪', detail: '通过已登录的 Muse Mac 窗口发送并读取回复。' };
}
export function safeMuseThreadUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && url.hostname === 'muse.ai' && (!url.port || url.port === '443') && !url.username && !url.password && !url.search && !url.hash && /^\/thread\/[A-Za-z0-9_-]{1,200}\/?$/.test(url.pathname) ? url.href : null;
  } catch { return null; }
}
export function canUseMuseReply(task, projectId, roomId) {
  return Boolean(task && task.status === 'complete' && task.sent === true && task.completion_evidence === 'framed_assistant_reply' && task.response?.trim() && (task.project_id || null) === (projectId || null) && (task.room_id || null) === (roomId || null));
}

export function mergeMuseTasks(previous, incoming, projectId, roomId) {
  const tasks = new Map(scopedMuseTasks(previous, projectId, roomId).map((task) => [task.id, task]));
  for (const task of scopedMuseTasks(incoming, projectId, roomId)) {
    const prior = tasks.get(task.id);
    if (!prior || String(task.updated_at || '') >= String(prior.updated_at || '')) tasks.set(task.id, task);
  }
  return scopedMuseTasks([...tasks.values()], projectId, roomId);
}
