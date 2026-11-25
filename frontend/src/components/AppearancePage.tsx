import { useEffect, useState } from "react";
import {
  DEFAULT_APPEARANCE,
  DEFAULT_FONT_MONO,
  DEFAULT_FONT_UI,
  DEFAULT_RADIUS,
  DERIVED_TOKENS,
  TOKEN_GROUPS,
  findTheme,
  isHex,
  isLight,
  normalizeAppearance,
  paletteFromColor,
  resolveColors,
  setAppearance,
  useAppearance,
  type ColorToken,
  type TokenInfo,
} from "../theme";
import type { Appearance, Theme } from "../types";

const UI_FONTS = ['"Segoe UI", system-ui, sans-serif', "Inter, sans-serif", "system-ui, sans-serif", "Verdana, sans-serif", "Georgia, serif"];
const MONO_FONTS = ['"Cascadia Code", monospace', '"JetBrains Mono", monospace', "Consolas, monospace", '"Fira Code", monospace'];

export function AppearancePage() {
  const { appearance: a, themes, status, error } = useAppearance();
  const colors = resolveColors(a, themes);
  const preset = findTheme(themes, a.preset);
  const changed = Object.keys(a.colors).length;
  const update = (patch: Partial<Appearance>) => setAppearance({ ...a, ...patch });

  const setColor = (key: ColorToken, value: string | null) => {
    const next = { ...a.colors };
    if (value === null) delete next[key];
    else next[key] = value.toLowerCase();
    update({ colors: next });
  };

  return (
    <>
      <header className="project-header" style={{ paddingBottom: 16 }}>
        <h1>Appearance</h1>
        <div className="meta">
          <span>
            {preset.name}
            {changed > 0 && ` · ${changed} color${changed === 1 ? "" : "s"} changed`}
          </span>
          {status === "saving" && <span className="faint">Saving…</span>}
          {status === "saved" && <span className="faint">Saved</span>}
          {status === "error" && <span className="error-text">Couldn't save: {error}</span>}
        </div>
      </header>
      <section className="content">
        <div className="appearance">
          <div className="appearance-main">
            <div className="card">
              <h3>Theme</h3>
              <div className="preset-grid">
                {themes.map((p) => (
                  <PresetTile
                    key={p.id}
                    preset={p}
                    active={p.id === a.preset}
                    customized={p.id === a.preset && changed > 0}
                    onPick={() => update({ preset: p.id, colors: {} })}
                  />
                ))}
              </div>
              {changed > 0 && (
                <p className="faint appearance-note">
                  Your color changes sit on top of {preset.name}. Picking a theme clears them.{" "}
                  <button className="btn small ghost" onClick={() => update({ colors: {} })}>
                    Clear changes
                  </button>
                </p>
              )}
            </div>

            <OneColorCard current={colors["--accent"]} onApply={(preset, palette) => update({ preset, colors: palette })} />

            <div className="card">
              <h3>Colors</h3>
              <p className="faint appearance-note" style={{ marginTop: -4 }}>
                Changes show everywhere right away. A color you haven't changed follows the theme.
              </p>
              {TOKEN_GROUPS.map((g) => (
                <div key={g.title} className="token-group">
                  <div className="token-group-title">{g.title}</div>
                  {g.tokens.map((t) => (
                    <ColorRow
                      key={t.key}
                      token={t}
                      value={colors[t.key]}
                      overridden={t.key in a.colors}
                      onChange={(v) => setColor(t.key, v)}
                      onReset={() => setColor(t.key, null)}
                    />
                  ))}
                </div>
              ))}
            </div>

            <div className="card">
              <h3>Fonts and shape</h3>
              <FontField
                label="Interface font"
                value={a.font_ui}
                fallback={DEFAULT_FONT_UI}
                options={UI_FONTS}
                onChange={(v) => update({ font_ui: v })}
              />
              <FontField
                label="Code font"
                value={a.font_mono}
                fallback={DEFAULT_FONT_MONO}
                options={MONO_FONTS}
                onChange={(v) => update({ font_mono: v })}
              />
              <label htmlFor="appearance-radius">Corner roundness</label>
              <div className="row">
                <input
                  id="appearance-radius"
                  type="range"
                  min={0}
                  max={16}
                  value={a.radius ?? DEFAULT_RADIUS}
                  onChange={(e) => update({ radius: Number(e.target.value) })}
                  style={{ flex: 1, padding: 0 }}
                />
                <span className="usage-pct" style={{ width: 40, textAlign: "right" }}>
                  {a.radius ?? DEFAULT_RADIUS}px
                </span>
                <button className="btn small ghost" disabled={a.radius === null} onClick={() => update({ radius: null })}>
                  Reset
                </button>
              </div>
            </div>

            <JsonCard appearance={a} onApply={setAppearance} />
          </div>

          <aside className="appearance-preview">
            <Preview />
          </aside>
        </div>
      </section>
    </>
  );
}

function PresetTile({ preset, active, customized, onPick }: { preset: Theme; active: boolean; customized: boolean; onPick: () => void }) {
  const c = preset.colors;
  return (
    <button className={`preset-tile ${active ? "active" : ""}`} onClick={onPick} title={`Use ${preset.name}`}>
      <span className="preset-swatch" style={{ background: c["--bg"], borderColor: c["--border"] }}>
        <span className="preset-side" style={{ background: c["--bg-elev"], borderColor: c["--border"] }}>
          <span style={{ background: c["--text-faint"] }} />
          <span style={{ background: c["--bg-elev-2"] }} />
          <span style={{ background: c["--text-faint"] }} />
        </span>
        <span className="preset-body">
          <span style={{ background: c["--text"], width: "70%" }} />
          <span style={{ background: c["--text-dim"], width: "50%" }} />
          <span className="preset-btn" style={{ background: c["--accent"] }} />
        </span>
      </span>
      <span className="preset-name">
        {preset.name}
        {customized && <span className="faint"> · changed</span>}
      </span>
    </button>
  );
}

function OneColorCard({ current, onApply }: { current: string; onApply: (preset: string, palette: Record<string, string>) => void }) {
  const [color, setColor] = useState(current);
  const [mode, setMode] = useState<"dark" | "light">("dark");
  const [tint, setTint] = useState(60);
  const palette = paletteFromColor(color, mode, tint / 100);

  return (
    <div className="card">
      <h3>Start from one color</h3>
      <p className="faint appearance-note" style={{ marginTop: -4 }}>
        Build a whole theme around a single color: it becomes the accent and tints everything else.
      </p>
      <div className="one-color">
        <ColorInput value={color} onChange={setColor} />
        <div className="seg" style={{ padding: 0 }}>
          {(["dark", "light"] as const).map((m) => (
            <button key={m} className={mode === m ? "active" : ""} onClick={() => setMode(m)}>
              {m === "dark" ? "Dark" : "Light"}
            </button>
          ))}
        </div>
        <label className="one-color-tint">
          Tint
          <input type="range" min={0} max={100} value={tint} onChange={(e) => setTint(Number(e.target.value))} />
        </label>
        <span className="one-color-strip" aria-hidden="true">
          {(["--bg", "--bg-elev", "--bg-elev-2", "--border", "--text-dim", "--text", "--accent"] as const).map((k) => (
            <span key={k} style={{ background: palette[k] }} />
          ))}
        </span>
        <button className="btn primary" onClick={() => onApply(mode === "dark" ? "mercury-dark" : "mercury-light", palette)}>
          Apply
        </button>
      </div>
    </div>
  );
}

/** A swatch that opens the system color picker, plus a hex field. */
function ColorInput({ value, onChange }: { value: string; onChange: (hex: string) => void }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  return (
    <span className="color-input">
      <input type="color" value={value} onChange={(e) => onChange(e.target.value)} aria-label="Pick a color" />
      <input
        className="hex"
        value={draft}
        spellCheck={false}
        maxLength={7}
        onChange={(e) => {
          const v = e.target.value.trim();
          const hex = v.startsWith("#") ? v : `#${v}`;
          setDraft(v);
          if (isHex(hex)) onChange(hex.toLowerCase());
        }}
        onBlur={() => setDraft(value)}
        aria-label="Hex color"
      />
    </span>
  );
}

function ColorRow({
  token,
  value,
  overridden,
  onChange,
  onReset,
}: {
  token: TokenInfo;
  value: string;
  overridden: boolean;
  onChange: (hex: string) => void;
  onReset: () => void;
}) {
  const auto = (DERIVED_TOKENS as string[]).includes(token.key);
  return (
    <div className="token-row">
      <ColorInput value={value} onChange={onChange} />
      <div className="token-label">
        <div>
          {token.label}
          {overridden ? <span className="token-tag changed">changed</span> : auto && <span className="token-tag">auto</span>}
        </div>
        <div className="faint">{token.hint}</div>
      </div>
      <button className="btn small ghost" disabled={!overridden} onClick={onReset} title="Back to the theme's color">
        Reset
      </button>
    </div>
  );
}

function FontField({
  label,
  value,
  fallback,
  options,
  onChange,
}: {
  label: string;
  value: string | null;
  fallback: string;
  options: string[];
  onChange: (v: string | null) => void;
}) {
  const id = `font-${label.replace(/\W+/g, "-").toLowerCase()}`;
  const [draft, setDraft] = useState(value ?? "");
  useEffect(() => setDraft(value ?? ""), [value]);
  const commit = (v: string) => {
    const clean = v.replace(/[;{}<>\\]/g, "").trim();
    onChange(clean ? clean : null);
  };
  return (
    <>
      <label htmlFor={id}>{label}</label>
      <div className="row">
        <input
          id={id}
          list={`${id}-list`}
          value={draft}
          placeholder={fallback}
          onChange={(e) => {
            setDraft(e.target.value);
            commit(e.target.value);
          }}
          style={{ fontFamily: value ?? fallback }}
        />
        <button className="btn small ghost" disabled={value === null} onClick={() => onChange(null)}>
          Reset
        </button>
      </div>
      <datalist id={`${id}-list`}>
        {options.map((o) => (
          <option key={o} value={o} />
        ))}
      </datalist>
    </>
  );
}

function JsonCard({ appearance, onApply }: { appearance: Appearance; onApply: (a: Appearance) => void }) {
  const json = JSON.stringify(appearance, null, 2);
  const [draft, setDraft] = useState<string | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(json);
      setMessage({ ok: true, text: "Copied." });
    } catch {
      setMessage({ ok: false, text: "Couldn't reach the clipboard. Select the text below and copy it." });
      setDraft(json);
    }
  };

  const apply = () => {
    try {
      onApply(normalizeAppearance(JSON.parse(draft ?? json)));
      setDraft(null);
      setMessage({ ok: true, text: "Applied." });
    } catch (e) {
      setMessage({ ok: false, text: `Not valid JSON: ${e instanceof Error ? e.message : String(e)}` });
    }
  };

  return (
    <div className="card">
      <h3>Share and reset</h3>
      <p className="faint appearance-note" style={{ marginTop: -4 }}>
        A theme is a small piece of JSON. Copy it to share it, or paste one in.
      </p>
      <div className="row" style={{ flexWrap: "wrap" }}>
        <button className="btn" onClick={() => void copy()}>
          Copy theme JSON
        </button>
        <button className="btn" onClick={() => setDraft(draft === null ? json : null)}>
          {draft === null ? "Edit JSON…" : "Close editor"}
        </button>
        <span className="spacer" />
        <button
          className="btn danger"
          onClick={() => {
            if (confirm("Reset colors, fonts and corners to Mercury Dark?")) onApply(DEFAULT_APPEARANCE);
          }}
        >
          Reset everything
        </button>
      </div>
      {draft !== null && (
        <>
          <textarea
            className="appearance-json"
            value={draft}
            spellCheck={false}
            onChange={(e) => setDraft(e.target.value)}
            rows={10}
          />
          <div className="row" style={{ marginTop: 8 }}>
            <button className="btn primary" onClick={apply}>
              Apply JSON
            </button>
          </div>
        </>
      )}
      {message && <p className={message.ok ? "ok-text appearance-note" : "error-text appearance-note"}>{message.text}</p>}
    </div>
  );
}

