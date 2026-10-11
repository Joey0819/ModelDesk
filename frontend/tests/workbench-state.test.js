import { test } from 'node:test';
import assert from 'node:assert/strict';
import { applyEvent, hydrateJob } from '../src/workbench-state.js';

const delta = (seq, text, model = 'gpt-6.1-sol') => ({ job_id: 'job', seq, type: 'delta', model, text });

test('refresh restores both model streams and skips replayed sequence numbers', () => {
  const events = [delta(1, '已收到'), delta(2, '第一段'), delta(3, '另一段', 'gemini-3-flash')];
  const restored = hydrateJob({ id: 'job', status: 'running', result: null, events });
  assert.equal(restored.result.responses[0].text, '已收到第一段');
  assert.equal(restored.result.responses[1].text, '另一段');
  assert.strictEqual(applyEvent(restored, events[1]), restored);
  assert.equal(applyEvent(restored, delta(4, '。')).result.responses[0].text, '已收到第一段。');
});

test('identical text chunks with different sequence numbers are retained', () => {
  const restored = hydrateJob({ id: 'job', status: 'running', result: null, events: [delta(1, '哈'), delta(2, '哈')] });
  assert.equal(restored.result.responses[0].text, '哈哈');
});

test('cancel and restart interruption keep already received answers', () => {
  for (const status of ['cancelled', 'interrupted']) {
    const restored = hydrateJob({ id: 'job', status, result: null, events: [delta(1, '部分回答'), { job_id: 'job', seq: 2, type: 'cancelled' }] });
    assert.equal(restored.status, status);
    assert.equal(restored.result.responses[0].text, '部分回答');
  }
});

test('Council hydration preserves completed stages and anonymous ranking metadata', () => {
  const metadata = { label_to_model: { 'Response A': 'gpt-6.1-sol' } };
  const stage1 = [{ model: 'gpt-6.1-sol', response: '独立回答' }];
  const stage2 = [{ model: 'gemini-3-flash', ranking: 'Response A' }];
  const restored = hydrateJob({ id: 'job', status: 'running', result: null, events: [{ job_id: 'job', seq: 1, type: 'stage', stage: 'responses', data: stage1 }, { job_id: 'job', seq: 2, type: 'stage', stage: 'rankings', data: stage2, metadata }] });
  assert.deepEqual(restored.result, { stage1, stage2, metadata });
});

test('authoritative completed result replaces accumulated streaming fragments', () => {
  const result = { responses: [{ model: 'gpt-6.1-sol', text: '完整回答' }] };
  const restored = hydrateJob({ id: 'job', status: 'completed', result, events: [delta(1, '部分回答')] });
  assert.deepEqual(restored.result, result);
});

test('group discussion preserves model attribution and completed rounds after interruption', () => {
  const messages = [
    { job_id: 'job', seq: 1, type: 'message', model: 'gpt-6.1-sol', round: 1, content: '提出方案' },
    { job_id: 'job', seq: 2, type: 'message', model: 'gemini-3-flash', round: 1, content: '质询前面的方案' },
    { job_id: 'job', seq: 3, type: 'message', model: 'gpt-6.1-sol', round: 2, content: '修改方案' },
  ];
  const restored = hydrateJob({ id: 'job', mode: 'group', status: 'cancelled', result: null, events: messages });
  assert.deepEqual(restored.result.messages, messages.map(({ model, round, content }) => ({ model, round, content })));
  assert.strictEqual(applyEvent(restored, messages[2]), restored);
});

test('custom framework roles and completed tasks remain visible before a final result', () => {
  const events = [
    { job_id: 'job', seq: 1, type: 'framework_message', role: '统计审查员', model: 'gemini-3-flash', round: 2, content: '检查独立样本与重复测量' },
    { job_id: 'job', seq: 2, type: 'framework_task', id: 'stats', name: 'stats', agent: 'analyst', description: '计算', context: [], output: '结果 42' },
    { job_id: 'job', seq: 3, type: 'tool_result', tool: 'arithmetic', input: '6*7', output: 42 },
  ];
  const restored = hydrateJob({ id: 'job', status: 'interrupted', result: null, events });
  assert.equal(restored.result.partial, true);
  assert.equal(restored.result.messages[0].role, '统计审查员');
  assert.equal(restored.result.tasks[0].output, '结果 42');
  assert.equal(restored.events.filter((event) => event.type === 'tool_result')[0].output, 42);
});

test('terminal event uses canonical backend status names', () => {
  assert.equal(applyEvent({ id: 'job', events: [] }, { job_id: 'job', type: 'complete', result: {} }).status, 'complete');
  assert.equal(applyEvent({ id: 'job', events: [] }, { job_id: 'job', type: 'error', message: 'failed' }).status, 'error');
});

test('actual group failure and length-limit events preserve their evidence after refresh', () => {
  const events = [
    { job_id: 'job', seq: 1, type: 'message', model: 'gpt-6.1-sol', round: 1, content: '', error: '模型网关返回 HTTP 403；请检查登录权限或配额。' },
    { job_id: 'job', seq: 2, type: 'message', model: 'gemini-3-flash', round: 1, content: '已收到的部分发言', incomplete: true },
    { job_id: 'job', seq: 3, type: 'message', model: 'gpt-6.1-sol', round: 2, content: '后一轮成功发言' },
  ];
  const restored = hydrateJob({ id: 'job', mode: 'group', status: 'cancelled', result: null, events });
  assert.equal(restored.result.partial_failure, true);
  assert.equal(restored.result.messages[0].error, events[0].error);
  assert.equal(restored.result.messages[1].incomplete, true);
  assert.equal(restored.result.messages[1].content, '已收到的部分发言');
  assert.equal(restored.result.messages[2].content, '后一轮成功发言');
  assert.strictEqual(applyEvent(restored, events[1]), restored);
});

test('flow route skip and actual native model provenance survive partial task event hydration', () => {
  const event = { job_id: 'job', seq: 1, type: 'framework_task', id: 'fix', name: 'fix', agent: 'reviewer', model: 'gpt-6.1-sol', status: 'skipped', condition: { task: 'check', operator: 'contains', value: '需要修正' }, reason: '实际前序输出未满足条件', context: ['check'] };
  const restored = hydrateJob({ id: 'job', status: 'cancelled', result: null, events: [event] });
  assert.equal(restored.result.tasks[0].status, 'skipped');
  assert.equal(restored.result.tasks[0].model, 'gpt-6.1-sol');
  assert.deepEqual(restored.result.tasks[0].condition, event.condition);
  assert.equal(restored.result.tasks[0].reason, event.reason);
});

test('context truncation and actual memory creation provenance survive authoritative final results', () => {
  const references = [{ kind: 'knowledge', id: 'document', start_line: 3, end_line: 5 }];
  const events = [{ job_id: 'job', seq: 1, type: 'stage', stage: 'context', data: { references, truncated: true } }, { job_id: 'job', seq: 2, type: 'stage', stage: 'memory_saved', data: { id: 'memory', scope: 'research' } }];
  const restored = hydrateJob({ id: 'job', status: 'complete', result: { answer: 'done' }, events });
  assert.deepEqual(restored.context_references, references);
  assert.equal(restored.context_truncated, true);
  assert.equal(restored.remembered_memory.id, 'memory');
});
