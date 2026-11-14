import test from 'node:test';
import assert from 'node:assert/strict';
import Module from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { FEISHU_SETUP_URL, feishuBinding, feishuBody, feishuConnectionStatus, feishuDraft, feishuIssue, feishuSenderIds, safeFeishuError } from '../src/feishu-state.js';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const compiled = buildSync({ stdin: { contents: 'export {FeishuConfigurationForm,FeishuConnectionOverview,FeishuRecentReceipts} from "./src/components/FeishuConnection.jsx"; export {feishuApi} from "./src/feishu-api.js"; export {machineInfo} from "./src/machine.js";', resolveDir: root }, bundle: true, platform: 'node', format: 'cjs', jsx: 'automatic', write: false, external: ['react', 'react/jsx-runtime'], define: { 'import.meta.env': '{}' }, loader: { '.css': 'empty' } });
const filename = path.join(root, 'tests/feishu-virtual.cjs');
const module = new Module(filename); module.paths = Module._nodeModulePaths(path.dirname(filename)); module._compile(compiled.outputFiles[0].text, filename);
const { FeishuConfigurationForm, FeishuConnectionOverview, FeishuRecentReceipts, feishuApi, machineInfo } = module.exports;
const binding = { chat_id: 'oc_allowed', name: 'Work group', project_id: 'project-a', room_id: 'room-a', allowed_sender_ids: ['ou_owner'], allow_native: false, natural_language_tasks: false };
const config = { app_id: 'cli_myapp', enabled: true, has_app_secret: true, bindings: [binding] };

test('Feishu drafts never restore credentials or arbitrary server fields and body preserves only explicit grants', () => {
  const draft = feishuDraft({ ...config, app_secret: 'server-private-value', token: 'server-token', bindings: [{ ...binding, hidden: 'private', allow_native: undefined }] });
  assert.equal(draft.app_secret, ''); assert.equal(draft.has_app_secret, true);
  assert.equal('token' in draft, false); assert.equal('hidden' in draft.bindings[0], false);
  assert.equal(draft.bindings[0].allow_native, false);
  const body = feishuBody({ ...draft, arbitrary_url: 'https://attacker.invalid', bindings: [{ ...draft.bindings[0], sender_text: 'not sent' }] });
  assert.deepEqual(body, { enabled: true, app_id: 'cli_myapp', bindings: [binding] });
  assert.equal('app_secret' in body, false); assert.equal('has_app_secret' in body, false); assert.equal('original_app_id' in body, false);
  assert.equal(feishuBody({ ...draft, app_secret: ' replacement ' }).app_secret, 'replacement');
  assert.deepEqual(feishuSenderIds('ou_a\nou_b,ou_a， ou_c'), ['ou_a', 'ou_b', 'ou_c']);
  assert.equal(feishuBinding().allow_native, false);
  assert.equal(feishuBinding().natural_language_tasks, false);
  assert.equal(feishuBody({ ...draft, bindings: [{ ...binding, natural_language_tasks: true }] }).bindings[0].natural_language_tasks, true);
});

test('Feishu validation permits a discovery-only listener and validates explicit chat destinations', () => {
  assert.equal(feishuIssue(feishuDraft(config)), '');
  assert.match(feishuIssue(feishuDraft()), /App ID/);
  assert.match(feishuIssue({ ...feishuDraft(config), has_app_secret: false }), /App Secret/);
  assert.match(feishuIssue({ ...feishuDraft(config), app_id: 'cli_other' }), /更换 App ID/);
  assert.equal(feishuIssue({ ...feishuDraft(config), app_id: 'cli_other', app_secret: 'new-value' }), '');
  assert.equal(feishuIssue({ ...feishuDraft(config), bindings: [] }), '');
  assert.match(feishuIssue({ ...feishuDraft(config), bindings: [{ ...binding, room_id: '' }] }), /项目群/);
  assert.match(feishuIssue({ ...feishuDraft(config), bindings: [binding, binding] }), /只能绑定一个/);
  assert.equal(feishuIssue({ ...feishuDraft(config), enabled: false, bindings: [] }), '');
});

