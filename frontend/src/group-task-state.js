export const TASK_LABELS = { queued: '排队中', running: '正在执行', complete: '执行结束', error: '执行失败', cancelled: '已停止', interrupted: '已中断' };
export const APPROVAL_LABELS = { pending: '等待你的决定', merged: '已合入本地项目', merging: '正在合入本地项目', needs_attention: '合入结果需要检查', rejected: '已拒绝', failed: '合入失败', stale: '需要重新审阅' };
export const taskActive = (task) => ['queued', 'running'].includes(task?.status);
export const taskScope = (projectId, roomId) => new URLSearchParams({ ...(projectId ? { project_id: projectId } : {}), room_id: roomId || '' }).toString();
export const taskDraftKey = (projectId, roomId) => `modeldesk.group.task-draft.${projectId || 'none'}.${roomId}`;
export function eligibleReviews(task, tasks) {
  if (task?.kind !== 'code' || task.status !== 'complete' || !task.diff_sha256) return [];
  return tasks.filter((review) => review.kind === 'review' && review.status === 'complete' && review.source_task_id === task.id && review.member_id !== task.member_id && review.review_of_patch_sha256 === task.diff_sha256 && review.project_id === task.project_id && review.room_id === task.room_id);
}
export function taskRequest({ projectId, roomId, memberId, engine, kind, prompt, sourceTaskId }) {
  return { project_id: projectId, room_id: roomId, member_id: memberId, engine, kind, prompt: prompt.trim(), ...(kind === 'review' && sourceTaskId ? { source_task_id: sourceTaskId } : {}) };
}
export function approvalDecision(card, decision) {
  if (!['approve', 'reject'].includes(decision) || card?.status !== 'pending' || !card.base_sha || !card.patch_sha256) throw new Error('审批记录已变化，请刷新后重新检查。');
  return { project_id: card.project_id, room_id: card.room_id, decision, expected_base_sha: card.base_sha, expected_patch_sha256: card.patch_sha256 };
}
export function readTaskDraft(storage, projectId, roomId) {
  try { const value = JSON.parse(storage.getItem(taskDraftKey(projectId, roomId)) || '{}'); return { prompt: typeof value.prompt === 'string' ? value.prompt : '', memberId: typeof value.memberId === 'string' ? value.memberId : '', engine: ['codex', 'claude', 'member_model'].includes(value.engine) ? value.engine : '', sourceTaskId: typeof value.sourceTaskId === 'string' ? value.sourceTaskId : '' }; } catch { return { prompt: '', memberId: '', engine: '', sourceTaskId: '' }; }
}

export function engineSupports(engine, kind) { return (engine?.capabilities || []).includes(kind); }
export function taskEngineLabel(task) { const label = task.engine === 'member_model' ? '成员模型' : task.engine === 'claude' ? 'Claude Code' : task.engine === 'codex' ? 'Codex' : task.engine || '执行引擎'; const model = task.engine_model || (task.engine === 'member_model' ? task.member?.model : null); return model ? `${label} · ${model}` : label; }


export function memberEngineSupports(engine, kind, member) {
  const memberKind = member?.kind || 'model';
  if (memberKind === 'muse') return false;
  return engineSupports(engine, kind) && (memberKind === 'model' || engine.id === memberKind);
}
