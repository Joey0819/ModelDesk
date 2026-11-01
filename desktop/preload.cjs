const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('modelDesk', Object.freeze({
  chooseProjectDirectory: () => ipcRenderer.invoke('modeldesk:choose-project-directory'),
  chooseInputFiles: () => ipcRenderer.invoke('modeldesk:choose-input-files'),
  chooseAttachmentDirectory: () => ipcRenderer.invoke('modeldesk:choose-attachment-directory'),
  openMuse: () => ipcRenderer.invoke('modeldesk:open-muse'),
  openAccessibilitySettings: () => ipcRenderer.invoke('modeldesk:open-accessibility-settings'),
  revealMuseBridge: () => ipcRenderer.invoke('modeldesk:reveal-muse-bridge'),
  openOriginalApp: identifier => identifier === 'chathub' ? ipcRenderer.invoke('modeldesk:open-original-app', 'chathub') : Promise.resolve(false),
}));
