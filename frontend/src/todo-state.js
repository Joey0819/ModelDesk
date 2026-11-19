/** Pure-function module for Todo Kanban board state management. */

export const COLUMNS = [
  { id: 'todo', label: '待办' },
  { id: 'doing', label: '进行中' },
  { id: 'blocked', label: '受阻' },
  { id: 'review', label: '待审核' },
  { id: 'done', label: '已完成' },
];

const VALID_STATUSES = new Set(COLUMNS.map((c) => c.id));
const VALID_PRIORITIES = new Set(['low', 'medium', 'high', 'urgent']);
const RESULT_STATUSES = new Set(['review', 'blocked', 'done']);

// C0 control characters (except tab/newline) plus DEL. Built from char codes so
// the source file itself carries no literal control bytes.
const CONTROL_CHARS = new RegExp(
  `[${String.fromCharCode(0)}-${String.fromCharCode(8)}${String.fromCharCode(11)}${String.fromCharCode(12)}`
  + `${String.fromCharCode(14)}-${String.fromCharCode(31)}${String.fromCharCode(127)}]`,
);

/**
 * Group tasks by status. Returns object with column ids as keys.
 */
export function groupByStatus(tasks) {
  const groups = {};
  for (const col of COLUMNS) groups[col.id] = [];
  for (const task of tasks) {
    if (groups[task.status]) groups[task.status].push(task);
  }
  // Sort each group by position
  for (const key of Object.keys(groups)) {
    groups[key].sort((a, b) => (a.position || 0) - (b.position || 0));
  }
  return groups;
}

/** Anchor the moved card to a visible neighbor so the server can preserve hidden cards' order. */
export function computeMove(task, targetStatus, visibleCards, targetIndex) {
  if (!task || task.archived || !VALID_STATUSES.has(targetStatus)) return null;
  const currentIndex = visibleCards.findIndex((card) => card.id === task.id);
  const neighbors = visibleCards.filter((card) => card.id !== task.id && !card.archived);
  const index = Math.max(0, Math.min(targetIndex, neighbors.length));
  if (task.status === targetStatus && currentIndex >= 0 && visibleCards.slice(0, currentIndex).filter((card) => !card.archived).length === index) return null;
  const before = neighbors[index];
  return {
    id: task.id,
    from_status: task.status,
    status: targetStatus,
    anchor_id: before?.id ?? neighbors.at(-1)?.id ?? null,
    placement: before ? 'before' : 'after',
  };
}

export function sourceStatus(task) {
  return task.source_status ?? task.status;
}

// Technical identifiers are available in card details. Keep the board preview focused on the work.
export function cardPreview(task) {
  const text = task.description || '';
  const source = task.external_source;
  if (!source) return text;
  const lines = text.split('\n');
  if (source.platform === 'workbench' && lines.at(-1) === `任务编号：${source.task_id}` && lines.at(-2)?.startsWith('模式：')) {
    return lines.slice(0, -2).join('\n');
  }
  if (source.platform === 'muse' && text === `Muse 原生任务编号：${source.task_id}`) return '';
  if (source.platform === 'group' && lines.length === 3 && lines[0].startsWith('Kind: ') &&
      lines[1].startsWith('Engine: ') && lines[2] === `ID: ${source.task_id}`) {
    return `执行器 · ${lines[1].slice('Engine: '.length)}`;
  }
  return text;
}

export function canReassignFleet(task) {
  return task?.external_source?.platform === 'fleet' && Boolean(task.external_source.task_id) && !task.archived && sourceStatus(task) === 'blocked';
}

export function hasWorkbenchResult(task) {
  return Boolean(task.project_id && task.external_source?.platform === 'workbench' &&
    task.external_source.task_id && RESULT_STATUSES.has(sourceStatus(task)));
}

export function todoResultRef(task) {
  if (!task || !RESULT_STATUSES.has(sourceStatus(task))) return null;
  const source = task.external_source;
  const kind = source?.platform === 'workbench' ? 'workbench' : source?.platform === 'group' ? 'group_agent' : null;
  if (!kind || !source.task_id) return null;
  const ref = task.result_ref;
  if (!ref) return !Object.hasOwn(task, 'result_ref') && hasWorkbenchResult(task) ? { kind, id: source.task_id, project_id: task.project_id } : null;
  if (ref.kind !== kind || ref.id !== source.task_id || (ref.project_id || null) !== (task.project_id || null) ||
      (task.room_id && ref.room_id !== task.room_id) || (kind === 'group_agent' && (!ref.project_id || !ref.room_id))) return null;
  return ref;
}

