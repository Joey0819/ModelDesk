import test from 'node:test';
import assert from 'node:assert/strict';
import {
  COLUMNS, groupByStatus, computeReorder, filterTasks, countByStatus, validateTask,
  buildTaskPayload, isFleetReassignment, hasWorkbenchResult, checkWorkbenchResult,
} from '../src/todo-state.js';

// --- groupByStatus ---

test('groupByStatus groups tasks by their status', () => {
  const tasks = [
    { id: '1', status: 'todo', position: 0 },
    { id: '2', status: 'doing', position: 0 },
    { id: '3', status: 'todo', position: 1 },
    { id: '4', status: 'done', position: 0 },
  ];
  const grouped = groupByStatus(tasks);
  assert.equal(grouped.todo.length, 2);
  assert.equal(grouped.doing.length, 1);
  assert.equal(grouped.done.length, 1);
  assert.equal(grouped.blocked.length, 0);
  assert.equal(grouped.review.length, 0);
});

test('groupByStatus sorts by position within each group', () => {
  const tasks = [
    { id: 'a', status: 'todo', position: 5 },
    { id: 'b', status: 'todo', position: 1 },
    { id: 'c', status: 'todo', position: 3 },
  ];
  const grouped = groupByStatus(tasks);
  assert.deepEqual(grouped.todo.map((t) => t.id), ['b', 'c', 'a']);
});

test('groupByStatus handles empty input', () => {
  const grouped = groupByStatus([]);
  for (const col of COLUMNS) {
    assert.equal(grouped[col.id].length, 0);
  }
});

// --- filterTasks ---

test('filterTasks filters by search text', () => {
  const tasks = [
    { id: '1', title: '修复登录Bug', description: '', assignee: '', tags: [], archived: false, status: 'todo' },
    { id: '2', title: '编写文档', description: 'API文档', assignee: '', tags: [], archived: false, status: 'todo' },
  ];
  const result = filterTasks(tasks, { search: 'bug' });
  assert.equal(result.length, 1);
  assert.equal(result[0].id, '1');
});

test('filterTasks searches in description, assignee and tags', () => {
  const tasks = [
    { id: '1', title: '任务A', description: '', assignee: '张三', tags: [], archived: false, status: 'todo' },
    { id: '2', title: '任务B', description: '', assignee: '', tags: ['前端'], archived: false, status: 'todo' },
    { id: '3', title: '任务C', description: '重要说明', assignee: '', tags: [], archived: false, status: 'todo' },
  ];
  assert.equal(filterTasks(tasks, { search: '张三' }).length, 1);
  assert.equal(filterTasks(tasks, { search: '前端' }).length, 1);
  assert.equal(filterTasks(tasks, { search: '重要' }).length, 1);
});

test('filterTasks filters by priority', () => {
  const tasks = [
    { id: '1', title: 'A', priority: 'high', archived: false, status: 'todo' },
    { id: '2', title: 'B', priority: 'low', archived: false, status: 'todo' },
    { id: '3', title: 'C', priority: 'high', archived: false, status: 'todo' },
  ];
  const result = filterTasks(tasks, { priority: 'high' });
  assert.equal(result.length, 2);
});

test('filterTasks hides done when showDone is false', () => {
  const tasks = [
    { id: '1', title: 'A', status: 'todo', archived: false },
    { id: '2', title: 'B', status: 'done', archived: false },
  ];
  const result = filterTasks(tasks, { showDone: false });
  assert.equal(result.length, 1);
  assert.equal(result[0].status, 'todo');
});

test('filterTasks hides archived when showArchived is false', () => {
  const tasks = [
    { id: '1', title: 'A', status: 'todo', archived: false },
    { id: '2', title: 'B', status: 'todo', archived: true },
  ];
  assert.equal(filterTasks(tasks, { showArchived: false }).length, 1);
  assert.equal(filterTasks(tasks, { showArchived: true }).length, 2);
});

