import { EMPTY_CONTEXT } from './platform-settings.js';
import { hydrateJob } from './workbench-state.js';

export const groupScopeKey = (projectId) => `modeldesk.groups.v1:${projectId || 'unassigned'}`;
export const groupDraftKey = (projectId, roomId) => `${groupScopeKey(projectId)}:${roomId}:draft`;
export const groupActive = (job) => ['running', 'queued'].includes(job?.status);
export function readGroupSelection(storage, projectId) {
  try { const value = JSON.parse(storage.getItem(groupScopeKey(projectId)) || '{}'); return typeof value.roomId === 'string' ? value.roomId : null; } catch { return null; }
}
export function readGroupDraft(storage, projectId, roomId) {
  let value = {};
  try { value = JSON.parse(storage.getItem(groupDraftKey(projectId, roomId)) || '{}') || {}; } catch { /* Start clean after corrupt storage. */ }
  return { prompt: typeof value.prompt === 'string' ? value.prompt : '', recipientIds: Array.isArray(value.recipientIds) ? value.recipientIds.filter((id) => typeof id === 'string') : [],
    context: { ...EMPTY_CONTEXT, ...(value.context || {}), project_id: projectId || null },
    contextTurns: [1, 4, 6, 8, 12].includes(value.contextTurns) ? value.contextTurns : 6,
  };
}
export function scopedRooms(rooms, projectId) {
  return (Array.isArray(rooms) ? rooms : []).filter((room) => (room.project_id || null) === (projectId || null)).sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')));
}
export function mergeRoomJobs(current, incoming, projectId, roomId) {
  const values = new Map(current.filter((job) => job.group_room_id === roomId && (job.project_id || null) === (projectId || null)).map((job) => [job.id, job]));
  for (const job of incoming || []) {
    if (job.group_room_id !== roomId || (job.project_id || null) !== (projectId || null)) continue;
    const previous = values.get(job.id);
    if (!previous || String(job.updated_at || '') >= String(previous.updated_at || '')) values.set(job.id, hydrateJob(job));
  }
  return [...values.values()].sort((a, b) => String(a.created_at || '').localeCompare(String(b.created_at || '')) || a.id.localeCompare(b.id));
}
export function groupMessageRequest({ prompt, recipientIds, context, contextTurns = 6 }) {
  const allowed = ['snapshot_id', 'attachment_ids', 'include_project_assets', 'knowledge_ids', 'memory_ids', 'memory_scope', 'remember', 'tools', 'mcp_server_ids', 'muse_task_id'];
  return { ...Object.fromEntries(allowed.filter((key) => context[key] !== undefined).map((key) => [key, structuredClone(context[key])])), prompt: prompt.trim(), recipient_member_ids: [...recipientIds], context_turns: contextTurns, rounds: 1 };
}
export function groupMessages(job, room) {
  const members = job.group_team_snapshot?.members || room.team_snapshot?.members || [];
  return (job.result?.messages || []).map((message) => {
    const member = members.find((item) => message.member_id ? item.id === message.member_id : item.model === message.model);
    return { ...message, member_id: message.member_id || member?.id, member_name: message.member_name || member?.name || message.model, role: message.role || member?.role || '' };
  });
}

export function groupRecipients(job, room) {
  const members = job.group_team_snapshot?.members || room.team_snapshot?.members || [];
  return job.group_recipient_member_ids?.length ? members.filter((member) => job.group_recipient_member_ids.includes(member.id)) : members;
}

export function groupTimeline(jobs, museTasks, projectId, roomId) {
  return [
    ...(jobs || []).filter((job) => job.group_room_id === roomId && (job.project_id || null) === (projectId || null)).map((value) => ({ type: 'discussion', value })),
    ...(museTasks || []).filter((task) => task.room_id === roomId && (task.project_id || null) === (projectId || null)).map((value) => ({ type: 'muse', value })),
  ].sort((a, b) => String(a.value.created_at || '').localeCompare(String(b.value.created_at || '')) || a.value.id.localeCompare(b.value.id));
}

export function groupMemberColor(identifier = '') {
  const colors = ['#b5475b', '#946028', '#a23e86', '#2b7284', '#5967a7', '#3e7960'];
  let hash = 0;
  for (const char of identifier) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return colors[hash % colors.length];
}
