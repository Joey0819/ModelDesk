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
