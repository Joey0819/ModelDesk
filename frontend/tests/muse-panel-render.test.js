import test from 'node:test';
import assert from 'node:assert/strict';
import Module from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const compiled = buildSync({ stdin: { contents: 'export {default as MuseDesktopPanel, MuseTaskCard, MusePermissionRecovery} from "./src/components/MuseDesktopPanel.jsx";', resolveDir: root }, bundle: true, platform: 'node', format: 'cjs', jsx: 'automatic', write: false, external: ['react', 'react/jsx-runtime'], define: { 'import.meta.env': '{}' }, loader: { '.css': 'empty' } });
const filename = path.join(root, 'tests/muse-panel-virtual.cjs');
const module = new Module(filename); module.paths = Module._nodeModulePaths(path.dirname(filename)); module._compile(compiled.outputFiles[0].text, filename);
const { MuseDesktopPanel, MuseTaskCard, MusePermissionRecovery } = module.exports;

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

test('skill card distinguishes prepared guidance from confirmed native dispatch', () => {
  const task = { id: 'skill', status: 'queued', sent: false, skill_application: { mode: 'adapted_guidance', prepared_by: 'coordinator', skills: [{ id: 'director', name: 'Director', sha256: '1234567890abcdef' }], guidance: 'Keep the approved characters.' } };
  const prepared = renderToStaticMarkup(React.createElement(MuseTaskCard, { task }));
  assert.match(prepared, /已准备，尚未确认送达/);
  assert.match(prepared, /未安装原生插件/);
  assert.match(prepared, /Keep the approved characters/);
  const sent = renderToStaticMarkup(React.createElement(MuseTaskCard, { task: { ...task, sent: true } }));
  assert.match(sent, /已随任务发送/);
});


test('Muse permission recovery gives fixed native controls and browser path guidance without claiming readiness', () => {
  const probe = { installed: true, permission: false, error: 'accessibility_permission_required', bridge_helper_path: '/Users/example/project/desktop/bin/muse-bridge' };
  const render = (props) => renderToStaticMarkup(React.createElement(MusePermissionRecovery, { probe, remote: false, ...props }));
  const native = render({ bridge: { openAccessibilitySettings() {}, revealMuseBridge() {} } });
  assert.match(native, /打开辅助功能设置/);
  assert.match(native, /显示需授权的桥接程序/);
  assert.match(native, /复制桥接路径/);
  assert.match(native, /Users\/example\/project\/desktop\/bin\/muse-bridge/);
  assert.doesNotMatch(native, /已授权|已就绪/);
  const browser = render({ bridge: null });
  assert.match(browser, /系统设置/);
  assert.match(browser, /Users\/example/);
  assert.doesNotMatch(browser, /打开辅助功能设置|显示需授权的桥接程序/);
  const remote = render({ bridge: null, remote: true });
  assert.match(remote, /运行 ModelDesk 的 Mac/);
  assert.doesNotMatch(remote, /Users\/example/);
  assert.equal(render({ probe: { ...probe, permission: true } }), '');
});
