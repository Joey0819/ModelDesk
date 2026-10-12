import test from 'node:test';
import assert from 'node:assert/strict';
import Module from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const compiled = buildSync({ entryPoints: [path.join(root, 'src/components/ConversationActions.jsx')], bundle: true, platform: 'node', format: 'cjs', jsx: 'automatic', write: false, external: ['react', 'react/jsx-runtime'], loader: { '.css': 'empty' } });
const filename = path.join(root, 'tests/copy-render-virtual.cjs');
const module = new Module(filename);
module.paths = Module._nodeModulePaths(path.dirname(filename));
module._compile(compiled.outputFiles[0].text, filename);
const { CopyTextButton } = module.exports;

test('the actual copy component renders safely before a streaming model has returned any text', () => {
  for (const text of [undefined, null, '']) {
    const html = renderToStaticMarkup(React.createElement(CopyTextButton, { text, label: '复制回答' }));
    assert.match(html, /disabled/);
    assert.match(html, /复制回答/);
    assert.doesNotMatch(html, /已复制/);
  }
  const ready = renderToStaticMarkup(React.createElement(CopyTextButton, { text: '真实回答' }));
  assert.doesNotMatch(ready, /disabled/);
});
