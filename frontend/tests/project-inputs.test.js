import test from 'node:test';
import assert from 'node:assert/strict';
import { prepareFiles, attachmentIssues, addAssetsWithinBudget } from '../src/attachment-files.js';
import { projectDraftKey, projectSessionKey, scopedResources } from '../src/project-context.js';
import { sessionFor, sourceSelection, restoredSession } from '../src/workbench-sessions.js';
import Module from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

test('folder preparation excludes credential/cache/escaping paths and reports byte limits', () => {
  const files = [
    { name: 'safe.md', size: 6, webkitRelativePath: 'folder/safe.md' },
    { name: '.env', size: 4, webkitRelativePath: 'folder/.env' },
    { name: 'cache.txt', size: 3, webkitRelativePath: 'folder/node_modules/cache.txt' },
    { name: 'escape.txt', size: 3, webkitRelativePath: '../escape.txt' },
    { name: 'huge.png', size: 11000000, type: 'image/png' },
  ];
  const prepared = prepareFiles(files);
  assert.equal(prepared.entries.length, 1);
  assert.equal(prepared.entries[0].relative_path, 'folder/safe.md');
  assert.equal(prepared.omitted.length, 4);
  assert.equal(prepared.bytes, 6);
});

test('current images, shared sources, removed IDs and unsupported material are explicitly bounded', () => {
  const assets = Array.from({ length: 9 }, (_, i) => ({ id: `image-${i}`, kind: 'image', size: 100, status: 'ready', shared: i === 8 }));
  assert.equal(attachmentIssues(assets, assets.slice(0, 8).map(a => a.id), false).length, 0);
  assert(attachmentIssues(assets, assets.slice(0, 8).map(a => a.id), true).length);
  assert(attachmentIssues(assets, ['removed'], false).length);
  const selected = addAssetsWithinBudget(assets, [], assets, false);
  assert.equal(selected.ids.length, 8);
  assert.equal(selected.omitted.length, 1);
  assert(attachmentIssues([{ id: 'unsupported', kind: 'unsupported', status: 'unsupported' }], ['unsupported'], false).length);
});

test('projects partition saved sessions, drafts and resource lists while global resources remain visible', () => {
  assert.notEqual(projectDraftKey('a', 'same'), projectDraftKey('b', 'same'));
  const sessions = {
    [projectSessionKey('a', 'council')]: { job_id: 'a-job', context: { project_id: 'a', attachment_ids: ['a-image'] }, preferences: { models: ['a-model'] } },
    [projectSessionKey('b', 'council')]: { job_id: 'b-job', context: { project_id: 'b', attachment_ids: ['b-image'] }, preferences: { models: ['b-model'] } },
  };
  assert.deepEqual(sessionFor(sessions, 'council', 'a').context.attachment_ids, ['a-image']);
  assert.equal(sessionFor(sessions, 'council', 'empty').job_id, undefined);
  assert.deepEqual(restoredSession(sessions, 'council', { id: 'a-job', project_id: 'a' }).preferences.models, ['a-model']);
  const resources = [{ id: 'global' }, { id: 'a', project_id: 'a' }, { id: 'b', project_id: 'b' }];
  assert.deepEqual(scopedResources(resources, 'a').map(item => item.id), ['global', 'a']);
  assert.deepEqual(scopedResources(resources, 'a', 'project').map(item => item.id), ['a']);
  const selection = sourceSelection({ project_id: 'a', attachment_ids: ['a-image', 'a-image'], include_project_assets: false, _attachments: [{ path: 'private' }] });
  assert.deepEqual(selection.attachment_ids, ['a-image']);
  assert.equal(selection.include_project_assets, false);
  assert.equal('_attachments' in selection, false);
});

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const compiled = buildSync({ stdin: { contents: 'export {default as ProjectHub} from "./src/components/ProjectHub.jsx"; export {default as AttachmentComposer} from "./src/components/AttachmentComposer.jsx"; export {ProjectContext} from "./src/project-context.js";', resolveDir: root }, bundle: true, platform: 'node', format: 'cjs', jsx: 'automatic', write: false, external: ['react', 'react/jsx-runtime'], define: { 'import.meta.env': '{}' }, loader: { '.css': 'empty' } });
const filename = path.join(root, 'tests/project-inputs-virtual.cjs');
const module = new Module(filename); module.paths = Module._nodeModulePaths(path.dirname(filename));
module._compile(compiled.outputFiles[0].text, filename);
const { ProjectHub, AttachmentComposer, ProjectContext } = module.exports;
const hub = { projects: [{ id: 'a', name: '图片项目', managed: true }], activeProjectId: 'a', activeProject: { name: '图片项目' }, assets: [{ id: 'img', project_id: 'a', name: 'image.png', kind: 'image', status: 'ready', size: 100, preview_url: '/api/workbench/projects/a/assets/img/content', shared: false }], loading: false, createOpen: false, managerOpen: false };

test('actual shared project and composer components expose project creation and immutable image selection', () => {
  const previousWindow = globalThis.window; globalThis.window = {};
  try {
    const html = renderToStaticMarkup(React.createElement(ProjectContext.Provider, { value: hub }, React.createElement(React.Fragment, null,
      React.createElement(ProjectHub, {}), React.createElement(AttachmentComposer, { attachmentIds: ['img'] }))));
    assert.match(html, /选择共享项目/); assert.match(html, /新建项目/); assert.match(html, /项目资料/);
    assert.match(html, /添加图片、文件或文件夹/); assert.match(html, /image.png/);
    assert.match(html, /移除本轮附件 image.png/); assert.match(html, /type="file"/);
    assert.match(html, /webkitdirectory/);
  } finally { globalThis.window = previousWindow; }
});

test('a managed project can be created without requiring a code directory in the actual form', () => {
  const previousWindow = globalThis.window; globalThis.window = {};
  try {
    const html = renderToStaticMarkup(React.createElement(ProjectContext.Provider, { value: { ...hub, activeProjectId: null, assets: [], createOpen: true } }, React.createElement(ProjectHub, {})));
    assert.match(html, /不需要代码目录/); assert.match(html, /项目名称/); assert.match(html, /绑定本地目录（可选/);
    assert.match(html, /创建并继续/);
  } finally { globalThis.window = previousWindow; }
});
