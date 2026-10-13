import test from 'node:test';
import assert from 'node:assert/strict';
import { connectionBody, connectionDraft, connectionIssue, connectionStatus } from '../src/model-connection-state.js';

test('editing never restores a key; empty key preserves backend secret without sentinels', () => {
  const draft = connectionDraft({ id: 'one', name: 'Grok', provider: 'xai', base_url: 'https://api.x.ai/v1', enabled: true, has_api_key: true, api_key: 'not-for-ui', models: [{ id: 'chat', label: 'Reviewer', alias: 'connection:one:chat' }] });
  assert.equal(draft.api_key, '');
  const body = connectionBody(draft);
  assert.equal(Object.hasOwn(body, 'api_key'), false);
  assert.deepEqual(body.models, [{ id: 'chat', label: 'Reviewer' }]);
  assert.equal(connectionIssue(draft), '');
});
test('changing endpoints cannot reuse the hidden key and insecure external endpoints are rejected', () => {
  const draft = connectionDraft({ id: 'one', name: 'Test', provider: 'openai_compatible', base_url: 'https://api.example/v1', enabled: true, has_api_key: true });
  assert.match(connectionIssue({ ...draft, base_url: 'https://other.example/v1' }), /重新填写密钥/);
  for (const base_url of ['http://remote.example/v1','https://a:b@example.com/v1','https://example.com/v1?api_key=private']) assert.ok(connectionIssue({ ...draft, base_url, api_key: 'replacement' }));
});
test('empty model list enables discovery, local endpoints need explicit choice, duplicate IDs fail', () => {
  const draft = { ...connectionDraft(null, 'openai_compatible'), name: 'Local', base_url: 'http://127.0.0.1:11434/v1', allow_local: true };
  assert.equal(connectionIssue(draft), '');
  assert.ok(connectionIssue({ ...draft, allow_local: false }));
  assert.match(connectionIssue({ ...draft, models: [{ id: 'm', label: 'A' }, { id: 'm', label: 'B' }] }), /重复/);
});
test('saved key, discovered model, successful test, and disabled state stay distinct', () => {
  assert.equal(connectionStatus({ enabled: true, has_api_key: true, models: [] }), '待选择模型');
  assert.equal(connectionStatus({ enabled: true, models: [{ id: 'm' }] }), '尚未测试调用');
  assert.equal(connectionStatus({ enabled: true, last_test: { ok: true } }), '调用已验证');
  assert.equal(connectionStatus({ enabled: false, last_test: { ok: true } }), '已停用');
});
