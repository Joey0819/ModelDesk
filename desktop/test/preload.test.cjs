const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('sandbox bridge exposes one fixed picker method and never forwards arbitrary arguments', async () => {
  let exposed;
  let invocation;
  const electron = {
    contextBridge: { exposeInMainWorld: (name, value) => { exposed = { name, value }; } },
    ipcRenderer: { invoke: async (...args) => { invocation = args; return '/Users/example/Project'; } },
  };
  const source = fs.readFileSync(path.join(__dirname, '..', 'preload.cjs'), 'utf8');
  vm.runInNewContext(source, { require: name => { assert.equal(name, 'electron'); return electron; } });
  assert.equal(exposed.name, 'modelDesk');
  assert.deepEqual(Object.keys(exposed.value), ['chooseProjectDirectory']);
  assert.equal(Object.isFrozen(exposed.value), true);
  assert.equal(await exposed.value.chooseProjectDirectory('/arbitrary/path', 'execute'), '/Users/example/Project');
  assert.deepEqual(invocation, ['modeldesk:choose-project-directory']);
});
