// Builds the Windows installer: dist/Mercury-Setup-<version>.exe.  `npm run release`
//
// The installer is self-contained: Electron, the built UI, and a standalone Python
// (python-build-standalone) with the backend installed into it. The Claude Code CLI comes
// along inside claude-agent-sdk, so users don't need Python or Node.
const { ROOT, BACKEND, run, fs } = require("./lib.cjs");
const path = require("node:path");

if (process.platform !== "win32") {
  console.error("The release build is Windows-only for now.");
  process.exit(1);
}

// Pinned so builds are repeatable. Bump both together; any 3.10+ CPython works.
const PYTHON_RELEASE = "20261124";
const PYTHON_ARCHIVE = `cpython-3.12.16+${PYTHON_RELEASE}-x86_64-pc-windows-msvc-install_only.tar.gz`;
const PYTHON_URL = `https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_RELEASE}/${encodeURIComponent(PYTHON_ARCHIVE)}`;

const STAGE = path.join(ROOT, "build"); // electron-builder copies build/python into the app's resources
const CACHE = path.join(STAGE, "cache");
const PYTHON_DIR = path.join(STAGE, "python");
const PYTHON = path.join(PYTHON_DIR, "python.exe");
// Windows' own bsdtar; Git's tar mistakes "C:\..." for a remote host.
const TAR = path.join(process.env.SystemRoot || "C:\\Windows", "System32", "tar.exe");

async function download(url, dest) {
  if (fs.existsSync(dest)) return;
  console.log(`> download ${url}`);
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Download failed: ${res.status} ${res.statusText}`);
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.writeFileSync(`${dest}.part`, Buffer.from(await res.arrayBuffer()));
  fs.renameSync(`${dest}.part`, dest);
}

async function main() {
  const { version } = require(path.join(ROOT, "package.json"));
  // The version lives in four places; they must agree (the app's /api/health reports the backend's).
  const versions = {
    "frontend/package.json": require(path.join(ROOT, "frontend", "package.json")).version,
    "backend/pyproject.toml": /^version\s*=\s*"([^"]+)"/m.exec(fs.readFileSync(path.join(BACKEND, "pyproject.toml"), "utf8"))?.[1],
    "backend/mercury/__init__.py": /__version__\s*=\s*"([^"]+)"/.exec(fs.readFileSync(path.join(BACKEND, "mercury", "__init__.py"), "utf8"))?.[1],
  };
  const stale = Object.entries(versions).filter(([, v]) => v !== version);
  if (stale.length) {
    console.error(`package.json says ${version}, but:\n${stale.map(([f, v]) => `  ${f}: ${v}`).join("\n")}`);
    process.exit(1);
  }
  console.log(`Building Mercury ${version}\n`);

  // 1. The UI.
  run("npm", ["run", "build"], { cwd: ROOT });

  // 2. A fresh standalone Python with the backend (not editable) installed into it.
  const archive = path.join(CACHE, PYTHON_ARCHIVE);
  await download(PYTHON_URL, archive);
  fs.rmSync(PYTHON_DIR, { recursive: true, force: true });
  run(TAR, ["-xzf", archive, "-C", STAGE]); // the archive's top folder is python/
  run(PYTHON, ["-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location", BACKEND]);
  // pip leaves a build/ folder in the source tree.
  fs.rmSync(path.join(BACKEND, "build"), { recursive: true, force: true });
  // Debug symbols and Tk aren't needed at runtime (~95 MB).
  for (const dir of ["tcl", "Lib/tkinter", "Lib/idlelib", "Lib/turtledemo"]) {
    fs.rmSync(path.join(PYTHON_DIR, dir), { recursive: true, force: true });
  }
  for (const file of fs.readdirSync(PYTHON_DIR, { recursive: true })) {
    if (file.endsWith(".pdb")) fs.rmSync(path.join(PYTHON_DIR, file));
  }
  // The installer stamps every file with the install time, so .pyc files that check the source's
  // mtime all look stale and Python recompiles everything on the first launch (~10-15 s with nothing
  // on screen). Unchecked-hash .pyc files are never checked against the source; nothing edits the
  // bundled Python, so that's safe (and run 2+ is no slower).
  run(PYTHON, ["-I", "-m", "compileall", "-q", "-f", "-j", "0", "--invalidation-mode", "unchecked-hash", path.join(PYTHON_DIR, "Lib")]);

  // 3. The installer.
  fs.rmSync(path.join(ROOT, "dist"), { recursive: true, force: true });
  run(process.execPath, [path.join(ROOT, "node_modules", "electron-builder", "cli.js"), "--win", "--publish", "never"], {
    cwd: ROOT,
  });

  const exe = path.join(ROOT, "dist", `Mercury-Setup-${version}.exe`);
  const mb = (fs.statSync(exe).size / 1024 / 1024).toFixed(0);
  console.log(`\nDone: ${path.relative(ROOT, exe)} (${mb} MB)`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
