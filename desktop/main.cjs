// Mercury desktop shell: starts the Python backend, then shows the UI in a window.
//
//   electron .          -> backend serves the built frontend (frontend/dist) on a free port
//   electron . --dev    -> backend on :8765, UI from the Vite dev server on :5173 (hot reload)
//   installed app       -> same as `electron .`, but with the Python and UI bundled in resources/
//                          (built by `npm run release`)

const { app, BrowserWindow, Notification, dialog, ipcMain, shell } = require("electron");
const { spawn, execFile } = require("node:child_process");
const fs = require("node:fs");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..");
const DEV = process.argv.includes("--dev");
const PACKAGED = app.isPackaged;
const VITE_URL = "http://127.0.0.1:5173";
const DATA_DIR = process.env.MERCURY_DATA_DIR || path.join(os.homedir(), ".mercury");
// The installed app has no terminal, so the backend's output goes here (overwritten each launch).
const BACKEND_LOG = path.join(DATA_DIR, "backend.log");

let backend = null;
let backendPort = null;
let mainWindow = null;
let splash = null;
let quitting = false;

const ICON_DIR = path.join(ROOT, "assets", "icon");

function pythonPath() {
  if (process.env.MERCURY_PYTHON) return process.env.MERCURY_PYTHON;
  if (PACKAGED) {
    const bundled = path.join(process.resourcesPath, "python");
    return process.platform === "win32" ? path.join(bundled, "python.exe") : path.join(bundled, "bin", "python3");
  }
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

// Generous: the first launch after an install can be slow (Defender scanning the new files).
async function waitForHealth(port, timeoutMs = 90000) {
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
  throw new Error(`Backend did not start within ${timeoutMs / 1000}s`);
}

async function startBackend() {
  backendPort = DEV ? Number(process.env.MERCURY_PORT || 8765) : await freePort();
  const env = { ...process.env, MERCURY_PORT: String(backendPort), PYTHONUNBUFFERED: "1" };
  if (DEV) env.MERCURY_FRONTEND_DIST = path.join(ROOT, "frontend", "no-dist"); // UI comes from Vite
  if (PACKAGED) env.MERCURY_FRONTEND_DIST = path.join(process.resourcesPath, "frontend");

  // The bundled Python runs isolated (-I): the user's PYTHONPATH/PYTHONHOME and user site-packages
  // can't leak in, and the working directory isn't on sys.path. -I also ignores PYTHONUNBUFFERED, hence -u.
  const args = PACKAGED ? ["-I", "-u", "-m", "mercury"] : ["-m", "mercury"];
  backend = spawn(pythonPath(), args, {
    cwd: PACKAGED ? os.homedir() : path.join(ROOT, "backend"),
    env,
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });
  let log = null;
  if (PACKAGED) {
    fs.mkdirSync(DATA_DIR, { recursive: true });
    log = fs.createWriteStream(BACKEND_LOG);
  }
  backend.stdout.on("data", (d) => (log ? log.write(d) : process.stdout.write(`[backend] ${d}`)));
  backend.stderr.on("data", (d) => (log ? log.write(d) : process.stderr.write(`[backend] ${d}`)));
  backend.on("exit", (code) => {
    if (!quitting) {
      const where = PACKAGED ? `\n\nDetails are in ${BACKEND_LOG}.` : "";
      dialog.showErrorBox("Mercury backend stopped", `The Python backend exited (code ${code}). Mercury will close.${where}`);
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

// A small "Starting Mercury…" window while the backend starts, so a slow start isn't a blank screen.
function showSplash() {
  splash = new BrowserWindow({
    width: 320,
    height: 220,
    frame: false,
    resizable: false,
    maximizable: false,
    fullscreenable: false,
    center: true,
    show: false,
    backgroundColor: "#0f1115",
    title: "Mercury",
    icon: path.join(ICON_DIR, process.platform === "win32" ? "icon.ico" : "icon.png"),
    webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false },
  });
  splash.once("ready-to-show", () => splash?.show());
  splash.on("closed", () => {
    splash = null;
  });
  void splash.loadFile(path.join(__dirname, "splash.html"));
}

function closeSplash() {
  if (splash && !splash.isDestroyed()) splash.destroy();
  splash = null;
}

function createWindow() {
  mainWindow = new BrowserWindow({
    show: false, // shown once the UI has painted, replacing the splash
    width: 1400,
    height: 900,
    minWidth: 960,
    minHeight: 600,
    backgroundColor: "#0f1115",
    title: "Mercury",
    // Window and taskbar icon; rebuild it with scripts/make_icon.py.
    icon: path.join(ICON_DIR, process.platform === "win32" ? "icon.ico" : "icon.png"),
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

  mainWindow.once("ready-to-show", () => {
    mainWindow.show();
    closeSplash();
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

// Shown notifications, kept so their click handlers survive garbage collection, including once they've
// moved to the Action Center (where "close" has already fired). Capped, oldest dropped first.
const notifications = new Set();
const MAX_NOTIFICATIONS = 20;
let flashing = false;

function showWindow() {
  if (!mainWindow) return;
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
}

// An agent session finished or needs the user (frontend/src/notifications.ts decides when).
ipcMain.on("mercury:notify", (event, n) => {
  if (!mainWindow || event.sender !== mainWindow.webContents || !Notification.isSupported()) return;
  const projectId = Number(n?.projectId);
  const runId = Number(n?.runId);
  if (!Number.isInteger(projectId) || !Number.isInteger(runId)) return;
  const text = (value, max) => String(value ?? "").slice(0, max);

  const notification = new Notification({
    title: text(n.title, 120) || "Mercury",
    body: text(n.body, 300),
    icon: path.join(ICON_DIR, "icon.png"),
  });
  notifications.add(notification);
  if (notifications.size > MAX_NOTIFICATIONS) notifications.delete(notifications.values().next().value);
  const forget = () => notifications.delete(notification);
  notification.on("click", () => {
    forget();
    showWindow();
    mainWindow?.webContents.send("mercury:open-run", { projectId, runId });
  });
  notification.on("failed", (_e, error) => {
    forget();
    console.error(`Notification failed: ${error}`);
  });
  notification.show();

  // Flash the taskbar button until the window gets focus.
  if (!mainWindow.isFocused() && !flashing) {
    flashing = true;
    mainWindow.flashFrame(true);
    mainWindow.once("focus", () => {
      flashing = false;
      mainWindow?.flashFrame(false);
    });
  }
});

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow?.isVisible()) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    } else {
      splash?.focus();
    }
  });

  app.whenReady().then(async () => {
    showSplash();
    try {
      await startBackend();
    } catch (err) {
      if (quitting) return; // the splash was closed while starting
      const hint = PACKAGED
        ? `Details are in ${BACKEND_LOG}. Reinstalling Mercury may help.`
        : "Run the setup steps in README.md (create backend/.venv and install the backend).";
      splash?.hide(); // hidden, not closed: closing the last window would quit before the dialog shows
      dialog.showErrorBox("Mercury couldn't start", `${err.message}\n\nPython: ${pythonPath()}\n${hint}`);
      quitting = true;
      stopBackend();
      app.quit();
      return;
    }
    if (!quitting) createWindow();
  });

  app.on("window-all-closed", () => app.quit());
  app.on("before-quit", () => {
    quitting = true;
    stopBackend();
  });
}
