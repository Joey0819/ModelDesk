const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { AutonomyNotifications, eligible, ENDPOINT } = require('../autonomy-notifications.cjs');
const row = { id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', project_id: null, notification_id: 'receipt-1', state: 'complete', read: false, updated_at: '2026-11-02T00:00:00Z', title: 'private task content', reason: 'private error' };
function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'modeldesk-notify-')); t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const alerts = [], opened = [];
  const options = { filename: path.join(dir, 'seen.json'), fetchInbox: async () => ({ tasks: [row] }), notify: n => alerts.push(n), openTask: x => opened.push(x), now: () => Date.parse(row.updated_at) };
  return { options, alerts, opened };
}
test('terminal notification survives window absence and is deduplicated across app restarts', async t => {
  const f = fixture(t); const observer = new AutonomyNotifications(f.options);
  await observer.tick(); await observer.tick(); await new AutonomyNotifications(f.options).tick();
  assert.equal(f.alerts.length, 1); assert.equal(JSON.stringify(f.alerts).includes('private'), false);
  f.alerts[0].onClick(); assert.deepEqual(f.opened, [{ task_id: row.id, project_id: null }]);
});
test('read, running, invalid targets do not notify; attention uses distinct persisted receipt', async t => {
  const f = fixture(t); f.options.fetchInbox = async () => ({ tasks: [{...row, read:true}, {...row,state:'running'}, {...row,id:'https://evil.invalid'}, {...row,state:'needs_attention',notification_id:'attention'}] });
  await new AutonomyNotifications(f.options).tick(); assert.equal(f.alerts.length,1); assert.match(f.alerts[0].title,/需要你处理/);
  assert.equal(eligible({...row,project_id:'bad'}),false); assert.equal(ENDPOINT,'http://127.0.0.1:8001/api/autonomy/inbox');
});
test('offline, stopped, old history and overlapping polls cannot spam', async t => {
  const f = fixture(t); let release; f.options.fetchInbox = () => new Promise(resolve => {release=resolve;});
  const watcher = new AutonomyNotifications(f.options); const pending = watcher.tick(); await watcher.tick(); watcher.stop(); release({tasks:[row]}); await pending; assert.equal(f.alerts.length,0);
  f.options.fetchInbox = async () => {throw Error('offline');}; await new AutonomyNotifications(f.options).tick();
  f.options.fetchInbox = async () => ({tasks:[{...row,updated_at:'2020-01-01'}]}); await new AutonomyNotifications(f.options).tick(); assert.equal(f.alerts.length,0);
});
