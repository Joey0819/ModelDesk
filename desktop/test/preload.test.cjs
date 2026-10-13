const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('sandbox bridge exposes only fixed picker and original-app methods', async () => {
  let exposed;
  let invocation;
  const electron = {
    contextBridge: { exposeInMainWorld: (name, value) => { exposed = { name, value }; } },
    ipcRenderer: { invoke: async (...args) => { invocation = args; return '/Users/example/Project'; } },
  };
  const source = fs.readFileSync(path.join(__dirname, '..', 'preload.cjs'), 'utf8');
  vm.runInNewContext(source, { require: name => { assert.equal(name, 'electron'); return electron; } });
  assert.equal(exposed.name, 'modelDesk');
  assert.deepEqual(Object.keys(exposed.value), ['chooseProjectDirectory', 'chooseInputFiles', 'chooseAttachmentDirectory', 'openOriginalApp']);
  assert.equal(Object.isFrozen(exposed.value), true);
  assert.equal(await exposed.value.chooseProjectDirectory('/arbitrary/path', 'execute'), '/Users/example/Project');
  assert.deepEqual(invocation, ['modeldesk:choose-project-directory']);
  await exposed.value.chooseInputFiles('/arbitrary/path');
  assert.deepEqual(invocation, ['modeldesk:choose-input-files']);
  await exposed.value.chooseAttachmentDirectory('/arbitrary/path');
  assert.deepEqual(invocation, ['modeldesk:choose-attachment-directory']);
  await exposed.value.openOriginalApp('chathub', 'https://malicious.invalid');
  assert.deepEqual(invocation, ['modeldesk:open-original-app', 'chathub']);
  for (const invalid of ['multibot', 'https://malicious.invalid', '../execute', null]) {
    assert.equal(await exposed.value.openOriginalApp(invalid), false);
    assert.deepEqual(invocation, ['modeldesk:open-original-app', 'chathub']);
  }
});
