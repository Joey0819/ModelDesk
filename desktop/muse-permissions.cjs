const fs = require('node:fs');
const path = require('node:path');
const { isTrustedWorkbenchIPC, workspaceRoot } = require('./runtime.cjs');

const ACCESSIBILITY_SETTINGS_URL = 'x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility';

function installMusePermissionActions({ ipcMain, shell, getWindow, resolveWorkspace = workspaceRoot, exists = fs.existsSync }) {
  const trusted = event => {
    const owner = getWindow();
    return owner && !owner.isDestroyed() && isTrustedWorkbenchIPC(event, owner.webContents);
  };
  ipcMain.handle('modeldesk:open-accessibility-settings', async event => {
    if (!trusted(event)) return false;
    try { await shell.openExternal(ACCESSIBILITY_SETTINGS_URL); return true; }
    catch { return false; }
  });
  ipcMain.handle('modeldesk:reveal-muse-bridge', event => {
    if (!trusted(event)) return false;
    try {
      // Reveal the helper used by the local backend, not the bundle's copy.
      // No renderer-supplied URL, path or command is ever accepted.
      const helper = path.resolve(resolveWorkspace(), '../llm-council/desktop/bin/muse-bridge');
      if (!exists(helper)) return false;
      shell.showItemInFolder(helper);
      return true;
    } catch { return false; }
  });
}

module.exports = { ACCESSIBILITY_SETTINGS_URL, installMusePermissionActions };
