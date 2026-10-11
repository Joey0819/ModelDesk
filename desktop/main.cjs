const { app, BrowserWindow, Menu, shell, screen, ipcMain, dialog } = require('electron');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const fs = require('node:fs');
const path = require('node:path');
const { UI_URL, HEALTH_URLS, workspaceRoot, serviceEnvironment, isWorkbenchURL, isExportURL, isExternalURL, isStatusURL, isTrustedWorkbenchIPC } = require('./runtime.cjs');

const execFileAsync = promisify(execFile);
const statusPath = path.join(__dirname, 'status.html');
let mainWindow;
let startInProgress = false;
let projectPickerInProgress = false;

app.setName('ModelDesk');
app.enableSandbox();

async function healthy(url) {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(2500), redirect: 'error' });
    await response.body?.cancel();
    return response.ok;
  } catch {
    return false;
  }
}

async function showState(state) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    try {
      await mainWindow.loadFile(statusPath, { query: { state } });
    } catch {
      // Closing a window can abort its pending load; do not reject the main lifecycle task.
      if (mainWindow && !mainWindow.isDestroyed()) mainWindow.show();
    }
  }
}

async function connectWorkbench() {
  if (startInProgress) return;
  startInProgress = true;
  try {
    await showState('starting');
    const root = workspaceRoot();
    const controller = path.join(root, 'scripts', 'services.py');
    if (!fs.existsSync(controller)) throw new Error('workspace-controller-missing');
    // Fixed executable and separate arguments; renderer content never becomes shell code.
    await execFileAsync('/usr/bin/python3', [controller, 'start'], {
      cwd: root,
      env: serviceEnvironment(),
      timeout: 65000,
      maxBuffer: 128 * 1024,
      windowsHide: true,
    });
    const checks = await Promise.all(HEALTH_URLS.map(healthy));
    if (!checks.every(Boolean)) throw new Error('workbench-not-ready');
    if (mainWindow && !mainWindow.isDestroyed()) await mainWindow.loadURL(UI_URL);
  } catch {
    // Avoid showing controller stderr or credentials in the renderer.
    await showState('error');
  } finally {
    startInProgress = false;
  }
}

function openExternal(value) {
  if (isExternalURL(value)) void shell.openExternal(value).catch(() => {});
}

function installProjectPicker() {
  ipcMain.handle('modeldesk:choose-project-directory', async event => {
    const owner = mainWindow;
    if (!owner || owner.isDestroyed() || !isTrustedWorkbenchIPC(event, owner.webContents) || projectPickerInProgress) return null;
    projectPickerInProgress = true;
    try {
      const selection = await dialog.showOpenDialog(owner, {
        title: '选择项目文件夹',
        buttonLabel: '选择项目',
        properties: ['openDirectory', 'dontAddToRecent'],
      });
      if (selection.canceled || owner.isDestroyed() || !isTrustedWorkbenchIPC(event, owner.webContents)) return null;
      return selection.filePaths.length === 1 ? selection.filePaths[0] : null;
    } catch {
      return null;
    } finally {
      projectPickerInProgress = false;
    }
  });
}

function createWindow() {
  const { width: availableWidth, height: availableHeight } = screen.getPrimaryDisplay().workArea;
  const width = Math.min(1440, Math.max(1, availableWidth - 40));
  const height = Math.min(960, Math.max(1, availableHeight - 40));
  mainWindow = new BrowserWindow({
    title: 'ModelDesk',
    width,
    height,
    minWidth: Math.min(1060, width),
    minHeight: Math.min(700, height),
    backgroundColor: '#f7f7f4',
    show: false,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      webSecurity: true,
      allowRunningInsecureContent: false,
      devTools: false,
      partition: 'persist:modeldesk-workbench',
      preload: path.join(__dirname, 'preload.cjs'),
    },
  });

  const contents = mainWindow.webContents;
  const windowSession = contents.session;
  contents.on('page-title-updated', event => { event.preventDefault(); });
  windowSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  windowSession.setPermissionCheckHandler(() => false);
  const configureExportDownload = (_event, item, sourceContents) => {
    if (sourceContents && sourceContents.id !== contents.id) return;
    const chain = item.getURLChain();
    if (!chain.length || !chain.every(isExportURL)) {
      item.cancel();
      return;
    }
    const format = new URL(chain[0]).searchParams.get('format');
    item.setSaveDialogOptions({
      title: '导出协作结果',
      buttonLabel: '保存',
      filters: [{ name: format === 'md' ? 'Markdown 文档' : 'JSON 数据', extensions: [format] }],
    });
    // Do not call setSavePath: the native save dialog keeps file choice/overwrite with the user.
  };
  windowSession.on('will-download', configureExportDownload);
  contents.once('destroyed', () => windowSession.removeListener('will-download', configureExportDownload));

  contents.on('will-navigate', (event, url) => {
    const current = contents.getURL();
    if (url === 'modeldesk-action://retry' && isStatusURL(current, statusPath)) {
      event.preventDefault();
      void connectWorkbench();
      return;
    }
    if (isExportURL(url)) {
      event.preventDefault();
      try { contents.downloadURL(url); } catch { /* A closed window can abort the download request. */ }
      return;
    }
    if (isWorkbenchURL(url) || isStatusURL(url, statusPath)) return;
    event.preventDefault();
    openExternal(url);
  });
  contents.on('will-redirect', (event, url) => {
    if (!isWorkbenchURL(url) && !isStatusURL(url, statusPath)) event.preventDefault();
  });
  contents.setWindowOpenHandler(({ url }) => {
    if (isExportURL(url)) {
      try { contents.downloadURL(url); } catch { /* Preserve the existing page if this window closed. */ }
    } else if (isWorkbenchURL(url)) void mainWindow.loadURL(url).catch(() => showState('error'));
    else openExternal(url);
    return { action: 'deny' };
  });
  contents.on('did-fail-load', (_event, code, _description, url, isMainFrame) => {
    if (isMainFrame && code !== -3 && isWorkbenchURL(url)) void showState('error');
  });
  mainWindow.once('ready-to-show', () => mainWindow.show());
  mainWindow.on('closed', () => { mainWindow = null; });
  void connectWorkbench();
}

function installMenu() {
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    { label: 'ModelDesk', submenu: [
      { role: 'about' },
      { type: 'separator' },
      { label: '重新连接工作台', click: () => void connectWorkbench() },
      { type: 'separator' },
      { role: 'hide' }, { role: 'hideOthers' }, { role: 'unhide' },
      { type: 'separator' }, { role: 'quit' },
    ] },
    { label: '编辑', submenu: [{ role: 'undo' }, { role: 'redo' }, { type: 'separator' }, { role: 'cut' }, { role: 'copy' }, { role: 'paste' }, { role: 'selectAll' }] },
    { label: '显示', submenu: [{ role: 'reload' }, { role: 'resetZoom' }, { role: 'zoomIn' }, { role: 'zoomOut' }, { role: 'togglefullscreen' }] },
    { label: '窗口', submenu: [{ role: 'minimize' }, { role: 'zoom' }, { role: 'front' }] },
  ]));
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (!mainWindow) createWindow();
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
  });
  app.whenReady().then(() => {
    app.setAboutPanelOptions({ applicationName: 'ModelDesk', applicationVersion: app.getVersion(), copyright: 'Local multi-model workbench' });
    installMenu();
    installProjectPicker();
    createWindow();
    app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
  });
  // Services are shared with ChatHub/browser clients; closing this window never stops them.
  app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
}
