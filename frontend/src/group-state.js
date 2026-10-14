import { EMPTY_CONTEXT } from './platform-settings.js';
import { hydrateJob } from './workbench-state.js';

export const groupScopeKey = (projectId) => `modeldesk.groups.v1:${projectId || 'unassigned'}`;
export const groupDraftKey = (projectId, roomId) => `${groupScopeKey(projectId)}:${roomId}:draft`;
export const groupActive = (job) => ['running', 'queued'].includes(job?.status);

export const GROUP_HARNESS_MODES = [
  { id: 'relay', label: '单轮接力', rounds: 1, description: '所选成员按顺序依次发言' },
  { id: 'consensus', label: '多轮深研 (2轮)', rounds: 2, description: '成员交叉质询与补充，形成两轮合议结论' },
  { id: 'discuss_muse', label: '讨论 → Muse 协同', rounds: 1, description: '群内模型先讨论优化方案，自动交接给 Muse 执行' },
  { id: 'discuss_agent', label: '讨论 → 代码 Agent', rounds: 1, description: '群内模型先明确实现方案，自动转交 Codex / Claude 执行' },
];

export function readGroupSelection(storage, projectId) {
  try { const value = JSON.parse(storage.getItem(groupScopeKey(projectId)) || '{}'); return typeof value.roomId === 'string' ? value.roomId : null; } catch { return null; }
}
export function readGroupDraft(storage, projectId, roomId) {
  let value = {};
  try { value = JSON.parse(storage.getItem(groupDraftKey(projectId, roomId)) || '{}') || {}; } catch { /* Start clean after corrupt storage. */ }
  return {
    prompt: typeof value.prompt === 'string' ? value.prompt : '',
    recipientIds: Array.isArray(value.recipientIds) ? value.recipientIds.filter((id) => typeof id === 'string') : [],
    includeMuse: Boolean(value.includeMuse),
    harnessMode: GROUP_HARNESS_MODES.some((item) => item.id === value.harnessMode) ? value.harnessMode : 'relay',
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
export function groupMessageRequest({ prompt, recipientIds, context, contextTurns = 6, rounds = 1 }) {
  const allowed = ['snapshot_id', 'attachment_ids', 'include_project_assets', 'knowledge_ids', 'memory_ids', 'memory_scope', 'remember', 'tools', 'mcp_server_ids', 'muse_task_id'];
  const boundedRounds = [1, 2, 3, 4, 5, 6].includes(Number(rounds)) ? Number(rounds) : 1;
  return { ...Object.fromEntries(allowed.filter((key) => context[key] !== undefined).map((key) => [key, structuredClone(context[key])])), prompt: prompt.trim(), recipient_member_ids: [...recipientIds], context_turns: contextTurns, rounds: boundedRounds };
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

export function groupTimeline(jobs, museTasks, projectId, roomId, agentTasks = []) {
  return [
    ...(jobs || []).filter((job) => job.group_room_id === roomId && (job.project_id || null) === (projectId || null)).map((value) => ({ type: 'discussion', value })),
    ...(museTasks || []).filter((task) => task.room_id === roomId && (task.project_id || null) === (projectId || null)).map((value) => ({ type: 'muse', value })),
    ...(agentTasks || []).filter((task) => task.room_id === roomId && (task.project_id || null) === (projectId || null)).map((value) => ({ type: 'agent_task', value })),
  ].sort((a, b) => String(a.value.created_at || '').localeCompare(String(b.value.created_at || '')) || a.value.id.localeCompare(b.value.id));
}

export function extractGroupDeliverable(job, room) {
  const messages = groupMessages(job, room || { team_snapshot: { members: [] } }).filter((item) => item.content?.trim() && !item.error);
  if (!messages.length) return '';
  const summary = messages.map((item) => `【${item.member_name || item.model}】：${item.content.trim()}`).join('\n\n');
  return summary.slice(0, 8000);
}

export function buildMuseHandoffPrompt(userPrompt, job, room) {
  const cleanUser = (userPrompt || job?.prompt || '').trim();
  const deliverable = job ? extractGroupDeliverable(job, room) : '';
  if (!deliverable) return cleanUser;
  return `用户原始任务：\n${cleanUser}\n\n项目群多智能体讨论结论与建议：\n${deliverable}\n\n请根据以上群组讨论结论直接执行并输出完整成果。`.slice(0, 11500);
}

export function groupMemberColor(identifier = '') {
  const colors = ['#b5475b', '#946028', '#a23e86', '#2b7284', '#5967a7', '#3e7960'];
  let hash = 0;
  for (const char of identifier) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return colors[hash % colors.length];
}
