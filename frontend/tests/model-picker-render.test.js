import test from 'node:test';
import assert from 'node:assert/strict';
import Module from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const compiled = buildSync({ stdin: { contents: 'export {default} from "./src/components/ModelPicker.jsx";', resolveDir: root }, bundle: true, platform: 'node', format: 'cjs', jsx: 'automatic', write: false, external: ['react', 'react/jsx-runtime'], define: { 'import.meta.env': '{}' }, loader: { '.css': 'empty' } });
const filename = path.join(root, 'tests/model-picker-virtual.cjs');
const module = new Module(filename); module.paths = Module._nodeModulePaths(path.dirname(filename)); module._compile(compiled.outputFiles[0].text, filename);
const Picker = module.exports.default;
const catalog = [{ id: 'arbitrary-new-model', provider: 'custom', provider_label: 'Team models' }, { id: 'gpt-image-2.5' }, { id: 'hidden-fixture', testing: true }];
const render = (props = {}) => renderToStaticMarkup(React.createElement(Picker, { label: 'Ruler', open: true, value: '', catalog, onChange() {}, ...props }));

test('Ruler search exposes dynamic text choices, hides image/test options and keeps auto coordination', () => {
  const html = render({ emptyLabel: '由首位模型成员统筹' });
  assert.match(html, /搜索模型/);
  assert.match(html, /刷新列表/);
  assert.match(html, /arbitrary-new-model/);
  assert.match(html, /由首位模型成员统筹/);
  assert.doesNotMatch(html, /gpt-image-2.5|hidden-fixture/);
  assert.match(html, /<dialog/);
});
test('image role keeps exact image ID and excludes text choices', () => {
  const html = render({ capability: 'image', value: 'gpt-image-2.5' });
  assert.match(html, /gpt-image-2.5/);
  assert.doesNotMatch(html, /arbitrary-new-model/);
  assert.match(html, /尚未验证调用/);
});
test('missing and test selections remain visible instead of being silently changed', () => {
  const html = render({ value: ['missing-old-model', 'hidden-fixture'], multiple: true });
  assert.match(html, /missing-old-model/);
  assert.match(html, /暂不可用 · 保留选择/);
  assert.match(html, /hidden-fixture/);
  assert.match(html, /已选 2\/4/);
});
