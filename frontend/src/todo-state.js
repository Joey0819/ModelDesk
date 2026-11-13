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

/**
 * Compute reorder result after dragging a task to a new position.
 * Returns { tasks: [...], reorderPayload: { ids, status, position_start } }
 */
export function computeReorder(tasks, draggedId, targetStatus, targetIndex) {
  const task = tasks.find((t) => t.id === draggedId);
  if (!task) return { tasks, reorderPayload: null };

  // Remove dragged task from list
  const without = tasks.filter((t) => t.id !== draggedId);
  // Get tasks in target column (excluding dragged)
  const inColumn = without.filter((t) => t.status === targetStatus).sort((a, b) => (a.position || 0) - (b.position || 0));
  // Insert at target index
  const clampedIndex = Math.max(0, Math.min(targetIndex, inColumn.length));
  inColumn.splice(clampedIndex, 0, task);

  // Build new positions
  const reorderedIds = inColumn.map((t) => t.id);
  const updatedTasks = without.map((t) => ({ ...t }));
  // Update the dragged task
  const draggedUpdated = { ...task, status: targetStatus, position: clampedIndex, updated_at: new Date().toISOString() };
  updatedTasks.push(draggedUpdated);

  // Update positions for all tasks in the target column
  for (let i = 0; i < inColumn.length; i++) {
    const id = inColumn[i].id;
    if (id === draggedId) continue;
    const existing = updatedTasks.find((t) => t.id === id);
    if (existing) {
      existing.position = i;
      existing.updated_at = new Date().toISOString();
    }
  }

  return {
    tasks: updatedTasks,
    reorderPayload: {
      ids: reorderedIds,
      status: targetStatus,
      position_start: 0,
    },
  };
}

/**
 * Filter tasks by search, priority, showDone, showArchived.
 */
export function filterTasks(tasks, filters = {}) {
  const { search = '', priority = '', showDone = true, showArchived = false } = filters;
  let result = tasks;

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
  } else if (/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/.test(fields.title)) {
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