test('filterTasks combines multiple filters', () => {
  const tasks = [
    { id: '1', title: '紧急修复', priority: 'urgent', status: 'todo', archived: false },
    { id: '2', title: '日常维护', priority: 'low', status: 'done', archived: false },
    { id: '3', title: '紧急部署', priority: 'urgent', status: 'done', archived: false },
  ];
  const result = filterTasks(tasks, { search: '紧急', priority: 'urgent', showDone: false });
  assert.equal(result.length, 1);
  assert.equal(result[0].id, '1');
});

// --- computeReorder ---

test('computeReorder moves task to target column at correct index', () => {
  const tasks = [
    { id: 'a', status: 'todo', position: 0 },
    { id: 'b', status: 'todo', position: 1 },
    { id: 'c', status: 'doing', position: 0 },
  ];
  const { reorderPayload } = computeReorder(tasks, 'a', 'doing', 0);
  assert.ok(reorderPayload);
  assert.equal(reorderPayload.status, 'doing');
  assert.deepEqual(reorderPayload.ids, ['a', 'c']);
});

test('computeReorder handles moving within same column', () => {
  const tasks = [
    { id: 'a', status: 'todo', position: 0 },
    { id: 'b', status: 'todo', position: 1 },
    { id: 'c', status: 'todo', position: 2 },
  ];
  const { reorderPayload } = computeReorder(tasks, 'c', 'todo', 0);
  assert.ok(reorderPayload);
  assert.deepEqual(reorderPayload.ids, ['c', 'a', 'b']);
});

test('computeReorder returns null payload for nonexistent task', () => {
  const tasks = [{ id: 'a', status: 'todo', position: 0 }];
  const { reorderPayload } = computeReorder(tasks, 'nonexistent', 'todo', 0);
  assert.equal(reorderPayload, null);
});

test('mirrored cards can be reordered in their current column without changing status', () => {
  const tasks = [{ id: 'agent', status: 'doing', external_source: { platform: 'group' } }];
  assert.equal(computeReorder(tasks, 'agent', 'done', 0).reorderPayload, null);
  assert.deepEqual(computeReorder(tasks, 'agent', 'doing', 0).reorderPayload.ids, ['agent']);
});

test('only dragging an active blocked Fleet card to todo requests reassignment', () => {
  const card = { id: 'fleet', status: 'blocked', external_source: { platform: 'fleet' } };
  assert.equal(isFleetReassignment(card, 'todo'), true);
  assert.equal(isFleetReassignment(card, 'done'), false);
  assert.equal(isFleetReassignment({ ...card, status: 'doing' }, 'todo'), false);
  assert.equal(isFleetReassignment({ ...card, archived: true }, 'todo'), false);
  assert.equal(isFleetReassignment({ ...card, external_source: { platform: 'group' } }, 'todo'), false);
});

test('mirrored card payload updates only the fields edited in the modal', () => {
  const original = {
    id: 'fleet', title: 'Queued', description: 'Queued detail', status: 'todo',
    priority: 'medium', tags: ['Fleet'], external_source: { platform: 'fleet' },
  };
  assert.deepEqual(buildTaskPayload({ ...original, priority: 'urgent', status: 'doing' }, original), {
    priority: 'urgent',
  });
  const manual = { id: 'manual', title: 'Write tests', status: 'todo' };
  assert.deepEqual(buildTaskPayload({ ...manual, status: 'doing' }, manual), { status: 'doing' });
});

test('result navigation requires a terminal Workbench card and an exact saved job and project', () => {
  const task = { project_id: 'project-a', status: 'review', external_source: { platform: 'workbench', task_id: 'job-a' } };
  assert.equal(hasWorkbenchResult(task), true);
  assert.equal(hasWorkbenchResult({ ...task, status: 'doing' }), false);
  assert.equal(hasWorkbenchResult({ ...task, project_id: null }), false);
  assert.equal(hasWorkbenchResult({ ...task, external_source: { platform: 'group', task_id: 'job-a' } }), false);
  assert.doesNotThrow(() => checkWorkbenchResult(task, { id: 'job-a', project_id: 'project-a' }));
  assert.throws(() => checkWorkbenchResult(task, { id: 'job-a', project_id: 'project-b' }), /项目不一致/);
  assert.throws(() => checkWorkbenchResult(task, { id: 'job-b', project_id: 'project-a' }), /项目不一致/);
});

