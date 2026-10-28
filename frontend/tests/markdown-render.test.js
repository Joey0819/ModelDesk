import test from 'node:test';
import assert from 'node:assert/strict';
import Module from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const compiled = buildSync({ stdin: { contents: 'export {Markdown} from "./src/components/WorkbenchResults.jsx";', resolveDir: root }, bundle: true, platform: 'node', format: 'cjs', jsx: 'automatic', write: false, external: ['react', 'react/jsx-runtime'], define: { 'import.meta.env': '{}' }, loader: { '.css': 'empty' } });
const filename = path.join(root, 'tests/markdown-virtual.cjs');
const module = new Module(filename); module.paths = Module._nodeModulePaths(path.dirname(filename)); module._compile(compiled.outputFiles[0].text, filename);
const render = (children) => renderToStaticMarkup(React.createElement(module.exports.Markdown, { children }));

test('delivery image inventory renders as a table with one download link per row', () => {
  const html = render('| 章节 | 图片 |\n| --- | --- |\n| 需求 | [下载 PNG](/api/media/image) |');
  assert.match(html, /<table>/);
  assert.match(html, /<th>章节<\/th>/);
  assert.match(html, /<td>需求<\/td>/);
  assert.match(html, /href="\/api\/media\/image"/);
  assert.equal((html.match(/<a /g) || []).length, 1);
  assert.doesNotMatch(html, /<img/);
});

test('formatted task lists cannot enable injected HTML or executable URLs', () => {
  const html = render('- [x] 已完成\n\n<script>alert(1)</script>\n\n[打开](javascript:alert%281%29)');
  assert.match(html, /type="checkbox"/);
  assert.match(html, /disabled/);
  assert.doesNotMatch(html, /<script|href="javascript:/);
});
