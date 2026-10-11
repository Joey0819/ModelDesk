const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('modelDesk', Object.freeze({
  chooseProjectDirectory: () => ipcRenderer.invoke('modeldesk:choose-project-directory'),
  openOriginalApp: identifier => identifier === 'chathub' ? ipcRenderer.invoke('modeldesk:open-original-app', 'chathub') : Promise.resolve(false),
}));