// --- countByStatus ---

test('countByStatus counts correctly', () => {
  const tasks = [
    { status: 'todo' }, { status: 'todo' }, { status: 'doing' },
    { status: 'blocked' }, { status: 'review' }, { status: 'done' }, { status: 'done' },
  ];
  const counts = countByStatus(tasks);
  assert.equal(counts.todo, 2);
  assert.equal(counts.doing, 1);
  assert.equal(counts.blocked, 1);
  assert.equal(counts.review, 1);
  assert.equal(counts.done, 2);
});

test('countByStatus handles empty input', () => {
  const counts = countByStatus([]);
  for (const col of COLUMNS) {
    assert.equal(counts[col.id], 0);
  }
});

// --- validateTask ---

test('validateTask accepts valid fields', () => {
  const errors = validateTask({ title: '正常标题', status: 'todo', priority: 'medium' });
  assert.equal(errors.length, 0);
});

test('validateTask rejects empty title', () => {
  const errors = validateTask({ title: '' });
  assert.ok(errors.some((e) => e.includes('标题')));
});

test('validateTask rejects missing title', () => {
  const errors = validateTask({});
  assert.ok(errors.some((e) => e.includes('标题')));
});

test('validateTask rejects long title', () => {
  const errors = validateTask({ title: 'a'.repeat(201) });
  assert.ok(errors.some((e) => e.includes('200')));
});

test('validateTask rejects control characters in title', () => {
  const errors = validateTask({ title: 'bad\x00title' });
  assert.ok(errors.some((e) => e.includes('控制字符')));
});

test('validateTask rejects invalid status', () => {
  const errors = validateTask({ title: 'ok', status: 'invalid' });
  assert.ok(errors.some((e) => e.includes('状态')));
});

test('validateTask rejects invalid priority', () => {
  const errors = validateTask({ title: 'ok', priority: 'super' });
  assert.ok(errors.some((e) => e.includes('优先级')));
});

test('validateTask rejects invalid due_date format', () => {
  const errors = validateTask({ title: 'ok', due_date: 'not-a-date' });
  assert.ok(errors.some((e) => e.includes('YYYY-MM-DD')));
});

test('validateTask accepts valid due_date', () => {
  const errors = validateTask({ title: 'ok', due_date: '2026-12-31' });
  assert.equal(errors.filter((e) => e.includes('日期')).length, 0);
});

test('validateTask rejects too many tags', () => {
  const tags = Array.from({ length: 21 }, (_, i) => `tag${i}`);
  const errors = validateTask({ title: 'ok', tags });
  assert.ok(errors.some((e) => e.includes('标签')));
});

test('validateTask rejects long tag', () => {
  const errors = validateTask({ title: 'ok', tags: ['a'.repeat(101)] });
  assert.ok(errors.some((e) => e.includes('标签')));
});

test('validateTask rejects long description', () => {
  const errors = validateTask({ title: 'ok', description: 'x'.repeat(4001) });
  assert.ok(errors.some((e) => e.includes('描述')));
});

test('validateTask rejects long assignee', () => {
  const errors = validateTask({ title: 'ok', assignee: 'a'.repeat(101) });
  assert.ok(errors.some((e) => e.includes('负责人')));
});

// --- COLUMNS ---

test('COLUMNS has five entries with correct ids', () => {
  assert.equal(COLUMNS.length, 5);
  assert.deepEqual(COLUMNS.map((c) => c.id), ['todo', 'doing', 'blocked', 'review', 'done']);
});
