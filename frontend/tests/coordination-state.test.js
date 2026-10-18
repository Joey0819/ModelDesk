import test from 'node:test';
import assert from 'node:assert/strict';
import Module from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { coordinationState } from '../src/coordination-state.js';

function job(changes = {}) {
  return { id: 'owned-run', group_room_id: 'room', status: 'complete', result: { delivery: {
    workflow: { schema_version: 1, run_id: 'owned-run', revision: 3, resume_count: 0, elapsed_seconds: 50 },
    status: 'partial', steps: [{ id: 'a', member_name: '作者', expected_output: '文章', status: 'complete', depends_on: [], attempts: [{}] }],
    task_spec: { requirements: [{ id: 'body', description: '完整文章' }] },
    validation: { status: 'needs_revision', mechanical_checks: [{ requirement_id: 'body', met: true }], review: {
      independent_model: true, checks: [{ requirement_id: 'body', met: false, reason: '缺少结尾' }],
    } }, versions: [{ number: 1, content: '旧版本', reason: '初稿' }, { number: 2, content: '新版本', reason: '修订' }],
    ...changes,
  } } };
}

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const compiled = buildSync({ stdin: { contents: 'export {default as Progress} from "./src/components/CoordinationProgress.jsx";', resolveDir: root }, bundle: true, platform: 'node', format: 'cjs', jsx: 'automatic', write: false, external: ['react', 'react/jsx-runtime'], define: { 'import.meta.env': '{}' }, loader: { '.css': 'empty' } });
const filename = path.join(root, 'tests/coordination-virtual.cjs');
const module = new Module(filename); module.paths = Module._nodeModulePaths(path.dirname(filename)); module._compile(compiled.outputFiles[0].text, filename);

test('only scoped coordination jobs get continuation controls', () => {
  assert.equal(coordinationState({ id: 'legacy', result: {} }), null);
  const foreign = job(); foreign.id = 'different';
  assert.equal(coordinationState(foreign), null);
  assert.equal(coordinationState(job()).canContinue, true);
  assert.equal(coordinationState(job({ status: 'complete' })).canContinue, false);
  const exhausted = job(); exhausted.result.delivery.workflow.resume_count = 2;
  assert.equal(coordinationState(exhausted).canContinue, false);
  exhausted.result.delivery.workflow.resume_count = 0; exhausted.result.delivery.workflow.elapsed_seconds = 1200;
  assert.equal(coordinationState(exhausted).canContinue, false);
  const running = job(); running.status = 'running';
  assert.equal(coordinationState(running).canContinue, false);
});

test('a review cannot visually override a failed real-output check', () => {
  const value = job();
  value.result.delivery.validation.review.checks[0].met = true;
  value.result.delivery.validation.mechanical_checks[0].met = false;
  assert.equal(coordinationState(value).requirements[0].state, 'missing');
  value.result.delivery.validation.mechanical_checks[0].met = true;
  assert.equal(coordinationState(value).requirements[0].state, 'met');
  value.result.delivery.validation.review.checks = [];
  assert.equal(coordinationState(value).requirements[0].state, 'pending');
});

test('progress renders requirements, lineage, review scope and a clear continue action', () => {
  const html = renderToStaticMarkup(React.createElement(module.exports.Progress, { job: job() }));
  for (const text of ['任务要求与交付检查', '完整文章', '缺少结尾', '查看分工与依赖', '成果版本', '旧版本', '新版本', '继续未完成的工作', '不会重新派发']) assert.ok(html.includes(text), text);
  assert.equal(renderToStaticMarkup(React.createElement(module.exports.Progress, { job: { id: 'old' } })), '');
});
