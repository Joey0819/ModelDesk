export const ROUTINE_TRIGGERS = {
  manual: '仅手动',
  daily: '每天',
  weekly: '每周',
  interval: '每隔固定时长',
  board_card: '创建带标签的看板卡片',
  board_status: '看板卡片进入指定列',
};

export const ROUTINE_WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];

export const ROUTINE_BOARD_STATUSES = {
  todo: '待办',
  doing: '进行中',
  blocked: '受阻',
  review: '待审核',
  done: '已完成',
};

export const ROUTINE_INTERVAL_PRESETS = [
  [6, '每 6 小时'],
  [12, '每 12 小时'],
  [24, '每 24 小时'],
  [48, '每 2 天（隔天）'],
  [72, '每 3 天'],
];

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

export const ROUTINE_FEEDBACK_VERDICTS = {
  good: '符合预期',
  needs_improvement: '需要改进',
  wrong_judgment: '判断有偏差',
};

export const ROUTINE_DECISION_LABELS = {
  complete: '直接完成',
  wait_details: '记录待补信息',
  ask_human: '停下来求助人工',
};

export const ROUTINE_STARTER_GOALS = [
  {
    label: '每周项目进度简报',
    prompt: '我的目标是每周自动产出一页清晰的项目进度简报，让团队不用手工翻聊天和看板就能掌握进展、风险和待决策事项。',
  },
  {
    label: '收集反馈并提炼改进',
    prompt: '我的目标是定期汇总用户或同事反馈，交叉核对已有记录，找出反复出现的问题并给出可执行的流程改进建议。',
  },
  {
    label: '待审核卡片自动核查',
    prompt: '我的目标是每当看板卡片进入待审核列时，自动按交付标准核对内容与缺漏，明确标出通过项和需要补齐的项。',
  },
  {
    label: '跟进客户 / 工单不遗漏',
    prompt: '我的目标是按关联编号持续跟进重要事项，信息齐全时直接整理交付，缺细节时记录待补状态并提醒我，避免重复处理。',
  },
];

export const routineTimed = (kind) => kind === 'daily' || kind === 'weekly';
export const routineScheduled = (kind) => kind === 'daily' || kind === 'weekly' || kind === 'interval';

export function routineDraft(routine) {
  const specification = {
    name: routine?.name || '',
    goal: routine?.goal || '',
    process: routine?.process || '',
    judgment: routine?.judgment || '',
    checks: routine?.checks || '',
    examples: routine?.examples || '',
    correlation_field: routine?.correlation_field || '',
    trigger: {
      kind: 'manual',
      time: '09:00',
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
      weekday: 0,
      every_hours: 48,
      tag: '',
      board_status: 'review',
      ...Object.fromEntries(Object.entries(routine?.trigger || {}).filter(([, value]) => value != null)),
    },
    document_formats: routine?.document_formats || [],
    allow_native: Boolean(routine?.allow_native),
    auto_reflect: Boolean(routine?.auto_reflect),
  };
  return { id: routine?.id || null, revision: routine?.revision, specification };
}

export function routineSpec(specification) {
  const {
    name = '', goal = '', process = '', judgment = '', checks = '', examples = '',
    correlation_field = '', trigger = { kind: 'manual' }, document_formats = [],
    allow_native = false, auto_reflect = false,
  } = specification;
  const { kind = 'manual', time, timezone = '', weekday, every_hours, tag = '', board_status } = trigger;
  const cleanTag = String(tag || '').trim();
  const hours = Number.parseInt(every_hours, 10);
  return {
    name: name.trim(),
    goal: goal.trim(),
    process: process.trim(),
    judgment: judgment.trim(),
    checks: checks.trim(),
    examples: examples.trim(),
    correlation_field: correlation_field.trim(),
    trigger: {
      kind,
      ...(routineTimed(kind) ? { time, timezone: timezone.trim() } : {}),
      ...(kind === 'weekly' ? { weekday } : {}),
      ...(kind === 'interval' ? { every_hours: Number.isFinite(hours) ? hours : 48 } : {}),
      ...(kind === 'board_card' ? { tag: cleanTag } : {}),
      ...(kind === 'board_status' ? { board_status, ...(cleanTag ? { tag: cleanTag } : {}) } : {}),
    },
    document_formats,
    allow_native: Boolean(allow_native),
    auto_reflect: Boolean(auto_reflect),
  };
}

export function routineTriggerLabel({ trigger, allow_native }) {
  const { kind, time, timezone, weekday, every_hours, tag, board_status } = trigger || { kind: 'manual' };
  if (kind === 'board_card') return `${allow_native ? '本机卡片触发' : '看板新卡片'} · ${tag}`;
  if (kind === 'board_status') {
    const column = ROUTINE_BOARD_STATUSES[board_status] || board_status;
    return `${allow_native ? '本机列变更' : '看板进入'}「${column}」${tag ? ` · ${tag}` : ''}`;
  }
  if (kind === 'interval') {
    const hours = Number(every_hours) || 0;
    if (hours >= 24 && hours % 24 === 0) return `每 ${hours / 24} 天运行`;
    return `每 ${hours} 小时运行`;
  }
  if (routineTimed(kind)) return `${kind === 'weekly' ? ROUTINE_WEEKDAYS[weekday] : '每天'} ${time} · ${timezone}`;
  return ROUTINE_TRIGGERS.manual;
}

export function routineReadiness(specification) {
  const spec = specification || {};
  const checks = {
    goal: Boolean((spec.goal || '').trim()),
    process: Boolean((spec.process || '').trim()),
    judgment: Boolean((spec.judgment || '').trim()),
    checks: Boolean((spec.checks || '').trim()),
    examples: Boolean((spec.examples || '').trim()),
    trigger: (spec.trigger?.kind || 'manual') !== 'manual',
  };
  const score = Object.values(checks).filter(Boolean).length;
  return { ...checks, score, total: 6 };
}

export function routineDurationLabel(seconds) {
  if (seconds == null || Number.isNaN(Number(seconds))) return '';
  const total = Math.max(0, Math.round(Number(seconds)));
  if (total < 60) return `${total}秒`;
  const minutes = Math.floor(total / 60);
  const rem = total % 60;
  if (minutes < 60) return rem ? `${minutes}分${rem}秒` : `${minutes}分`;
  const hours = Math.floor(minutes / 60);
  const remMin = minutes % 60;
  return remMin ? `${hours}小时${remMin}分` : `${hours}小时`;
}

export function appendRuleText(existing, addition, limit = 2000) {
  const cleanAdd = String(addition || '').trim();
  if (!cleanAdd) return String(existing || '');
  const cleanBase = String(existing || '').trim();
  if (!cleanBase) return cleanAdd.slice(0, limit);
  if (cleanBase.includes(cleanAdd)) return cleanBase;
  return `${cleanBase}\n- ${cleanAdd}`.slice(0, limit);
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
