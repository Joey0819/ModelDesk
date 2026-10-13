import test from 'node:test';
import assert from 'node:assert/strict';
import Module from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const compiled = buildSync({ stdin: { contents: 'export {ApprovalCard,TaskCard} from "./src/components/GroupAgentTasks.jsx";', resolveDir: root }, bundle: true, platform: 'node', format: 'cjs', jsx: 'automatic', write: false, external: ['react', 'react/jsx-runtime'], define: { 'import.meta.env': '{}' }, loader: { '.css': 'empty' } });
const filename = path.join(root, 'tests/group-task-virtual.cjs');
const module = new Module(filename); module.paths = Module._nodeModulePaths(path.dirname(filename)); module._compile(compiled.outputFiles[0].text, filename);
const { ApprovalCard, TaskCard } = module.exports;

test('actual approval card exposes local destination and pinned evidence without pretending to push GitHub', () => {
  const html = renderToStaticMarkup(React.createElement(ApprovalCard, { card: { id: 'approval', status: 'pending', title: 'Fix the export', target_branch: 'main', base_sha: 'base123', patch_sha256: 'patch123', changed_files: [{ path: 'a.py' }], review_output: 'Tests were not available.' }, onDecision() {} }));
  for (const text of ['批准并合入本地项目', 'base123', 'patch123', 'main', 'Tests were not available.', '推送 GitHub 需另外操作']) assert.ok(html.includes(text));
  assert.ok(!html.includes('PASS'));
});
test('failed independent task renders actual failure and permits neither review nor approval', () => {
  const html = renderToStaticMarkup(React.createElement(TaskCard, { task: { id: 'task', member: { name: '工程师' }, engine: 'codex', kind: 'code', status: 'error', prompt: 'Fix the export', error: 'Sandbox unavailable', output: 'Partial output', changed_files: [], events: [] }, tasks: [], approvals: [], onOpenArtifacts() {} }));
  assert.match(html, /Sandbox unavailable/);
  assert.match(html, /Partial output/);
  assert.match(html, /由 Codex 执行/);
  assert.doesNotMatch(html, /提交合入审批|请另一位成员审阅/);
});
