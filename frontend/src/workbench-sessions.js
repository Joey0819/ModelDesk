import { EMPTY_CONTEXT } from './platform-settings.js';

export const SESSION_KEY = 'modeldesk.sessions.v1';
export const DRAFT_KEY = 'modeldesk.conversation-drafts.v1';
export function readConversationDrafts(storage, key = DRAFT_KEY) {
  try {
    const value = JSON.parse(storage.getItem(key) || '{}');
    return value && typeof value === 'object' && !Array.isArray(value) ? Object.fromEntries(Object.entries(value).filter(([, draft]) => typeof draft === 'string')) : {};
  } catch { return {}; }
}
export function conversationDraft(drafts, job) {
  return drafts[job.conversation_id || job.id] || '';
}
export function reviewContinuation(jobs, projectId) {
  return [...jobs].filter((job) => job.mode === 'review' && job.project_id === projectId && ['complete', 'completed'].includes(job.status) && job.conversation_id).sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))[0] || null;
}
export function readSessions(storage) {
  try {
    const value = JSON.parse(storage.getItem(SESSION_KEY) || '{}');
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  } catch { return {}; }
}

export function sourceSelection(value = {}) {
  value = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  const context = { ...EMPTY_CONTEXT };
  for (const key of ['knowledge_ids', 'memory_ids', 'mcp_server_ids', 'tools']) {
    context[key] = Array.isArray(value[key]) ? [...new Set(value[key].filter((id) => typeof id === 'string'))] : [];
  }
  for (const key of ['project_id', 'snapshot_id']) context[key] = typeof value[key] === 'string' ? value[key] : null;
  context.memory_scope = typeof value.memory_scope === 'string' && value.memory_scope.trim() ? value.memory_scope : 'default';
  context.remember = value.remember === true;
  return context;
}

export function conversationPreferences(value = {}) {
  value = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  return {
    models: Array.isArray(value.models) ? [...new Set(value.models.filter((id) => typeof id === 'string'))].slice(0, 4) : [],
    chairman: typeof value.chairman === 'string' ? value.chairman : 'gpt-6.1-sol',
    rounds: Math.max(1, Math.min(6, Number(value.rounds) || 2)),
    context_turns: Math.max(1, Math.min(40, Number(value.context_turns) || 12)),
    system_prompt: typeof value.system_prompt === 'string' ? value.system_prompt : '',
    relay_prompt: typeof value.relay_prompt === 'string' ? value.relay_prompt : '请阅读其他参与者的发言，补充新的观点或指出具体问题，避免重复。',
    roles: value.roles && typeof value.roles === 'object' && !Array.isArray(value.roles) ? Object.fromEntries(Object.entries(value.roles).filter(([, role]) => typeof role === 'string')) : {},
  };
}

export function restoredSession(sessions, selectedMode, job) {
  const saved = sessions[selectedMode];
  const matching = saved?.job_id === job.id;
  return { context: sourceSelection(matching && saved.context ? saved.context : job), preferences: conversationPreferences(matching && saved.preferences ? saved.preferences : job) };
}

export function taskTitle(prompt = '') {
  const first = prompt.replace(/^任务要求\s*(失败\s*)?(\d{1,2}\/\d{1,2}\s+\d{1,2}:\d{2}\s*)?/, '').split(/\n+/).find((line) => line.trim())?.replace(/^#{1,6}\s*/, '').replace(/\s+/g, ' ').trim() || '未命名会话';
  return first.length > 110 ? `${first.slice(0, 110)}…` : first;
}

export function conversationGroups(jobs, query = '') {
  const groups = new Map();
  for (const job of [...jobs].sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at))) {
    if (job.mode === 'review' || job.mode === 'sdk_script') continue;
    const id = job.conversation_id || job.id;
    const group = groups.get(id) || { id, title: taskTitle(job.prompt), latest: job, jobs: [], modes: [] };
    group.jobs.push(job);
    group.latest = job;
    if (!group.modes.includes(job.mode)) group.modes.push(job.mode);
    groups.set(id, group);
  }
  const needle = query.trim().toLowerCase();
  return [...groups.values()].filter((group) => !needle || [group.title, ...group.jobs.map((job) => job.prompt), ...group.modes].join(' ').toLowerCase().includes(needle)).sort((a, b) => Date.parse(b.latest.created_at) - Date.parse(a.latest.created_at));
}

export function previousTurns(jobs, job) {
  if (!job) return [];
  return jobs.filter((item) => item.id !== job.id && item.conversation_id === job.conversation_id && (item.project_id || null) === (job.project_id || null) && Date.parse(item.created_at) < Date.parse(job.created_at)).sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at));
}
