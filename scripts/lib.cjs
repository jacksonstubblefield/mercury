const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..");
const BACKEND = path.join(ROOT, "backend");
const VENV = path.join(BACKEND, ".venv");
const VENV_PY =
  process.platform === "win32" ? path.join(VENV, "Scripts", "python.exe") : path.join(VENV, "bin", "python");

function run(cmd, args, opts = {}) {
  console.log(`> ${cmd} ${args.join(" ")}`);
  // npm is a .cmd shim on Windows and needs a shell; everything else runs directly.
  const shell = process.platform === "win32" && cmd === "npm";
  const r = shell
    ? spawnSync([cmd, ...args].join(" "), { stdio: "inherit", shell: true, ...opts })
    : spawnSync(cmd, args, { stdio: "inherit", ...opts });
  if (r.status !== 0) process.exit(r.status ?? 1);
}

module.exports = { ROOT, BACKEND, VENV, VENV_PY, run, fs };
