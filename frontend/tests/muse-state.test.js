import test from 'node:test';
import assert from 'node:assert/strict';
import Module from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { canUseMuseReply, mergeMuseTasks, museDraftKey, museReadiness, readMuseDraft, safeMuseThreadUrl, scopedMuseTasks } from '../src/muse-state.js';
import { groupMessageRequest } from '../src/group-state.js';

const task = { id: 'muse-task', project_id: 'project-a', room_id: 'room-a', status: 'complete', sent: true, monitoring: false, completion_evidence: 'framed_assistant_reply', response: 'Actual native reply', prompt: 'Research our proposal', thread_url: 'https://muse.ai/thread/real-thread', created_at: '2026-10-13T01:00:00Z', updated_at: '2026-10-13T01:02:00Z' };

test('installation alone never marks Muse ready; native permission and composer are required', () => {
  assert.equal(museReadiness({ installed: true }).ready, false);
  assert.equal(museReadiness({ installed: true, permission: true, ok: true, composer_ready: false }).ready, false);
  assert.equal(museReadiness({ installed: true, permission: false, ok: true, composer_ready: true }).ready, false);
  assert.equal(museReadiness({ installed: true, permission: true, ok: true, composer_ready: true }).ready, true);
  assert.equal(museReadiness({ installed: null, error: 'Cannot connect' }).label, '连接尚未确认');
});

test('only a framed, sent, completed native reply from this exact project and room can be selected', () => {
  assert.equal(canUseMuseReply(task, 'project-a', 'room-a'), true);
  for (const patch of [{ status: 'waiting_user' }, { status: 'uncertain' }, { sent: null }, { completion_evidence: null }, { response: '' }, { room_id: 'other' }, { project_id: 'other' }]) assert.equal(canUseMuseReply({ ...task, ...patch }, 'project-a', 'room-a'), false);
});

test('Muse drafts and task polling cannot cross room or project scopes or overwrite newer receipt', () => {
  const storage = { getItem: (key) => key === museDraftKey('project-a', 'room-a') ? JSON.stringify({ prompt: 'private', taskId: task.id }) : null };
  assert.equal(readMuseDraft(storage, 'project-a', 'room-a').prompt, 'private');
  assert.equal(readMuseDraft(storage, 'project-a', 'room-b').prompt, '');
  assert.equal(readMuseDraft(storage, 'project-b', 'room-a').taskId, null);
  assert.deepEqual(scopedMuseTasks([task, { ...task, id: 'foreign', room_id: 'room-b' }], 'project-a', 'room-a'), [task]);
  const merged = mergeMuseTasks([task], [{ ...task, status: 'running', updated_at: '2026-10-13T01:01:00Z' }], 'project-a', 'room-a');
  assert.equal(merged[0].status, 'complete');
});

test('group handoff sends the selected task ID without copying native response or inventing recipients', () => {
  const body = groupMessageRequest({ prompt: 'Review the reply', recipientIds: ['reviewer'], context: { muse_task_id: task.id, muse_task_label: task.prompt, response: task.response } });
  assert.equal(body.muse_task_id, task.id); assert.deepEqual(body.recipient_member_ids, ['reviewer']);
  assert.equal(body.prompt, 'Review the reply'); assert.equal('response' in body, false); assert.equal('muse_task_label' in body, false);
  assert.equal(groupMessageRequest({ prompt: 'Without reply', recipientIds: [], context: {} }).muse_task_id, undefined);
});

test('only canonical Muse HTTPS thread links are exposed', () => {
  assert.equal(safeMuseThreadUrl(task.thread_url), task.thread_url);
  for (const url of ['javascript:alert(1)', 'https://muse.ai.evil.test/thread/a', 'https://user:mypass@muse.ai/thread/a', 'https://muse.ai/thread/a?token=secret', 'https://muse.ai/other/a', 'http://muse.ai/thread/a']) assert.equal(safeMuseThreadUrl(url), null);
});

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const compiled = buildSync({ stdin: { contents: 'export {MuseTaskCard} from "./src/components/MuseDesktopPanel.jsx";', resolveDir: root }, bundle: true, platform: 'node', format: 'cjs', jsx: 'automatic', write: false, external: ['react', 'react/jsx-runtime'], define: { 'import.meta.env': '{}' }, loader: { '.css': 'empty' } });
const filename = path.join(root, 'tests/muse-virtual.cjs');
const module = new Module(filename); module.paths = Module._nodeModulePaths(path.dirname(filename)); module._compile(compiled.outputFiles[0].text, filename);
const { MuseTaskCard } = module.exports;
const markup = (value) => renderToStaticMarkup(React.createElement(MuseTaskCard, { task: value, projectId: 'project-a', roomId: 'room-a', busy: false, onCancel() {}, onObserve() {}, onUseReply() {} }));

test('actual Muse UI presents observed replies and explicit handoff without claiming external success', () => {
  const html = markup(task);
  assert.match(html, /已读取回复/); assert.match(html, /Actual native reply/); assert.match(html, /复制 Muse 回复/); assert.match(html, /使用 Muse 回复继续讨论/); assert.match(html, /导出 Markdown/);
  assert.equal(html.includes('任务成功'), false);
  const waiting = markup({ ...task, status: 'waiting_user', completion_evidence: null, monitoring: true });
  assert.match(waiting, /停止本地观察/); assert.match(waiting, /不会停止 Muse 云端任务/); assert.equal(waiting.includes('使用 Muse 回复继续讨论'), false);
  const interrupted = markup({ ...task, status: 'interrupted', completion_evidence: null });
  assert.match(interrupted, /继续观察原会话/); assert.equal(interrupted.includes('使用 Muse 回复继续讨论'), false);
});
