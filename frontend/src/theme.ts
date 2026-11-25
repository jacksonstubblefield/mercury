// Themes: the color tokens they set, and a small store that applies the user's choice to <html>
// as CSS custom properties and saves it to the backend.
//
// The themes themselves (system and the user's own) come from the backend (services/themes.py),
// which agents can change too, so the store polls for changes. Only Mercury Dark is built in here,
// for painting before the backend answers; styles.css declares the same values on :root.
// Tints such as the diff backgrounds are derived in CSS with color-mix().

import { useSyncExternalStore } from "react";
import { api } from "./api";
import type { Appearance, Theme } from "./types";

// --------------------------------------------------------------------------- tokens

export type BaseToken =
  | "--bg"
  | "--bg-elev"
  | "--bg-elev-2"
  | "--bg-hover"
  | "--border"
  | "--text"
  | "--text-dim"
  | "--text-faint"
  | "--accent"
  | "--ok"
  | "--warn"
  | "--err";

/** Worked out from the base tokens unless the user picks a color for them. */
export type DerivedToken = "--accent-dim" | "--on-accent";
export type ColorToken = BaseToken | DerivedToken;
export type Palette = Record<BaseToken, string>;

export interface TokenInfo {
  key: ColorToken;
  label: string;
  hint: string;
}

export const TOKEN_GROUPS: { title: string; tokens: TokenInfo[] }[] = [
  {
    title: "Surfaces",
    tokens: [
      { key: "--bg", label: "Background", hint: "Main area, inputs, code blocks" },
      { key: "--bg-elev", label: "Panels", hint: "Sidebar, cards, dialogs" },
      { key: "--bg-elev-2", label: "Raised", hint: "Buttons, selected items" },
      { key: "--bg-hover", label: "Hover", hint: "Rows and buttons under the pointer" },
      { key: "--border", label: "Borders", hint: "Dividers and outlines" },
    ],
  },
  {
    title: "Text",
    tokens: [
      { key: "--text", label: "Text", hint: "Body text and headings" },
      { key: "--text-dim", label: "Secondary text", hint: "Labels, metadata" },
      { key: "--text-faint", label: "Faint text", hint: "Hints, placeholders, line numbers" },
    ],
  },
  {
    title: "Accent",
    tokens: [
      { key: "--accent", label: "Accent", hint: "Primary buttons, links, active tab" },
      { key: "--accent-dim", label: "Accent, subtle", hint: "Badges, highlights, focus rings" },
      { key: "--on-accent", label: "Text on accent", hint: "Label on primary buttons" },
    ],
  },
  {
    title: "Status",
    tokens: [
      { key: "--ok", label: "Success", hint: "Finished runs, added lines" },
      { key: "--warn", label: "Warning", hint: "Approvals, high priority" },
      { key: "--err", label: "Error", hint: "Failures, deleted lines" },
    ],
  },
];

export const DERIVED_TOKENS: DerivedToken[] = ["--accent-dim", "--on-accent"];

// --------------------------------------------------------------------------- themes

/** STATUS_* feed paletteFromColor; they match the system themes' status colors in services/themes.py. */
const STATUS_DARK = { "--ok": "#4fd18b", "--warn": "#f0b44c", "--err": "#ff6b6b" } as const;
const STATUS_LIGHT = { "--ok": "#1e8a50", "--warn": "#a86a00", "--err": "#d33a3a" } as const;

/** Mercury Dark, used until the backend's theme list arrives (and for a theme that's gone). */
export const FALLBACK_THEME: Theme = {
  id: "mercury-dark",
  name: "Mercury Dark",
  system: true,
  colors: {
    "--bg": "#0f1115",
    "--bg-elev": "#161920",
    "--bg-elev-2": "#1d212a",
    "--bg-hover": "#232835",
    "--border": "#2a2f3b",
    "--text": "#e6e8ee",
    "--text-dim": "#9aa1b2",
    "--text-faint": "#6b7285",
    "--accent": "#7c8cff",
    ...STATUS_DARK,
  },
};

