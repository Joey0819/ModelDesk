import test from 'node:test';
import assert from 'node:assert/strict';
import Module from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { connectionDraft } from '../src/model-connection-state.js';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const compiled = buildSync({ stdin: { contents: 'export {ConnectionForm,ConnectionRow} from "./src/components/AgentSettings.jsx";', resolveDir: root }, bundle: true, platform: 'node', format: 'cjs', jsx: 'automatic', write: false, external: ['react', 'react/jsx-runtime'], define: { 'import.meta.env': '{}' }, loader: { '.css': 'empty' } });
const filename = path.join(root, 'tests/connection-virtual.cjs');
const module = new Module(filename); module.paths = Module._nodeModulePaths(path.dirname(filename)); module._compile(compiled.outputFiles[0].text, filename);
const { ConnectionForm, ConnectionRow } = module.exports;
test('connection form exposes password-only entry and never injects a stored API key', () => {
  const draft = connectionDraft({ id: 'one', provider: 'xai', name: 'Grok', base_url: 'https://api.x.ai/v1', has_api_key: true, api_key: 'hidden-private-key' });
  const html = renderToStaticMarkup(React.createElement(ConnectionForm, { draft, onSave() {}, onChange() {}, onCancel() {} }));
  assert.match(html, /type="password"/); assert.match(html, /留空保留/);
  assert.doesNotMatch(html, /hidden-private-key|localStorage/);
});
test('saved but untested connection row makes test and discovery explicit', () => {
  const html = renderToStaticMarkup(React.createElement(ConnectionRow, { connection: { id: 'one', name: 'Grok', provider: 'xai', enabled: true, base_url: 'https://api.x.ai/v1', models: [{ id: 'm', label: 'Grok reviewer' }], has_api_key: true } }));
  assert.match(html, /尚未测试调用/); assert.match(html, /读取模型列表/); assert.match(html, /测试模型调用/);
  assert.doesNotMatch(html, /调用已验证/);
});
