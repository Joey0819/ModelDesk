import test from 'node:test';
import assert from 'node:assert/strict';
import Module from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const compiled = buildSync({ stdin: { contents: 'export {default as TaskContextControl} from "./src/components/TaskContextControl.jsx"; export {default as HandoffReceipt} from "./src/components/HandoffReceipt.jsx";', resolveDir: root }, bundle: true, platform: 'node', format: 'cjs', jsx: 'automatic', write: false, external: ['react', 'react/jsx-runtime'] });
const filename = path.join(root, 'tests/memory-virtual.cjs');
const module = new Module(filename); module.paths = Module._nodeModulePaths(path.dirname(filename)); module._compile(compiled.outputFiles[0].text, filename);
const { TaskContextControl, HandoffReceipt } = module.exports;
const render = (component, props) => renderToStaticMarkup(React.createElement(component, props));
const memory = { task_id: 'task-a', revision: 3, sha256: 'a'.repeat(64), requests: [{ prompt: '保留原稿', job_ids: ['a'] }, { prompt: '改成蓝色', job_ids: ['b'] }], reference_documents: [{ source_job_id: 'a', names: ['story.md'], asset_ids: ['doc-a'], truncated: true }], reference_media: [{ id: 'image-a' }] };

test('memory-only media stays discoverable and a fresh task clearly excludes previous memory', () => {
  const continued = render(TaskContextControl, { value: 'continue', jobs: [{ task_context: memory }] });
  assert.match(continued, /任务记忆 · 2 条要求 · 1 份素材/);
  assert.match(continued, /1 份历史文本资料/);
  assert.match(continued, /仅保留已读取的部分/);
  assert.match(continued, /发送后会追加本轮要求/);
  const fresh = render(TaskContextControl, { value: 'fresh', jobs: [{ task_context: memory }] });
  assert.match(fresh, /查看旧任务记忆（本轮不带入）/);
  assert.match(fresh, /不会作为本轮任务记忆带入/);
  assert.doesNotMatch(fresh, /已保留底稿/);
});

test('handoff receipt shows exact ancestor lineage without inventing execution success for legacy jobs', () => {
  assert.equal(render(HandoffReceipt, { step: { status: 'complete' } }), '');
  const html = render(HandoffReceipt, { step: { status: 'error', input_context: { user_request_count: 2, context_sha256: 'b'.repeat(64), reference_asset_ids: ['image-a'], sources: [
    { step_id: 'write', member_name: '编剧', characters: 12340, output_sha256: 'c'.repeat(64), direct: false },
    { step_id: 'review', member_name: '审核', characters: 100, output_sha256: 'd'.repeat(64), direct: true },
  ] } } });
  assert.match(html, /2 条要求 · 2 份上游成果 · 1 份素材/);
  assert.match(html, /沿依赖链保留/);
  assert.match(html, /12340 字符/);
  assert.match(html, /直接前置/);
  assert.match(html, /执行状态与成果验收另行显示/);
  assert.doesNotMatch(html, /<details[^>]*open|验收通过|已经理解/);
});