export function findTheme(themes: Theme[], id: string): Theme {
  return themes.find((t) => t.id === id) ?? themes.find((t) => t.id === FALLBACK_THEME.id) ?? FALLBACK_THEME;
}

export const DEFAULT_APPEARANCE: Appearance = {
  preset: FALLBACK_THEME.id,
  colors: {},
  font_ui: null,
  font_mono: null,
  radius: null,
};

export const DEFAULT_FONT_UI = '"Segoe UI", system-ui, -apple-system, sans-serif';
export const DEFAULT_FONT_MONO = '"Cascadia Code", "JetBrains Mono", Consolas, monospace';
export const DEFAULT_RADIUS = 8;

// --------------------------------------------------------------------------- color math

type Rgb = [number, number, number];

export const isHex = (s: string) => /^#[0-9a-f]{6}$/i.test(s);

function toRgb(hex: string): Rgb {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function toHex([r, g, b]: Rgb): string {
  return "#" + [r, g, b].map((v) => Math.round(Math.min(255, Math.max(0, v))).toString(16).padStart(2, "0")).join("");
}

/** `amount` of `a` mixed into `b`, like CSS color-mix(in srgb, a amount, b). */
export function mix(a: string, b: string, amount: number): string {
  const x = toRgb(a);
  const y = toRgb(b);
  return toHex([0, 1, 2].map((i) => x[i] * amount + y[i] * (1 - amount)) as Rgb);
}

function luminance(hex: string): number {
  const c = toRgb(hex).map((v) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}

export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

export const isLight = (hex: string) => luminance(hex) > 0.4;

function toHsl(hex: string): [number, number, number] {
  const [r, g, b] = toRgb(hex).map((v) => v / 255);
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  if (max === min) return [0, 0, l];
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  const h = max === r ? (g - b) / d + (g < b ? 6 : 0) : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return [h * 60, s, l];
}

function hsl(h: number, s: number, l: number): string {
  const k = (n: number) => (n + h / 30) % 12;
  const a = s * Math.min(l, 1 - l);
  const f = (n: number) => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
  return toHex([f(0) * 255, f(8) * 255, f(4) * 255]);
}

/**
 * A whole palette from one color: surfaces and text take its hue, and it becomes the accent.
 * `tint` (0-1) is how strongly the surfaces are colored.
 */
export function paletteFromColor(color: string, mode: "dark" | "light", tint: number): Palette {
  const [h, s] = toHsl(color);
  const sat = (max: number) => Math.min(s, 1) * max * tint;
  if (mode === "dark") {
    return {
      "--bg": hsl(h, sat(0.5), 0.06),
      "--bg-elev": hsl(h, sat(0.45), 0.09),
      "--bg-elev-2": hsl(h, sat(0.42), 0.125),
      "--bg-hover": hsl(h, sat(0.4), 0.155),
      "--border": hsl(h, sat(0.38), 0.21),
      "--text": hsl(h, sat(0.9), 0.93),
      "--text-dim": hsl(h, sat(0.55), 0.7),
      "--text-faint": hsl(h, sat(0.3), 0.49),
      "--accent": color,
      ...STATUS_DARK,
    };
  }
  return {
    "--bg": hsl(h, sat(0.6), 0.985),
    "--bg-elev": hsl(h, sat(0.5), 0.955),
    "--bg-elev-2": hsl(h, sat(0.45), 0.915),
    "--bg-hover": hsl(h, sat(0.42), 0.885),
    "--border": hsl(h, sat(0.38), 0.83),
    "--text": hsl(h, sat(0.45), 0.13),
    "--text-dim": hsl(h, sat(0.25), 0.37),
    "--text-faint": hsl(h, sat(0.15), 0.56),
    // A pale accent would vanish on a light background, so darken it until it reads.
    "--accent": readableOn(color, "#fbfbfd"),
    ...STATUS_LIGHT,
  };
}

function readableOn(color: string, bg: string): string {
  let c = color;
  for (let i = 0; i < 10 && contrast(c, bg) < 3; i++) c = mix(c, "#000000", 0.88);
  return c;
}

// --------------------------------------------------------------------------- resolving

function derive(key: DerivedToken, p: Palette): string {
  if (key === "--accent-dim") return mix(p["--accent"], p["--bg"], 0.45);
  // --on-accent: whichever of near-black and white reads better on the accent.
  return contrast("#0b0d12", p["--accent"]) >= contrast("#ffffff", p["--accent"]) ? "#0b0d12" : "#ffffff";
}

/**
 * Every color token's value for this appearance: the theme's, then the user's colors, then derived ones
 * (unless the theme or the user pins them).
 */
export function resolveColors(a: Appearance, themes: Theme[] = state.themes): Record<ColorToken, string> {
  const theme = findTheme(themes, a.preset);
  // A theme from the backend should set every base token, but fill gaps from Mercury Dark just in case.
  const base = { ...FALLBACK_THEME.colors } as Palette;
  for (const k of Object.keys(base) as BaseToken[]) {
    const v = a.colors[k] ?? theme.colors[k];
    if (v && isHex(v)) base[k] = v;
  }
  const out = { ...base } as Record<ColorToken, string>;
  for (const k of DERIVED_TOKENS) {
    const v = a.colors[k] ?? theme.colors[k];
    out[k] = v && isHex(v) ? v : derive(k, base);
  }
  return out;
}

export function applyAppearance(a: Appearance, themes: Theme[] = state.themes, root: HTMLElement = document.documentElement): void {
  const colors = resolveColors(a, themes);
  for (const [k, v] of Object.entries(colors)) root.style.setProperty(k, v);
  const radius = a.radius ?? DEFAULT_RADIUS;
  root.style.setProperty("--radius", `${radius}px`);
  root.style.setProperty("--radius-sm", `${Math.round(radius * 0.75)}px`);
  root.style.setProperty("--font-ui", a.font_ui || DEFAULT_FONT_UI);
  root.style.setProperty("--mono", a.font_mono || DEFAULT_FONT_MONO);
  // Native scrollbars, checkboxes and date pickers follow this.
  root.style.colorScheme = isLight(colors["--bg"]) ? "light" : "dark";
}

/** Accepts anything (pasted JSON, an old file) and keeps only the parts that make sense. */
export function normalizeAppearance(raw: unknown): Appearance {
  const o = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const colors: Record<string, string> = {};
  if (o.colors && typeof o.colors === "object") {
    for (const [k, v] of Object.entries(o.colors as Record<string, unknown>)) {
      if (/^--[a-z0-9-]{1,40}$/.test(k) && typeof v === "string" && isHex(v)) colors[k] = v.toLowerCase();
    }
  }
  const font = (v: unknown) => (typeof v === "string" && v.trim() && !/[;{}<>\\]/.test(v) ? v.trim().slice(0, 200) : null);
  const radius = typeof o.radius === "number" ? Math.round(Math.min(16, Math.max(0, o.radius))) : null;
  return {
    // Not checked against the theme list, which may not have loaded yet; an unknown id shows Mercury Dark.
    preset: typeof o.preset === "string" && /^[a-z0-9-]{1,40}$/.test(o.preset) ? o.preset : FALLBACK_THEME.id,
    colors,
    font_ui: font(o.font_ui),
    font_mono: font(o.font_mono),
    radius,
  };
}

// --------------------------------------------------------------------------- store

export type SaveStatus = "idle" | "saving" | "saved" | "error";

interface State {
  appearance: Appearance;
  /** System themes first, then the user's. Just Mercury Dark until the backend answers. */
  themes: Theme[];
  status: SaveStatus;
  error: string | null;
}

// Remembered in this window so a reload in dev paints the theme at once. The backend copy is
// the real one: the packaged app gets a new origin (port) each launch, which empties localStorage.
const CACHE_KEY = "mercury.appearance";
const THEMES_CACHE_KEY = "mercury.themes";
const SAVE_DELAY_MS = 400;
// Agents can change themes and the appearance, so check the backend now and then.
const POLL_MS = 4000;

let state: State = { appearance: DEFAULT_APPEARANCE, themes: [FALLBACK_THEME], status: "idle", error: null };
const listeners = new Set<() => void>();
let saveTimer: ReturnType<typeof setTimeout> | undefined;
let saving: Promise<unknown> = Promise.resolve();
// Bumped on every local change and finished save, so a poll that started before one is ignored.
let generation = 0;
let pollTimer: ReturnType<typeof setInterval> | undefined;

function emit(next: Partial<State>) {
  state = { ...state, ...next };
  listeners.forEach((l) => l());
}

function cache(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage unavailable; the backend still has it */
  }
}

function normalizeThemes(raw: unknown): Theme[] | null {
  if (!Array.isArray(raw)) return null;
  const themes = raw.filter(
    (t): t is Theme =>
      !!t && typeof t === "object" && typeof t.id === "string" && typeof t.name === "string" && !!t.colors && typeof t.colors === "object",
  );
  return themes.length ? themes : null;
}

/** Change the theme everywhere at once; it is saved shortly after the last change. */
export function setAppearance(a: Appearance): void {
  generation++;
  applyAppearance(a);
  cache(CACHE_KEY, a);
  emit({ appearance: a, status: "saving", error: null });
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    const toSave = state.appearance;
    // One save at a time, in order, so an older save can't land after a newer one.
    saving = saving.then(() =>
      api.saveAppearance(toSave).then(
        () => {
          generation++;
          if (state.appearance === toSave) emit({ status: "saved" });
        },
        (e: unknown) => emit({ status: "error", error: e instanceof Error ? e.message : String(e) }),
      ),
    );
  }, SAVE_DELAY_MS);
}

