const test = require('node:test');
const assert = require('node:assert/strict');
const { installMusePermissionActions, ACCESSIBILITY_SETTINGS_URL } = require('../muse-permissions.cjs');

test('Muse repair actions accept only trusted main frame and fixed settings/path targets', async () => {
  const handlers = new Map(), calls = [];
  const contents = { mainFrame: { processId: 21, routingId: 7 }, isDestroyed: () => false, getURL: () => 'http://localhost:5173/' };
  const owner = { webContents: contents, isDestroyed: () => false };
  const event = { sender: contents, senderFrame: { processId: 21, routingId: 7, url: 'http://localhost:5173/' } };
  installMusePermissionActions({ ipcMain: { handle: (name, handler) => handlers.set(name, handler) },
    shell: { openExternal: async value => calls.push(value), showItemInFolder: value => calls.push(value) },
    getWindow: () => owner, resolveWorkspace: () => '/Users/example/Project/Skills/llm-tools', exists: () => true });
  const settings = handlers.get('modeldesk:open-accessibility-settings'), reveal = handlers.get('modeldesk:reveal-muse-bridge');
  assert.equal(await settings(event, 'https://untrusted.example'), true);
  assert.equal(reveal(event, '/etc/passwd'), true);
  assert.deepEqual(calls, [ACCESSIBILITY_SETTINGS_URL, '/Users/example/Project/Skills/llm-council/desktop/bin/muse-bridge']);
  for (const bad of [{ ...event, sender: {} }, { ...event, senderFrame: { ...event.senderFrame, routingId: 8 } },
    { ...event, senderFrame: { ...event.senderFrame, url: 'https://untrusted.example' } }, { ...event, senderFrame: null }]) {
    assert.equal(await settings(bad), false); assert.equal(reveal(bad), false);
  }
  owner.isDestroyed = () => true;
  assert.equal(await settings(event), false); assert.equal(reveal(event), false);
  assert.equal(calls.length, 2);
});

test('missing bridge and failed system settings launch report failure without changing permission state', async () => {
  const handlers = new Map();
  const contents = { mainFrame: { processId: 1, routingId: 1 }, isDestroyed: () => false, getURL: () => 'http://localhost:5173/' };
  const event = { sender: contents, senderFrame: { ...contents.mainFrame, url: contents.getURL() } };
  installMusePermissionActions({ ipcMain: { handle: (name, handler) => handlers.set(name, handler) },
    shell: { openExternal: async () => { throw new Error('Cannot open'); }, showItemInFolder: () => { throw new Error('Should not reveal'); } },
    getWindow: () => ({ webContents: contents, isDestroyed: () => false }), resolveWorkspace: () => '/workspace/llm-tools', exists: () => false });
  assert.equal(await handlers.get('modeldesk:open-accessibility-settings')(event), false);
  assert.equal(handlers.get('modeldesk:reveal-muse-bridge')(event), false);
});
