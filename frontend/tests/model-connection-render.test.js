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
const compiled = buildSync({ stdin: { contents: 'export {default as Connections,ConnectionForm,ConnectionRow} from "./src/components/ModelConnections.jsx";', resolveDir: root }, bundle: true, platform: 'node', format: 'cjs', jsx: 'automatic', write: false, external: ['react', 'react/jsx-runtime'], define: { 'import.meta.env': '{}' }, loader: { '.css': 'empty' } });
const filename = path.join(root, 'tests/connection-virtual.cjs');
const module = new Module(filename); module.paths = Module._nodeModulePaths(path.dirname(filename)); module._compile(compiled.outputFiles[0].text, filename);
const { Connections, ConnectionForm, ConnectionRow } = module.exports;
test('connection form exposes password-only entry and never injects a stored API key', () => {
  const draft = connectionDraft({ id: 'one', provider: 'xai', name: 'Grok', base_url: 'https://api.x.ai/v1', has_api_key: true, api_key: 'hidden-private-key' });
  const html = renderToStaticMarkup(React.createElement(ConnectionForm, { draft, onSave() {}, onChange() {}, onCancel() {} }));
  assert.match(html, /type="password"/); assert.match(html, /留空保留/);
  assert.doesNotMatch(html, /hidden-private-key|localStorage/);
});
test('Gemini is an explicit connection action with a fixed Google address and AI Studio link', () => {
  const view = renderToStaticMarkup(React.createElement(Connections, { onRefresh() {} }));
  assert.match(view, /添加 Gemini API/);
  const draft = connectionDraft(null, 'gemini');
  const form = renderToStaticMarkup(React.createElement(ConnectionForm, { draft, onSave() {}, onChange() {}, onCancel() {} }));
  assert.match(form, /<option value="gemini" selected="">Google Gemini API<\/option>/);
  assert.match(form, /<input [^>]*aria-label="API 地址"[^>]*readOnly=""[^>]*value="https:\/\/generativelanguage\.googleapis\.com\/v1beta\/openai"/i);
  assert.match(form, /href="https:\/\/aistudio\.google\.com\/apikey"[^>]*>打开 Google AI Studio<\/a>/);
  assert.doesNotMatch(form, /打开 xAI 控制台/);
});
test('saved but untested connection row makes test and discovery explicit', () => {
  const html = renderToStaticMarkup(React.createElement(ConnectionRow, { connection: { id: 'one', name: 'Grok', provider: 'xai', enabled: true, base_url: 'https://api.x.ai/v1', models: [{ id: 'm', label: 'Grok reviewer' }], has_api_key: true } }));
  assert.match(html, /尚未测试调用/); assert.match(html, /读取模型列表/); assert.match(html, /测试模型调用/);
  assert.doesNotMatch(html, /调用已验证/);
});