test('Feishu actual configuration form uses password-only entry and explicit chat/sender/native controls', () => {
  const draft = feishuDraft({ ...config, app_secret: 'never-return-this-secret' });
  const html = renderToStaticMarkup(React.createElement(FeishuConfigurationForm, { draft, projects: [{ id: 'project-a', name: 'My project' }], chats: [{ chat_id: binding.chat_id, name: binding.name }], onChange() {}, onSave() {} }));
  assert.match(html, /aria-label="飞书 App Secret" type="password"/); assert.match(html, /留空保留/);
  assert.doesNotMatch(html, /never-return-this-secret|localStorage/);
  assert.match(html, /允许的飞书会话/); assert.match(html, /oc_allowed/); assert.match(html, /My project/); assert.match(html, /room-a/);
  assert.match(html, /允许的用户 Open ID/); assert.match(html, /允许此会话触发这台电脑原生 Agent/);
  assert.match(html, /<input type="checkbox"\/><span>允许此会话触发/); assert.match(html, /保存飞书设置/);
});

test('actual Feishu status view distinguishes saved credentials from real connection and message delivery', () => {
  const render = (props = {}) => renderToStaticMarkup(React.createElement(FeishuConnectionOverview, { connection: { config, status: { state: 'connecting', reason: '等待长连接' } }, onRefresh() {}, onTest() {}, onDiscover() {}, ...props }));
  const fresh = render({ connection: { config: {}, status: { state: 'not_configured' } } });
  assert.match(fresh, /未配置/); assert.doesNotMatch(fresh, /class="connection-state verified"/);
  assert.equal(feishuConnectionStatus({ state: 'connected' }, { ...config, enabled: false }), '已停用');
  assert.match(render(), /正在连接/); assert.doesNotMatch(render(), /class="connection-state verified"/);
  assert.match(render({ connection: { config, status: { state: 'connected' } } }), /class="connection-state verified">长连接在线/);
  const failed = render({ error: '服务无法访问' });
  assert.match(failed, /状态未确认/); assert.match(failed, /role="alert"/); assert.match(failed, /重新读取/);
  assert.match(fresh, /im.message.receive_v1/); assert.match(fresh, /\/modeldesk status/); assert.match(fresh, /\/modeldesk cancel/);
  assert.match(fresh, /\/modeldesk task/); assert.match(fresh, /\/modeldesk continue/); assert.match(fresh, /共享历史与资料/);
  const unbound = render({ connection: { config: { ...config, bindings: [] }, status: { state: 'connected' } } });
  assert.match(unbound, /已连接，但尚未允许任何聊天/); assert.match(unbound, /未绑定会话不会执行指令/);
  assert.match(fresh, /不代表任务已经完成/); assert.match(fresh, /官方长连接配置指南/);
  assert.ok(fresh.includes(FEISHU_SETUP_URL));
  const remote = render({ remote: true }); assert.match(remote, /请在这台电脑设置/); assert.doesNotMatch(remote, /验证已保存的应用凭据/);
  assert.equal(safeFeishuError('failed for exact-secret', 'exact-secret'), 'failed for [密钥已隐藏]');
});

// A macOS host reports itself through status.platform; the Feishu copy must
// follow it (Mac labels) instead of the neutral fallback. The neutral branch
// stays covered by the assertions above; both host shapes are pinned here.
test('Feishu host copy follows the platform report on macOS and stays neutral without one', () => {
  const macStatus = { platform: { system: 'Darwin', is_macos: true, is_linux: false, is_windows: false, has_native_muse_bridge: true, machine_label: '\u8fd9\u53f0 Mac', machine_short: 'Mac' } };
  const mac = machineInfo(macStatus);
  assert.equal(mac.label, '\u8fd9\u53f0 Mac'); assert.equal(mac.short, 'Mac'); assert.equal(mac.isMacos, true);
  const form = renderToStaticMarkup(React.createElement(FeishuConfigurationForm, { draft: feishuDraft(config), machine: mac, onChange() {} }));
  assert.match(form, /\u5bc6\u94a5\u4ec5\u4fdd\u5b58\u5728\u8fd9\u53f0 Mac\u7684\u670d\u52a1\u4e2d/);
  assert.match(form, /\u5141\u8bb8\u6b64\u4f1a\u8bdd\u89e6\u53d1Mac\u539f\u751f Agent\uff08\u5982 Muse\uff09/);
  assert.doesNotMatch(form, /\u8fd9\u53f0\u7535\u8111/);
  const overview = renderToStaticMarkup(React.createElement(FeishuConnectionOverview, { connection: { config, status: { state: 'connected' } }, machine: mac, onRefresh() {}, onTest() {}, onDiscover() {} }));
  assert.match(overview, /ModelDesk \u5728\u8fd9\u53f0 Mac\u6267\u884c/);
  assert.match(overview, /Mac\u4e0e ModelDesk \u670d\u52a1\u9700\u8981\u4fdd\u6301\u8fd0\u884c\u5e76\u8054\u7f51/);
  const remoteOverview = renderToStaticMarkup(React.createElement(FeishuConnectionOverview, { connection: { config: {}, status: { state: 'not_configured' } }, remote: true, machine: mac, onRefresh() {}, onTest() {}, onDiscover() {} }));
  assert.match(remoteOverview, /\u8bf7\u5728Mac\u8bbe\u7f6e/);
  assert.doesNotMatch(remoteOverview, /\u8bf7\u5728\u8fd9\u53f0\u7535\u8111\u8bbe\u7f6e/);
});

