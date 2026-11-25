// Mercury desktop shell: starts the Python backend, then shows the UI in a window.
//
//   electron .          -> backend serves the built frontend (frontend/dist) on a free port
//   electron . --dev    -> backend on :8765, UI from the Vite dev server on :5173 (hot reload)

const { app, BrowserWindow, dialog, ipcMain, shell } = require("electron");
const { spawn, execFile } = require("node:child_process");
const fs = require("node:fs");
const net = require("node:net");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..");
const DEV = process.argv.includes("--dev");
const VITE_URL = "http://127.0.0.1:5173";

let backend = null;
let backendPort = null;
let mainWindow = null;
let quitting = false;

function pythonPath() {
  if (process.env.MERCURY_PYTHON) return process.env.MERCURY_PYTHON;
  const venv = path.join(ROOT, "backend", ".venv");
  const candidate =
    process.platform === "win32" ? path.join(venv, "Scripts", "python.exe") : path.join(venv, "bin", "python");
  return fs.existsSync(candidate) ? candidate : process.platform === "win32" ? "python" : "python3";
}

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

async function waitForHealth(port, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (backend && backend.exitCode !== null) throw new Error(`Backend exited with code ${backend.exitCode}`);
    try {
      const res = await fetch(`http://127.0.0.1:${port}/api/health`);
      if (res.ok) return;
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error("Backend did not start within 30s");
}

async function startBackend() {
  backendPort = DEV ? Number(process.env.MERCURY_PORT || 8765) : await freePort();
  const env = { ...process.env, MERCURY_PORT: String(backendPort), PYTHONUNBUFFERED: "1" };
  if (DEV) env.MERCURY_FRONTEND_DIST = path.join(ROOT, "frontend", "no-dist"); // UI comes from Vite

  backend = spawn(pythonPath(), ["-m", "mercury"], {
    cwd: path.join(ROOT, "backend"),
    env,
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });
  backend.stdout.on("data", (d) => process.stdout.write(`[backend] ${d}`));
  backend.stderr.on("data", (d) => process.stderr.write(`[backend] ${d}`));
  backend.on("exit", (code) => {
    if (!quitting) {
      dialog.showErrorBox("Mercury backend stopped", `The Python backend exited (code ${code}). Mercury will close.`);
      app.quit();
    }
  });
  await waitForHealth(backendPort);
}

function stopBackend() {
  if (!backend || backend.exitCode !== null) return;
  if (process.platform === "win32") {
    // Kill the whole tree so agent subprocesses (Claude Code CLI) go too.
    execFile("taskkill", ["/pid", String(backend.pid), "/T", "/F"], () => {});
  } else {
    backend.kill("SIGTERM");
  }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 960,
    minHeight: 600,
    backgroundColor: "#0f1115",
    title: "Mercury",
    // Window and taskbar icon; rebuild it with scripts/make_icon.py.
    icon: path.join(ROOT, "assets", "icon", process.platform === "win32" ? "icon.ico" : "icon.png"),
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  const appUrl = DEV ? VITE_URL : `http://127.0.0.1:${backendPort}`;
  const isAppUrl = (url) => url.startsWith(appUrl);

  // Links leave the app and open in the system browser.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//.test(url)) void shell.openExternal(url);
    return { action: "deny" };
  });
  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (!isAppUrl(url)) {
      event.preventDefault();
      if (/^https?:\/\//.test(url)) void shell.openExternal(url);
    }
  });

  void mainWindow.loadURL(appUrl);
  if (DEV) mainWindow.webContents.openDevTools({ mode: "detach" });
}

ipcMain.handle("mercury:pick-folder", async () => {
  const result = await dialog.showOpenDialog(mainWindow, { properties: ["openDirectory"] });
  return result.canceled ? null : result.filePaths[0];
});

ipcMain.handle("mercury:open-external", async (_event, url) => {
  if (typeof url === "string" && /^https?:\/\//.test(url)) await shell.openExternal(url);
});

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  app.whenReady().then(async () => {
    try {
      await startBackend();
    } catch (err) {
      dialog.showErrorBox(
        "Mercury couldn't start",
        `${err.message}\n\nPython: ${pythonPath()}\nRun the setup steps in README.md (create backend/.venv and install the backend).`,
      );
      quitting = true;
      stopBackend();
      app.quit();
      return;
    }
    createWindow();
  });

  app.on("window-all-closed", () => app.quit());
  app.on("before-quit", () => {
    quitting = true;
    stopBackend();
  });
}
