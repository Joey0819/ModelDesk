import test from 'node:test';
import assert from 'node:assert/strict';
import Module from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { autonomyActive, autonomyCounts, autonomyUnread, checkAutonomyTarget, scopedAutonomyTasks } from '../src/autonomy-state.js';
import { groupMessageRequest, readGroupDraft } from '../src/group-state.js';
import { applyEvent, hydrateJob } from '../src/workbench-state.js';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const compiled = buildSync({ stdin: { contents: 'export {default as AutonomyTaskCenter, AutonomyTaskList, BackgroundDelegateSetting} from "./src/components/AutonomyTaskCenter.jsx"; export {autonomyApi} from "./src/autonomy-api.js";', resolveDir: root }, bundle: true, platform: 'node', format: 'cjs', jsx: 'automatic', write: false, external: ['react', 'react/jsx-runtime'], define: { 'import.meta.env': '{}' }, loader: { '.css': 'empty' } });
const filename = path.join(root, 'tests/autonomy-virtual.cjs');
const module = new Module(filename); module.paths = Module._nodeModulePaths(path.dirname(filename)); module._compile(compiled.outputFiles[0].text, filename);
const { AutonomyTaskCenter, AutonomyTaskList, BackgroundDelegateSetting, autonomyApi } = module.exports;
const task = { id: 'job-a', project_id: 'project-a', room_id: 'room-a', title: 'Write a complete report', state: 'running', attempts: 0, max_attempts: 2, read: false, notification_id: null, updated_at: '2026-11-02T01:00:00Z' };
const renderList = (props) => renderToStaticMarkup(React.createElement(AutonomyTaskList, { onRefresh() {}, onOpenTask() {}, onRead() {}, onPause() {}, ...props }));

test('background inbox consumes only authoritative outcomes in the exact project scope', () => {
  const saved = { ...task, id: 'job-complete', state: 'complete', read: false, notification_id: 'delivered-1' };
  const tasks = scopedAutonomyTasks({ tasks: [task, saved, { ...saved, id: 'foreign', project_id: 'project-b' }, { ...task, id: 'generic', status: 'complete', state: undefined }, { ...task, id: 'standalone', project_id: null }] }, 'project-a');
  assert.deepEqual(tasks.map((item) => item.id), ['job-complete', 'job-a']);
  assert.equal(tasks[0].state, 'complete');
  assert.deepEqual(autonomyCounts(tasks), { active: 1, attention: 0, unread: 1 });
  assert.equal(autonomyUnread(task), false);
  assert.equal(autonomyUnread(saved), true);
  assert.equal(autonomyUnread({ ...saved, read: true }), false);
  assert.deepEqual(scopedAutonomyTasks({ tasks: [{ ...task, project_id: null }, saved] }, null).map((item) => item.project_id), [null]);
  assert.deepEqual(scopedAutonomyTasks(null, 'project-a'), []);
});

test('background task requests keep exact project scope and report failures instead of replacing them with empty success', async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options) => { calls.push({ url, method: options.method }); return { ok: true, json: async () => ({ tasks: [task] }) }; };
  try {
    await autonomyApi.list('project-a'); await autonomyApi.list(null); await autonomyApi.read('job-a', 'project-a'); await autonomyApi.pause('job-a', 'project-a');
    assert.deepEqual(calls.map(({ url, method }) => [new URL(url).pathname + new URL(url).search, method]), [
      ['/api/autonomy/tasks?project_id=project-a', 'GET'], ['/api/autonomy/tasks', 'GET'], ['/api/autonomy/tasks/job-a/read?project_id=project-a', 'POST'], ['/api/autonomy/tasks/job-a/pause?project_id=project-a', 'POST'],
    ]);
    globalThis.fetch = async () => ({ ok: false, status: 403, json: async () => ({ detail: '此项目未授权' }) });
    await assert.rejects(autonomyApi.list('project-a'), /此项目未授权/);
    globalThis.fetch = async () => { throw new Error('connection refused'); };
    await assert.rejects(autonomyApi.list('project-a'), /无法连接/);
  } finally { globalThis.fetch = originalFetch; }
});

test('persisted autonomy events survive replay without manufacturing a delivered result', () => {
  const job = { id: task.id, status: 'complete', result: { delivery: { status: 'partial', content: 'Actual partial report' } }, events: [] };
  const event = { type: 'autonomy', job_id: task.id, seq: 1, autonomy: { state: 'retry_wait', attempts: 1 } };
  const next = applyEvent(job, event);
  assert.equal(next.autonomy.state, 'retry_wait'); assert.equal(next.result.delivery.status, 'partial');
  assert.equal(applyEvent(next, event).events.length, 1);
  const restored = hydrateJob(next);
  assert.equal(restored.autonomy.state, 'retry_wait'); assert.equal(restored.result.delivery.content, 'Actual partial report');
});

