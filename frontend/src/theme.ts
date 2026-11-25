// Themes: built-in presets, the color tokens they set, and a small store that applies the
// user's choice to <html> as CSS custom properties and saves it to the backend.
//
// styles.css declares the same tokens on :root with Mercury Dark's values, so the app looks right
// before this runs. Tints such as the diff backgrounds are derived in CSS with color-mix().

import { useSyncExternalStore } from "react";
import { api } from "./api";
import type { Appearance } from "./types";

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

// --------------------------------------------------------------------------- presets

export interface Preset {
  id: string;
  name: string;
  colors: Palette;
}

const STATUS_DARK = { "--ok": "#4fd18b", "--warn": "#f0b44c", "--err": "#ff6b6b" } as const;
const STATUS_LIGHT = { "--ok": "#1e8a50", "--warn": "#a86a00", "--err": "#d33a3a" } as const;

export const PRESETS: Preset[] = [
  {
    id: "mercury-dark",
    name: "Mercury Dark",
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
  },
  {
    id: "mercury-light",
    name: "Mercury Light",
    colors: {
      "--bg": "#fbfbfd",
      "--bg-elev": "#f1f2f6",
      "--bg-elev-2": "#e6e8ef",
      "--bg-hover": "#dfe2ea",
      "--border": "#d3d7e0",
      "--text": "#1d2130",
      "--text-dim": "#555d72",
      "--text-faint": "#868da0",
      "--accent": "#4f5fe0",
      ...STATUS_LIGHT,
    },
  },
  {
    id: "hot-pink",
    name: "Hot Pink",
    colors: {
      "--bg": "#1a0a14",
      "--bg-elev": "#250f1d",
      "--bg-elev-2": "#321428",
      "--bg-hover": "#3d1932",
      "--border": "#521f43",
      "--text": "#ffe8f4",
      "--text-dim": "#e9a6cb",
      "--text-faint": "#a86b8d",
      "--accent": "#ff3ea5",
      "--ok": "#5ee0a0",
      "--warn": "#ffc35c",
      "--err": "#ff7348",
    },
  },
  {
    id: "midnight",
    name: "Midnight",
    colors: {
      "--bg": "#0a0e17",
      "--bg-elev": "#111726",
      "--bg-elev-2": "#18203a",
      "--bg-hover": "#1f2947",
      "--border": "#263152",
      "--text": "#dce6f5",
      "--text-dim": "#8ea0bf",
      "--text-faint": "#5d6c8a",
      "--accent": "#4cc9f0",
      ...STATUS_DARK,
    },
  },
  {
    id: "forest",
    name: "Forest",
    colors: {
      "--bg": "#0d1310",
      "--bg-elev": "#131b17",
      "--bg-elev-2": "#1a251f",
      "--bg-hover": "#202f27",
      "--border": "#2a3b32",
      "--text": "#e1ebe4",
      "--text-dim": "#97ab9e",
      "--text-faint": "#66796d",
      "--accent": "#7fd49b",
      "--ok": "#b5e06a",
      "--warn": "#f0b44c",
      "--err": "#ff7b6b",
    },
  },
  {
    id: "ember",
    name: "Ember",
    colors: {
      "--bg": "#14100e",
      "--bg-elev": "#1c1613",
      "--bg-elev-2": "#251d19",
      "--bg-hover": "#2e241f",
      "--border": "#3a2e27",
      "--text": "#f0e6df",
      "--text-dim": "#b2a194",
      "--text-faint": "#7d6e63",
      "--accent": "#ff9f68",
      ...STATUS_DARK,
    },
  },
  {
    id: "paper",
    name: "Paper",
    colors: {
      "--bg": "#fffdf8",
      "--bg-elev": "#f6f2e9",
      "--bg-elev-2": "#ece6da",
      "--bg-hover": "#e5ddce",
      "--border": "#d9cfbd",
      "--text": "#2b2620",
      "--text-dim": "#6b6153",
      "--text-faint": "#9a8f7f",
      "--accent": "#c2410c",
      ...STATUS_LIGHT,
    },
  },
];

export const DEFAULT_PRESET = PRESETS[0];

export function findPreset(id: string): Preset {
  return PRESETS.find((p) => p.id === id) ?? DEFAULT_PRESET;
}

export const DEFAULT_APPEARANCE: Appearance = {
  preset: DEFAULT_PRESET.id,
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

/** Every color token's value for this appearance: preset, then the user's colors, then derived ones. */
export function resolveColors(a: Appearance): Record<ColorToken, string> {
  const base = { ...findPreset(a.preset).colors };
  for (const k of Object.keys(base) as BaseToken[]) {
    const v = a.colors[k];
    if (v && isHex(v)) base[k] = v;
  }
  const out = { ...base } as Record<ColorToken, string>;
  for (const k of DERIVED_TOKENS) {
    const v = a.colors[k];
    out[k] = v && isHex(v) ? v : derive(k, base);
  }
  return out;
}

export function applyAppearance(a: Appearance, root: HTMLElement = document.documentElement): void {
  const colors = resolveColors(a);
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
    preset: typeof o.preset === "string" ? findPreset(o.preset).id : DEFAULT_PRESET.id,
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
  status: SaveStatus;
  error: string | null;
}

// Remembered in this window so a reload in dev paints the theme at once. The backend copy is
// the real one: the packaged app gets a new origin (port) each launch, which empties localStorage.
const CACHE_KEY = "mercury.appearance";
const SAVE_DELAY_MS = 400;

let state: State = { appearance: DEFAULT_APPEARANCE, status: "idle", error: null };
const listeners = new Set<() => void>();
let saveTimer: ReturnType<typeof setTimeout> | undefined;
let saving: Promise<unknown> = Promise.resolve();

function emit(next: Partial<State>) {
  state = { ...state, ...next };
  listeners.forEach((l) => l());
}

function cache(a: Appearance) {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(a));
  } catch {
    /* storage unavailable; the backend still has it */
  }
}

/** Change the theme everywhere at once; it is saved shortly after the last change. */
export function setAppearance(a: Appearance): void {
  applyAppearance(a);
  cache(a);
  emit({ appearance: a, status: "saving", error: null });
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    const toSave = state.appearance;
    // One save at a time, in order, so an older save can't land after a newer one.
    saving = saving.then(() =>
      api.saveAppearance(toSave).then(
        () => state.appearance === toSave && emit({ status: "saved" }),
        (e: unknown) => emit({ status: "error", error: e instanceof Error ? e.message : String(e) }),
      ),
    );
  }, SAVE_DELAY_MS);
}

/** Apply the cached theme now, then the saved one from the backend. Resolves when that's done. */
export async function loadAppearance(): Promise<void> {
  try {
    const cached = localStorage.getItem(CACHE_KEY);
    if (cached) {
      const a = normalizeAppearance(JSON.parse(cached));
      applyAppearance(a);
      emit({ appearance: a });
    }
  } catch {
    /* bad cache: ignore it */
  }
  try {
    const a = normalizeAppearance(await api.appearance());
    if (state.status !== "idle") return; // the user already changed it in this window
    applyAppearance(a);
    cache(a);
    emit({ appearance: a });
  } catch {
    /* backend not up yet: keep the cached or default theme */
  }
}

const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => listeners.delete(l);
};

export function useAppearance(): State {
  return useSyncExternalStore(subscribe, () => state);
}
