import test from 'node:test';
import assert from 'node:assert/strict';
import Module from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { fetchContextSource, initialSources, missingSourceSelections } from '../src/context-source-state.js';

test('resource reads filter archived/foreign entries and failure is not an empty successful library', async () => {
  const selected = ['existing', 'missing'];
  const api = { list: async (kind, archived, scope) => {
    assert.equal(kind, 'memories'); assert.equal(archived, false); assert.equal(scope.project_id, 'project-a');
    return [{ id: 'existing', project_id: 'project-a' }, { id: 'global' }, { id: 'foreign', project_id: 'project-b' }, { id: 'archived', archived: true }];
  } };
  const ready = await fetchContextSource(api, 'memories', 'project-a');
  assert.deepEqual(ready.items.map((item) => item.id), ['existing', 'global']);
  assert.equal(ready.status, 'ready');
  assert.deepEqual(missingSourceSelections(selected, ready).map((item) => item.id), ['missing']);
  const failed = await fetchContextSource({ list: async () => { throw new Error('Service offline'); } }, 'memories', 'project-b');
  assert.deepEqual(failed, { status: 'error', items: [], error: 'Service offline' });
  assert.deepEqual(missingSourceSelections(selected, failed).map((item) => item.id), selected);
  assert.match(missingSourceSelections(selected, failed)[0].reason, /暂时无法确认/);
  assert.match(missingSourceSelections(selected, ready)[0].reason, /已归档/);
  assert.deepEqual(selected, ['existing', 'missing']);
  assert.equal(initialSources().memories.status, 'loading');
});

test('global resource reads request only global scope and retries replace the failed result', async () => {
  let fail = true;
  const api = { list: async (kind, archived, scope) => {
    assert.deepEqual(scope, { scope: 'global' });
    if (fail) throw new Error('offline');
    return { items: [{ id: 'global' }, { id: 'foreign', project_id: 'project-a' }] };
  } };
  assert.equal((await fetchContextSource(api, 'mcp', null)).status, 'error');
  fail = false;
  assert.deepEqual((await fetchContextSource(api, 'mcp', null)).items, [{ id: 'global' }]);
});

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const compiled = buildSync({ stdin: { contents: 'export {default as ContextAttachments} from "./src/components/ContextAttachments.jsx"; export {ContextSourceRows} from "./src/components/ContextSourceRows.jsx";', resolveDir: root }, bundle: true, platform: 'node', format: 'cjs', jsx: 'automatic', write: false, external: ['react', 'react/jsx-runtime'], define: { 'import.meta.env': '{}' }, loader: { '.css': 'empty' } });
const filename = path.join(root, 'tests/context-source-virtual.cjs');
const module = new Module(filename); module.paths = Module._nodeModulePaths(path.dirname(filename)); module._compile(compiled.outputFiles[0].text, filename);
const { ContextAttachments, ContextSourceRows } = module.exports;

test('project switch remounts the source owner so pending reads and previews cannot leak into new project', () => {
  const a = ContextAttachments({ value: { project_id: 'a' } });
  const b = ContextAttachments({ value: { project_id: 'b' } });
  const global = ContextAttachments({ value: { project_id: null } });
  assert.notEqual(a.key, b.key); assert.notEqual(b.key, global.key);
  assert.equal(ContextAttachments({ value: { project_id: 'a', memory_ids: ['changed'] } }).key, a.key);
});

test('source picker exposes retry, missing-selection removal, and escaped preview rather than silently dropping selections', () => {
  const failed = renderToStaticMarkup(React.createElement(ContextSourceRows, { kind: 'memories', state: { status: 'error', items: [], error: 'Connection failed' }, selected: ['still-selected'], toggle() {}, retry() {} }));
  assert.match(failed, /重试持久记忆/); assert.match(failed, /still-selected/); assert.match(failed, /移除选择/);
  assert.doesNotMatch(failed, /没有可选择/);
  const ready = renderToStaticMarkup(React.createElement(ContextSourceRows, { kind: 'memories', state: { status: 'ready', items: [{ id: 'one', name: 'Memory', content: '<script>untrusted()</script>', scope: 'research', source_job_id: 'job-one' }] }, selected: ['one'], toggle() {}, retry() {} }));
  assert.match(ready, /查看内容与来源/); assert.match(ready, /job-one/); assert.match(ready, /checked/);
  assert.match(ready, /&lt;script&gt;/); assert.doesNotMatch(ready, /<script>/);
});
