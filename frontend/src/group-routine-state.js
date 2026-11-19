export const ROUTINE_TRIGGERS = {
  manual: '仅手动',
  daily: '每天',
  weekly: '每周',
  board_card: '创建带标签的看板卡片',
};

export const ROUTINE_WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];

export const ROUTINE_RUN_STATES = {
  queued: '已接收，等待执行',
  running: '执行中',
  retry_wait: '等待重试',
  paused: '已暂停',
  needs_attention: '需要处理',
  cancelled: '已取消',
  complete: '已完成',
  skipped: '已跳过',
};

export const routineTimed = (kind) => kind === 'daily' || kind === 'weekly';

export function routineDraft(routine) {
  const specification = {
    name: routine?.name || '',
    goal: routine?.goal || '',
    process: routine?.process || '',
    checks: routine?.checks || '',
    trigger: {
      kind: 'manual',
      time: '09:00',
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
      weekday: 0,
      tag: '',
      ...Object.fromEntries(Object.entries(routine?.trigger || {}).filter(([, value]) => value != null)),
    },
    document_formats: routine?.document_formats || [],
    allow_native: Boolean(routine?.allow_native),
  };
  return { id: routine?.id || null, revision: routine?.revision, specification };
}

export function routineSpec(specification) {
  const { name, goal, process, checks, trigger, document_formats, allow_native } = specification;
  const { kind, time, timezone, weekday, tag } = trigger;
  return {
    name: name.trim(), goal: goal.trim(), process: process.trim(), checks: checks.trim(),
    trigger: {
      kind,
      ...(routineTimed(kind) ? { time, timezone: timezone.trim() } : {}),
      ...(kind === 'weekly' ? { weekday } : {}),
      ...(kind === 'board_card' ? { tag: tag.trim() } : {}),
    },
    document_formats,
    allow_native,
  };
}

export function routineTriggerLabel({ trigger, allow_native }) {
  const { kind, time, timezone, weekday, tag } = trigger;
  if (kind === 'board_card') return `${allow_native ? '本机卡片触发' : '看板新卡片'} · ${tag}`;
  if (routineTimed(kind)) return `${kind === 'weekly' ? ROUTINE_WEEKDAYS[weekday] : '每天'} ${time} · ${timezone}`;
  return ROUTINE_TRIGGERS.manual;
}

export function routineDateLabel(value, timezone) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  try {
    return new Intl.DateTimeFormat('zh-CN', {
      year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
      hourCycle: 'h23', timeZone: timezone,
    }).format(date);
  } catch { return ''; }
}

export function readRoutineStorage(key) {
  try { return JSON.parse(globalThis.localStorage.getItem(key) || 'null'); }
  catch { return null; }
}

export function writeRoutineStorage(key, value) {
  try {
    if (value === null) globalThis.localStorage.removeItem(key);
    else globalThis.localStorage.setItem(key, JSON.stringify(value));
  } catch { /* The current session can still retry with the same receipt. */ }
}

export function routineStorageKey(projectId, roomId, suffix) {
  return `modeldesk.group.routines.${JSON.stringify([projectId || null, roomId, suffix])}`;
}
