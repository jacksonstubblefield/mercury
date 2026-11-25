// Minimal, explicit bridge between the UI and the desktop shell.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("mercury", {
  pickFolder: () => ipcRenderer.invoke("mercury:pick-folder"),
  openExternal: (url) => ipcRenderer.invoke("mercury:open-external", url),
  // Agent session notifications: show one, and hear when the user clicks one.
  notify: (n) => ipcRenderer.send("mercury:notify", n),
  onOpenRun: (callback) => {
    const listener = (_event, target) => callback(target);
    ipcRenderer.on("mercury:open-run", listener);
    return () => ipcRenderer.removeListener("mercury:open-run", listener);
  },
});