test('Feishu API uses fixed local endpoints and transmits changed credentials only on explicit save', async () => {
  const previous = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options) => { calls.push({ url, ...options }); return { ok: true, json: async () => ({ config, status: { state: 'disabled' } }) }; };
  try {
    await feishuApi.get(); await feishuApi.save(feishuBody(feishuDraft(config))); await feishuApi.test(); await feishuApi.chats('page + one');
    assert.deepEqual(calls.map((call) => [new URL(call.url).pathname + new URL(call.url).search, call.method]), [
      ['/api/feishu', 'GET'], ['/api/feishu', 'PUT'], ['/api/feishu/test', 'POST'], ['/api/feishu/chats?page_token=page%20%2B%20one', 'GET'],
    ]);
    assert.equal('app_secret' in JSON.parse(calls[1].body), false);
    assert.equal(calls[2].body, undefined);
    globalThis.fetch = async () => ({ ok: false, status: 403, json: async () => ({ detail: '仅可在 Mac 配置' }) });
    await assert.rejects(feishuApi.get(), /仅可在 Mac 配置/);
    globalThis.fetch = async () => { throw new Error('offline'); };
    await assert.rejects(feishuApi.get(), /暂时无法访问/);
  } finally { globalThis.fetch = previous; }
});

test('Feishu receipt renderer exposes bounded actual delivery failures separately from task completion', () => {
  const records = [
    { id: 'oldest', state: 'delivered', reason: 'Too old to show', updated_at: '2026-11-01T00:00:00Z' },
    ...['received', 'dispatching', 'monitoring', 'delivered', 'needs_attention'].map((state, index) => ({ id: `r-${index}`, state, reason: state === 'needs_attention' ? '文件过大，飞书尚未收到完整成果' : `Actual ${state} receipt`, chat_id: 'oc_allowed', job_id: `job-${index}`, prompt: 'never-render-original-prompt', app_secret: 'never-render-private-field', updated_at: `2026-11-02T0${index}:00:00Z` })),
  ];
  const html = renderToStaticMarkup(React.createElement(FeishuRecentReceipts, { recent: records }));
  assert.match(html, /<details class="feishu-recent-receipts"><summary>最近指令与回传（5）/);
  assert.doesNotMatch(html, /<details[^>]*open|Too old to show|never-render-original-prompt|never-render-private-field/);
  assert.match(html, /文件过大，飞书尚未收到完整成果/); assert.match(html, /收发需要处理/); assert.match(html, /回传已确认/);
  assert.match(html, /正在等待任务/); assert.match(html, /正在派发/); assert.match(html, /已接收指令/);
  assert.match(html, /关联任务：job-4/); assert.match(html, /飞书会话：oc_allowed/);
  assert.ok(html.indexOf('文件过大') < html.indexOf('Actual delivered receipt'));
  assert.deepEqual(records.map((record) => record.id), ['oldest', 'r-0', 'r-1', 'r-2', 'r-3', 'r-4']);
  const empty = renderToStaticMarkup(React.createElement(FeishuRecentReceipts));
  assert.match(empty, /暂无指令回执/); assert.doesNotMatch(empty, /回传已确认/);
  for (const [state, label] of [['attention', '任务有待处理事项'], ['rejected', '指令已拒绝']]) assert.match(renderToStaticMarkup(React.createElement(FeishuRecentReceipts, { recent: [{ id: state, state, reason: 'Actual reason' }] })), new RegExp(label));
});
