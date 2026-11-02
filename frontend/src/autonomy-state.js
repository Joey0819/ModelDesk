export const AUTONOMY_STATES = {
  running: '正在执行', retry_wait: '等待安全续跑', complete: '已交付', needs_attention: '需要你处理', cancelled: '已停止', paused: '托管已暂停',
};
export const autonomyUnread = (task) => Boolean(task?.notification_id && task.read === false);
export const autonomyActive = (task) => ['running', 'retry_wait'].includes(task?.state);

/** Server outcomes are authoritative; a generic completed job never implies delivery. */
export function scopedAutonomyTasks(value, projectId) {
  const tasks = Array.isArray(value?.tasks) ? value.tasks : [];
  return tasks.filter((task) => task && typeof task.id === 'string' && typeof task.room_id === 'string'
    && Object.hasOwn(AUTONOMY_STATES, task.state) && (task.project_id || null) === (projectId || null))
    .sort((a, b) => Number(autonomyUnread(b)) - Number(autonomyUnread(a))
      || Number(autonomyActive(b)) - Number(autonomyActive(a))
      || String(b.updated_at || '').localeCompare(String(a.updated_at || '')));
}

export function autonomyCounts(tasks) {
  return { active: tasks.filter(autonomyActive).length, unread: tasks.filter(autonomyUnread).length, attention: tasks.filter((task) => task.state === 'needs_attention').length };
}

export function checkAutonomyTarget(task, job) {
  if (!task || !job || task.id !== job.id || !task.room_id || task.room_id !== job.group_room_id
    || (task.project_id || null) !== (job.project_id || null)) throw new Error('后台任务与项目群记录不匹配，请刷新任务列表。');
  return job;
}
