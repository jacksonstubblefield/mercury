// Minimal, explicit bridge between the UI and the desktop shell.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("mercury", {
  pickFolder: () => ipcRenderer.invoke("mercury:pick-folder"),
  openExternal: (url) => ipcRenderer.invoke("mercury:open-external", url),
});
