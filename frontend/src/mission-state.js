import { sourceSelection } from './workbench-sessions.js';
import { copyRunTeam, runTeamSelection } from './mission-teams.js';

export const RUN_STATUS = {
  queued: '等待开始', running: '进行中', waiting_review: '等待你审阅',
  complete: '已完成', error: '需要处理', cancelled: '已停止', interrupted: '等待恢复', pending: '待执行',
};

export const missionActive = (run) => ['queued', 'running'].includes(run?.status);
export const missionRecoverable = (run) => ['error', 'cancelled', 'interrupted'].includes(run?.status);
export const missionScopeKey = (projectId) => `modeldesk.missions.v1:${projectId || 'unassigned'}`;

export function readMissionWorkspace(storage, projectId) {
  try {
    const value = JSON.parse(storage.getItem(missionScopeKey(projectId)) || '{}');
    return {
      runId: typeof value.runId === 'string' ? value.runId : null,
      draft: typeof value.draft === 'string' ? value.draft : '',
      templateId: typeof value.templateId === 'string' ? value.templateId : 'research-review',
      steps: Array.isArray(value.steps) ? value.steps : null,
      requestOptions: value.requestOptions && typeof value.requestOptions === 'object' && !Array.isArray(value.requestOptions) ? value.requestOptions : {},
      models: Array.isArray(value.models) && value.models.every((item) => typeof item === 'string') ? value.models : null,
      team: value.team && typeof value.team === 'object' && Array.isArray(value.team.members) ? value.team : null,
    };
  } catch { return { runId: null, draft: '', templateId: 'research-review', steps: null, requestOptions: {}, team: null, models: null }; }
}

export function nextStepId(steps) {
  let index = 1;
  while (steps.some((step) => step.id === `step_${index}`)) index += 1;
  return `step_${index}`;
}

export function missionRequest({ goal, templateId, steps, models, context, requestOptions = {}, team = null }) {
  // RunRequest rejects unknown fields; never spread the full UI state or a runtime run.
  const options = Object.fromEntries(['system_prompt', 'timeout_seconds', 'step_timeout_seconds', 'max_attempts'].filter((key) => requestOptions[key] !== undefined).map((key) => [key, requestOptions[key]]));
  return { ...options, ...sourceSelection(context), ...runTeamSelection(team, models), goal: goal.trim(), template_id: templateId, models: [...models], steps: structuredClone(steps) };
}

export function copyMissionDraft(run) {
  const request = run.request || {};
  return {
    draft: request.goal || run.goal || '', templateId: request.template_id || 'research-review',
    steps: structuredClone(request.steps || run.steps.map((step) => step.config)),
    models: [...(request.models || [])], context: sourceSelection(request),
    requestOptions: structuredClone(request),
    team: copyRunTeam(run),
  };
}

export function scopedMissions(items, projectId) {
  return (Array.isArray(items) ? items : []).filter((run) => (run.project_id || null) === (projectId || null))
    .sort((a, b) => String(b.updated_at || b.created_at || '').localeCompare(String(a.updated_at || a.created_at || '')));
}

export function missionProgress(run) {
  const steps = run?.steps || [];
  const complete = steps.filter((step) => step.status === 'complete').length;
  const current = steps.find((step) => step.id === run?.active_step_id)
    || steps.find((step) => step.status !== 'complete');
  return { complete, total: steps.length, current, percent: steps.length ? Math.round(complete / steps.length * 100) : 0 };
}

export function stepJobId(step) {
  return step?.attempts?.at(-1)?.job_id || step?.output?.job_id || null;
}

export function selectedMissionStep(run, selection) {
  const steps = run?.steps || [];
  return (selection?.runId === run?.id && steps.find((step) => step.id === selection.stepId))
    || steps.find((step) => step.id === run?.active_step_id)
    || [...steps].reverse().find((step) => stepJobId(step))
    || steps[0];
}

export function missionErrors({ goal, steps, models, catalog = [], online = true, attachments = [] }) {
  const errors = [];
  if (!goal.trim()) errors.push('先写下希望完成的任务。');
  if (!online) errors.push('本地网关未连接，连接后可以开始。');
  if (!models.length) errors.push('请选择参与模型。');
  if (!steps?.length) errors.push('至少保留一个协作步骤。');
  if (steps?.length > 6) errors.push('一次任务最多包含 6 个步骤。');
  for (const step of steps || []) {
    const selected = step.models?.length ? step.models : models;
    if (!step.title?.trim() || !step.instructions?.trim()) errors.push('每个步骤都需要名称与执行要求。');
    if (['council', 'group', 'camel'].includes(step.mode) && selected.length < 2) errors.push(`${step.title || '此步骤'}需要至少两个模型。`);
    if (['crewai', 'camel_workforce', 'crewai_flow'].includes(step.mode) && !step.workflow && selected.length < 2) errors.push(`${step.title || '此步骤'}的默认团队需要两个模型。`);
    if (step.mode === 'review' && (step.engine || 'council') !== 'chat' && selected.length < 2) errors.push('代码审查的当前引擎需要至少两个模型。');
    if (catalog.length && selected.some((model) => !catalog.some((item) => item.id === model))) errors.push('已选模型中有当前不可用的模型，请重新选择。');
  }
  return [...new Set([...errors, ...attachments])];
}
