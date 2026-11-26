// Capture the README screenshots from a throwaway Mercury instance seeded by seed.py (see its steps).
// It switches that instance's theme for each shot.
// env: SHOOT_URL (backend base URL), SHOOT_OUT (output dir, default docs/screenshots), SHOOT_ONLY (one shot by name)
// Pass these as env vars: Electron on Windows swallows arguments after the script path.
const { app, BrowserWindow } = require("electron");
const fs = require("node:fs");
const path = require("node:path");

const URL = process.env.SHOOT_URL;
const OUT = process.env.SHOOT_OUT || path.join(__dirname, "..", "..", "docs", "screenshots");
const ONLY = process.env.SHOOT_ONLY;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const SHOTS = [
  { name: "board", theme: "mercury-dark", steps: [["click", "Trailmix", ".project-item .name"], ["tab", "Board"]] },
  {
    name: "agent",
    theme: "mercury-light",
    steps: [["click", "Trailmix", ".project-item .name"], ["tab", "Agents"], ["clickContains", "Weather warnings for the start time"], ["clickContains", "Latest"]],
  },
  {
    name: "docs",
    theme: "hot-pink",
    steps: [["click", "Trailmix", ".project-item .name"], ["tab", "Docs"], ["click", "Planning", ".doc-collection-name"], ["click", "Data sources", ".doc-collection-name"], ["clickContains", "Hike time estimate"]],
  },
];

async function setTheme(preset) {
  const res = await fetch(`${URL}/api/appearance`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", Origin: URL },
    body: JSON.stringify({ preset, colors: {} }),
  });
  if (!res.ok) throw new Error(`appearance ${res.status}`);
}

function stepJs([op, text, selector]) {
  const t = JSON.stringify(text);
  if (op === "tab") {
    return `(() => { const el = [...document.querySelectorAll(".tab")].find(e => e.textContent.trim().startsWith(${t}));
      if (!el) return "missing " + ${t}; el.click(); return "ok"; })()`;
  }
  if (op === "click") {
    return `(() => { const el = [...document.querySelectorAll(${JSON.stringify(selector)})].find(e => e.textContent.trim() === ${t});
      if (!el) return "missing " + ${t}; el.click(); return "ok"; })()`;
  }
  // the deepest visible element whose text contains it
  return `(() => { const all = [...document.querySelectorAll("body *")].filter(e => e.textContent.includes(${t}) && e.offsetParent);
    const el = all.filter(e => ![...e.children].some(c => c.textContent.includes(${t}))).pop();
    if (!el) return "missing " + ${t}; el.click(); return "ok"; })()`;
}

async function capture(win, file) {
  await win.webContents.capturePage(); // the first capture can be a stale frame
  win.webContents.invalidate();
  await sleep(600);
  const img = await win.webContents.capturePage();
  fs.writeFileSync(file, img.toPNG());
  console.log("wrote", file, img.getSize());
}

app.whenReady().then(async () => {
  const win = new BrowserWindow({ width: 1440, height: 900, show: false, webPreferences: { offscreen: true } });
  win.webContents.setFrameRate(30);
  try {
    for (const shot of SHOTS) {
      if (ONLY && shot.name !== ONLY) continue;
      await setTheme(shot.theme);
      await win.loadURL(URL);
      await sleep(1500);
      for (const step of shot.steps) {
        const r = await win.webContents.executeJavaScript(stepJs(step));
        if (r !== "ok") console.log(shot.name, r);
        await sleep(900);
      }
      await sleep(1200);
      await capture(win, path.join(OUT, `${shot.name}.png`));
    }
  } catch (e) {
    console.error(e);
  }
  app.quit();
});
