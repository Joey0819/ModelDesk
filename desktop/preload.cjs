const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('modelDesk', Object.freeze({
  chooseProjectDirectory: () => ipcRenderer.invoke('modeldesk:choose-project-directory'),
}));
