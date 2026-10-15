import test from 'node:test';
import assert from 'node:assert/strict';
import Module from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const compiled = buildSync({ stdin: { contents: 'export {default as MuseDesktopPanel, MuseTaskCard} from "./src/components/MuseDesktopPanel.jsx";', resolveDir: root }, bundle: true, platform: 'node', format: 'cjs', jsx: 'automatic', write: false, external: ['react', 'react/jsx-runtime'], define: { 'import.meta.env': '{}' }, loader: { '.css': 'empty' } });
const filename = path.join(root, 'tests/muse-panel-virtual.cjs');
const module = new Module(filename); module.paths = Module._nodeModulePaths(path.dirname(filename)); module._compile(compiled.outputFiles[0].text, filename);
const { MuseDesktopPanel, MuseTaskCard } = module.exports;

test('native Muse panel waits for an actual readiness probe before enabling dispatch', () => {
  const originalStorage = globalThis.localStorage;
  globalThis.localStorage = { getItem: () => JSON.stringify({ prompt: 'Dispatch this actual task' }) };
  try {
    const html = renderToStaticMarkup(React.createElement(MuseDesktopPanel, { projectId: 'p', roomId: 'r', onUseReply() {} }));
    assert.match(html, /正在检查连接/);
    assert.match(html, /class="muse-not-ready"/);
    assert.match(html, /type="submit"[^>]*disabled/);
    assert.doesNotMatch(html, /内置 Muse|已就绪|自动将生成的视频/);
  } finally { if (originalStorage === undefined) delete globalThis.localStorage; else globalThis.localStorage = originalStorage; }
});

test('historical local generation renders as unverified without a native video or reusable Muse reply', () => {
  const html = renderToStaticMarkup(React.createElement(MuseTaskCard, { task: { id: 'old', project_id: 'p', room_id: 'r', harness_generated: true, status: 'complete', sent: true, completion_evidence: 'framed_assistant_reply', response: 'Old generated text', thread_url: 'https://muse.ai/thread/old', video_url: 'https://example.com/old.mp4' }, projectId: 'p', roomId: 'r', onUseReply() {} }));
  assert.match(html, /Old generated text/);
  assert.match(html, /历史 ModelDesk 本地生成内容/);
  assert.doesNotMatch(html, /<video|使用 Muse 回复继续讨论|打开 Muse 会话/);
});
