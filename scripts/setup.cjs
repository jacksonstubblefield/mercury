// One-time setup: Python venv + backend install, frontend deps.  `npm run setup`
const { ROOT, BACKEND, VENV, VENV_PY, run, fs } = require("./lib.cjs");
const path = require("node:path");

const systemPython = process.env.MERCURY_PYTHON || (process.platform === "win32" ? "python" : "python3");

// Electron downloads its binary in a postinstall script, which can be skipped; make sure it's there.
const electronDir = path.join(ROOT, "node_modules", "electron");
if (fs.existsSync(electronDir) && !fs.existsSync(path.join(electronDir, "path.txt"))) {
  run(process.execPath, [path.join(electronDir, "install.js")]);
}

if (!fs.existsSync(VENV_PY)) run(systemPython, ["-m", "venv", VENV]);
run(VENV_PY, ["-m", "pip", "install", "--upgrade", "pip"]);
run(VENV_PY, ["-m", "pip", "install", "-e", ".[dev]"], { cwd: BACKEND });
run("npm", ["install"], { cwd: path.join(ROOT, "frontend") });
console.log("\nSetup complete. Start Mercury with:  npm run dev");
