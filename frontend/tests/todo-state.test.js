import test from 'node:test';
import assert from 'node:assert/strict';
import { COLUMNS, groupByStatus, computeReorder, filterTasks, countByStatus, validateTask } from '../src/todo-state.js';

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