/** Real app components in the current theme, so you can see a change before going back to work. */
function Preview() {
  const { appearance } = useAppearance();
  const light = isLight(resolveColors(appearance)["--bg"]);
  return (
    <div className="card preview">
      <h3>Preview</h3>
      <div className="tabs" style={{ marginTop: 0, borderBottom: "1px solid var(--border)" }}>
        <button className="tab active">Overview</button>
        <button className="tab">Board</button>
        <button className="tab">Agents</button>
      </div>
      <div className="preview-body">
        <div className="row" style={{ flexWrap: "wrap" }}>
          <button className="btn primary">Start session</button>
          <button className="btn">Cancel</button>
          <button className="btn danger">Delete</button>
        </div>
        <input placeholder="Search docs…" />
        <div className="task-card">
          Tidy the release checklist
          <div className="t-meta">
            <span className="prio low">low</span>
            <span className="prio medium">medium</span>
            <span className="prio high">high</span>
            <span className="prio urgent">urgent</span>
          </div>
        </div>
        <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
          <span className="status running">running</span>
          <span className="status waiting_approval">needs approval</span>
          <span className="status succeeded">succeeded</span>
          <span className="status failed">failed</span>
          <span className="badge live">● 2</span>
          <span className="badge">5</span>
        </div>
        <div>
          Body text with a <a href="#preview" onClick={(e) => e.preventDefault()}>link</a>, <span className="muted">secondary</span>{" "}
          and <span className="faint">faint</span> text, and <code>inline code</code>.
        </div>
        <div className="ev-tool">
          <table className="diff">
            <tbody>
              <tr className="d-hunk"><td colSpan={2}>@@ -1,2 +1,2 @@</td></tr>
              <tr className="d-del"><td className="ln">1</td><td className="code"><span className="sign">-</span>color: "indigo";</td></tr>
              <tr className="d-add"><td className="ln">1</td><td className="code"><span className="sign">+</span>color: "hot pink";</td></tr>
            </tbody>
          </table>
        </div>
        <div className="ev-error">Something went wrong in this run.</div>
        <div className="faint" style={{ fontSize: 12 }}>{light ? "Light" : "Dark"} theme: native controls and scrollbars follow it.</div>
      </div>
    </div>
  );
}
