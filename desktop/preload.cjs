const { contextBridge, ipcRenderer } = require('electron');

let pendingTask = null;
let taskListener = null;
ipcRenderer.on('modeldesk:open-autonomy-task', (_event, value) => {
  if (!value || typeof value.task_id !== 'string') return;
  pendingTask = { task_id: value.task_id, project_id: value.project_id || null };
  if (taskListener) { const task = pendingTask; pendingTask = null; taskListener(task); }
});

contextBridge.exposeInMainWorld('modelDesk', Object.freeze({
  onOpenAutonomyTask: callback => {
    if (typeof callback !== 'function') return () => {};
    taskListener = callback;
    if (pendingTask) { const task = pendingTask; pendingTask = null; callback(task); }
    return () => { if (taskListener === callback) taskListener = null; };
  },
  chooseProjectDirectory: () => ipcRenderer.invoke('modeldesk:choose-project-directory'),
  chooseInputFiles: () => ipcRenderer.invoke('modeldesk:choose-input-files'),
  chooseAttachmentDirectory: () => ipcRenderer.invoke('modeldesk:choose-attachment-directory'),
  openMuse: () => ipcRenderer.invoke('modeldesk:open-muse'),
  openAccessibilitySettings: () => ipcRenderer.invoke('modeldesk:open-accessibility-settings'),
  revealMuseBridge: () => ipcRenderer.invoke('modeldesk:reveal-muse-bridge'),
  openOriginalApp: identifier => identifier === 'chathub' ? ipcRenderer.invoke('modeldesk:open-original-app', 'chathub') : Promise.resolve(false),
}));