test('actual inbox renderer follows running, retry wait, delivered, attention and paused snapshots', () => {
  let html = renderList({ tasks: [task] });
  assert.match(html, /正在执行/); assert.match(html, /暂停托管/); assert.doesNotMatch(html, /已交付|查看成果|标为已读/);
  html = renderList({ tasks: [{ ...task, state: 'retry_wait', reason: '等待网关恢复', attempts: 1, next_action_at: '2026-11-02T01:01:00Z' }] });
  assert.match(html, /等待安全续跑/); assert.match(html, /等待网关恢复/); assert.match(html, /下次检查/); assert.match(html, /安全续跑 1\/2/);
  html = renderList({ tasks: [{ ...task, state: 'complete', notification_id: 'done-1' }] });
  assert.match(html, /已交付/); assert.match(html, /查看成果/); assert.match(html, /标为已读/); assert.doesNotMatch(html, /暂停托管/);
  html = renderList({ tasks: [{ ...task, state: 'needs_attention', reason: 'Muse 送达未确认，不会重复派发', notification_id: 'attention-1' }] });
  assert.match(html, /需要你处理/); assert.match(html, /送达未确认/); assert.doesNotMatch(html, /查看成果|已交付/);
  html = renderList({ tasks: [{ ...task, state: 'paused' }] });
  assert.match(html, /托管已暂停/); assert.match(html, /已启动的执行不会因此取消/); assert.doesNotMatch(html, /暂停托管<\/button>/);
  assert.equal(autonomyActive({ ...task, state: 'cancelled' }), false);
});

test('actual inbox renderer separates no tasks, loading and an offline stale snapshot', () => {
  const empty = renderList({ tasks: [] });
  assert.match(empty, /这个项目还没有后台任务/); assert.doesNotMatch(empty, /已交付/);
  const loading = renderList({ loading: true });
  assert.match(loading, /正在读取服务端保存的任务/); assert.doesNotMatch(loading, /这个项目还没有后台任务/);
  const offline = renderList({ error: '后台服务离线' });
  assert.match(offline, /role="alert"/); assert.match(offline, /重新连接/); assert.doesNotMatch(offline, /这个项目还没有后台任务/);
  const stale = renderList({ tasks: [task], error: '后台服务离线' });
  assert.match(stale, /上次同步的状态，尚未确认最新进度/); assert.match(stale, /Write a complete report/); assert.doesNotMatch(stale, /已交付/);
  const center = renderToStaticMarkup(React.createElement(AutonomyTaskCenter, { projectId: 'project-a', projectName: 'A', onOpenTask() {} }));
  assert.match(center, /后台任务/); assert.match(center, /后台任务中心/); assert.match(center, /这台电脑休眠、关机或服务停止时无法继续执行/);
});

// A macOS host reports itself through status.platform and the autonomy copy
// follows it; the neutral branch stays covered above. Both hosts are pinned.
test('autonomy host copy follows the platform report on macOS', () => {
  const macStatus = { platform: { system: 'Darwin', is_macos: true, is_linux: false, is_windows: false, has_native_muse_bridge: true, machine_label: '\u8fd9\u53f0 Mac', machine_short: 'Mac' } };
  const center = renderToStaticMarkup(React.createElement(AutonomyTaskCenter, { projectId: 'project-a', projectName: 'A', status: macStatus, onOpenTask() {} }));
  assert.match(center, /Mac\u4f11\u7720\u3001\u5173\u673a\u6216\u670d\u52a1\u505c\u6b62\u65f6\u65e0\u6cd5\u7ee7\u7eed\u6267\u884c/);
  assert.doesNotMatch(center, /\u8fd9\u53f0\u7535\u8111\u4f11\u7720/);
  const setting = renderToStaticMarkup(React.createElement(BackgroundDelegateSetting, { checked: true, hostShort: 'Mac', onChange() {} }));
  assert.match(setting, /Mac\u9700\u8981\u4fdd\u6301\u5524\u9192\u5e76\u8054\u7f51/);
  assert.doesNotMatch(setting, /\u8fd9\u53f0\u7535\u8111\u9700\u8981\u4fdd\u6301\u5524\u9192/);
});

test('notification targets cannot open another project, room, or substituted job', () => {
  const job = { id: task.id, project_id: task.project_id, group_room_id: task.room_id };
  assert.equal(checkAutonomyTarget(task, job), job);
  for (const changed of [{ ...job, id: 'other' }, { ...job, project_id: 'other' }, { ...job, group_room_id: 'other' }]) assert.throws(() => checkAutonomyTarget(task, changed), /不匹配/);
  assert.throws(() => checkAutonomyTarget(undefined, job), /不匹配/);
});

test('new automatic tasks delegate by default while discussion and explicit opt out cannot start background continuation', () => {
  assert.equal(readGroupDraft({ getItem: () => null }, 'p', 'r').backgroundMode, true);
  assert.equal(readGroupDraft({ getItem: () => '{"backgroundMode":false}' }, 'p', 'r').backgroundMode, false);
  assert.equal(groupMessageRequest({ prompt: 'Deliver report' }).background_mode, true);
  assert.equal(groupMessageRequest({ prompt: 'Deliver report', backgroundMode: false }).background_mode, false);
  assert.equal(groupMessageRequest({ prompt: 'Discuss', deliveryMode: 'discussion', backgroundMode: true }).background_mode, false);
  assert.equal(groupMessageRequest({ prompt: 'Discuss', deliveryMode: 'discussion', context: { background_mode: true } }).background_mode, false);
  const setting = renderToStaticMarkup(React.createElement(BackgroundDelegateSetting, { checked: true, onChange() {} }));
  assert.match(setting, /type="checkbox" checked=""/); assert.match(setting, /后台托管/); assert.match(setting, /这台电脑需要保持唤醒并联网/); assert.match(setting, /不重复派发/);
});
