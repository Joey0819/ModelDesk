const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { createAutonomyTaskNavigator } = require('../autonomy-navigation.cjs');
const { isWorkbenchURL } = require('../runtime.cjs');
const UI = 'http://localhost:5173/';
const status = 'file:///Applications/ModelDesk.app/Contents/Resources/status.html?state=error';
const tick = () => new Promise(resolve => setImmediate(resolve));

function window(url = status, loading = false) {
  const owner = new EventEmitter(), contents = new EventEmitter(), sent = [];
  Object.assign(contents, { url, loading, destroyed: false, getURL: () => contents.url,
    isLoading: () => contents.loading, isDestroyed: () => contents.destroyed,
    send: (...args) => sent.push(args) });
  Object.assign(owner, { webContents: contents, destroyed: false, minimized: true,
    isDestroyed: () => owner.destroyed, isMinimized: () => owner.minimized,
    restore: () => { owner.minimized = false; }, show: () => {}, focus: () => {} });
  return { owner, contents, sent, loaded: next => { contents.url = next; contents.loading = false; contents.emit('did-finish-load'); },
    close: () => { owner.destroyed = contents.destroyed = true; owner.emit('closed'); } };
}

test('status error page reconnects; repeated clicks during connection deliver only the latest task', async () => {
  const fixture = window(); let connections = 0, finish;
  const open = createAutonomyTaskNavigator({ getWindow: () => fixture.owner, isWorkbenchURL,
    connectWorkbench: () => { connections++; return new Promise(resolve => { finish = resolve; }); } });
  open({ task_id: 'first', project_id: null }); open({ task_id: 'second', project_id: 'project' });
  await tick();
  assert.equal(connections, 1); assert.equal(fixture.owner.minimized, false);
  assert.equal(fixture.contents.listenerCount('did-finish-load'), 1);
  fixture.loaded(status); assert.equal(fixture.sent.length, 0);
  open({ task_id: 'latest', project_id: null });
  fixture.loaded(UI); finish(); await tick();
  assert.deepEqual(fixture.sent, [['modeldesk:open-autonomy-task', { task_id: 'latest', project_id: null }]]);
  assert.equal(fixture.contents.listenerCount('did-finish-load'), 0);
  assert.equal(fixture.owner.listenerCount('closed'), 0);
  fixture.loaded(UI); assert.equal(fixture.sent.length, 1);
});

test('ready workbench opens immediately; loading workbench waits without restarting services', async () => {
  const fixture = window(UI); let connections = 0;
  const open = createAutonomyTaskNavigator({ getWindow: () => fixture.owner, isWorkbenchURL, connectWorkbench: () => { connections++; } });
  open({ task_id: 'ready' });
  fixture.contents.loading = true;
  open({ task_id: 'old' }); open({ task_id: 'new' });
  assert.equal(fixture.sent.length, 1);
  fixture.loaded(UI); await tick();
  assert.deepEqual(fixture.sent.map(args => args[1].task_id), ['ready', 'new']);
  assert.equal(connections, 0);
  assert.equal(fixture.contents.listenerCount('did-finish-load'), 0);
});

test('closing a window removes pending callbacks; its connection completion cannot route an old selection', async () => {
  const original = window(), replacement = window(UI); let current = original, finish;
  const open = createAutonomyTaskNavigator({ getWindow: () => current.owner, isWorkbenchURL,
    connectWorkbench: () => new Promise(resolve => { finish = resolve; }) });
  open({ task_id: 'old' }); await tick(); original.close(); current = replacement;
  open({ task_id: 'current' }); original.loaded(UI); finish(); await tick();
  assert.deepEqual(original.sent, []);
  assert.deepEqual(replacement.sent, [['modeldesk:open-autonomy-task', { task_id: 'current' }]]);
  assert.equal(original.contents.listenerCount('did-finish-load'), 0);
  assert.equal(original.owner.listenerCount('closed'), 0);
});

test('failed reconnect stays recoverable and never sends task identifiers to the status page', async () => {
  const fixture = window(); let connections = 0;
  const open = createAutonomyTaskNavigator({ getWindow: () => fixture.owner, isWorkbenchURL,
    connectWorkbench: async () => { connections++; throw Error('service unavailable'); } });
  open({ task_id: 'first' }); await tick();
  assert.equal(connections, 1); assert.deepEqual(fixture.sent, []);
  open({ task_id: 'latest' }); await tick();
  assert.equal(connections, 2); assert.equal(fixture.contents.listenerCount('did-finish-load'), 1);
  fixture.loaded('https://example.com/'); assert.deepEqual(fixture.sent, []);
  fixture.loaded(UI);
  assert.deepEqual(fixture.sent, [['modeldesk:open-autonomy-task', { task_id: 'latest' }]]);
  assert.equal(fixture.contents.listenerCount('did-finish-load'), 0);
});