/** Use a new theme list (after creating, renaming or deleting one). */
export function setThemes(themes: Theme[]): void {
  cache(THEMES_CACHE_KEY, themes);
  emit({ themes });
  applyAppearance(state.appearance, themes);
}

/** Fetch the themes and the saved appearance; apply what changed unless the user is mid-edit here. */
async function sync(initial: boolean): Promise<void> {
  const started = generation;
  const [themes, appearance] = await Promise.all([
    api.themes().then(normalizeThemes, () => null),
    api.appearance().then(normalizeAppearance, () => null),
  ]);
  if (themes && JSON.stringify(themes) !== JSON.stringify(state.themes)) {
    cache(THEMES_CACHE_KEY, themes);
    emit({ themes });
  }
  const busy = state.status === "saving" || generation !== started || (initial && state.status !== "idle");
  if (appearance && !busy && JSON.stringify(appearance) !== JSON.stringify(state.appearance)) {
    cache(CACHE_KEY, appearance);
    emit({ appearance });
  }
  applyAppearance(state.appearance, state.themes);
}

/** Apply the cached theme now, then the saved one from the backend. Resolves when that's done. */
export async function loadAppearance(): Promise<void> {
  try {
    const themes = normalizeThemes(JSON.parse(localStorage.getItem(THEMES_CACHE_KEY) ?? "null"));
    if (themes) emit({ themes });
    const cached = localStorage.getItem(CACHE_KEY);
    if (cached) emit({ appearance: normalizeAppearance(JSON.parse(cached)) });
  } catch {
    /* bad cache: ignore it */
  }
  applyAppearance(state.appearance);
  if (pollTimer === undefined) {
    pollTimer = setInterval(() => {
      if (document.visibilityState === "visible") void sync(false);
    }, POLL_MS);
  }
  await sync(true);
}

const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => listeners.delete(l);
};

export function useAppearance(): State {
  return useSyncExternalStore(subscribe, () => state);
}