export function checkGroupTaskResult(ref, task) {
  const saved = task?.result_ref;
  if (ref?.kind !== 'group_agent' || !ref.project_id || !ref.room_id || task?.id !== ref.id || task.project_id !== ref.project_id || task.room_id !== ref.room_id ||
      saved?.kind !== ref.kind || saved.id !== ref.id || saved.project_id !== ref.project_id || saved.room_id !== ref.room_id ||
      (saved.source_run_id || null) !== (ref.source_run_id || null)) {
    throw new Error('此 Agent 成果与卡片上的项目、群聊或来源运行不一致，请刷新看板后重试。');
  }
}

export function checkWorkbenchResult(task, job) {
  const ref = todoResultRef(task);
  if (ref?.kind !== 'workbench' || job?.id !== ref.id || (job.project_id || null) !== (ref.project_id || null) ||
      (task.result_ref && (job.group_room_id || null) !== (ref.room_id || null))) {
    throw new Error('此任务记录与看板卡片的项目不一致，请刷新看板后重试。');
  }
}

export function taskLocation(scope) {
  return {
    project_id: scope.projectId || null,
    room_id: scope.type === 'room' ? scope.roomId : null,
  };
}

/** Submit editable fields only, keeping the source's execution and location intact during edits. */
export function buildTaskPayload(fields, task) {
  const payload = {
    title: fields.title.trim(),
    description: fields.description || '',
    priority: fields.priority,
    assignee: fields.assignee || '',
    due_date: fields.due_date || null,
    tags: fields.tags || [],
  };
  if (!task?.external_source) {
    payload.project_id = fields.project_id || null;
    payload.room_id = fields.room_id || null;
  }
  if (!task?.id) return { ...payload, status: fields.status };
  const edited = Object.fromEntries(Object.entries(payload).filter(([key]) => fields[key] !== task[key]));
  // The source's current lane may change while the editor is open. Only submit an explicit status change.
  if (fields.status !== task.status) edited.board_status = fields.status;
  return edited;
}

/**
 * Filter tasks by search, priority, showDone, showArchived.
 */
export function filterTasks(tasks, filters = {}) {
  const { search = '', priority = '', showDone = true, showArchived = false, scope } = filters;
  let result = tasks;

  if (scope && scope.type !== 'all') {
    const { project_id, room_id } = taskLocation(scope);
    result = result.filter((task) => (task.project_id || null) === project_id && (!room_id || task.room_id === room_id));
  }

  if (!showArchived) {
    result = result.filter((t) => !t.archived);
  }

  if (!showDone) {
    result = result.filter((t) => t.status !== 'done');
  }

  if (priority) {
    result = result.filter((t) => t.priority === priority);
  }

  if (search) {
    const q = search.toLowerCase();
    result = result.filter(
      (t) =>
        (t.title && t.title.toLowerCase().includes(q)) ||
        (t.description && t.description.toLowerCase().includes(q)) ||
        (t.assignee && t.assignee.toLowerCase().includes(q)) ||
        (t.tags && t.tags.some((tag) => tag.toLowerCase().includes(q))),
    );
  }

  return result;
}

/**
 * Count tasks by status.
 */
export function countByStatus(tasks) {
  const counts = {};
  for (const col of COLUMNS) counts[col.id] = 0;
  for (const task of tasks) {
    if (counts[task.status] !== undefined) counts[task.status]++;
  }
  return counts;
}

/**
 * Validate task fields on the client side.
 * Returns array of error strings. Empty = valid.
 */
export function validateTask(fields) {
  const errors = [];

  if (!fields.title || !fields.title.trim()) {
    errors.push('标题不能为空');
  } else if (fields.title.trim().length > 200) {
    errors.push('标题不能超过 200 个字符');
  } else if (CONTROL_CHARS.test(fields.title)) {
    errors.push('标题包含非法控制字符');
  }

  if (fields.description && fields.description.length > 4000) {
    errors.push('描述不能超过 4000 个字符');
  }

  if (fields.status && !VALID_STATUSES.has(fields.status)) {
    errors.push(`无效的状态值：${fields.status}`);
  }

  if (fields.priority && !VALID_PRIORITIES.has(fields.priority)) {
    errors.push(`无效的优先级：${fields.priority}`);
  }

  if (fields.assignee && fields.assignee.length > 100) {
    errors.push('负责人名称不能超过 100 个字符');
  }

  if (fields.due_date) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(fields.due_date)) {
      errors.push('截止日期格式必须为 YYYY-MM-DD');
    } else {
      const parts = fields.due_date.split('-');
      const d = new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
      if (isNaN(d.getTime())) {
        errors.push('截止日期不是有效日期');
      }
    }
  }

  if (fields.tags) {
    if (fields.tags.length > 20) {
      errors.push('标签数量不能超过 20 个');
    }
    for (const tag of fields.tags) {
      if (tag.length > 100) {
        errors.push('单个标签不能超过 100 个字符');
        break;
      }
    }
  }

  return errors;
}
