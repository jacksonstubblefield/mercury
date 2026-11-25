import { useCallback, useState } from "react";
import { api } from "../api";
import { useLoader, usePolling } from "../hooks";
import { MODES, modeInfo } from "../permissions";
import { AGENT_MODELS, modelInfo } from "../agentModels";
import type { AgentModel, AgentSettingsAccess, PermissionMode, Preferences, SessionOpenScroll } from "../types";

// Agents can change settings too (get_settings / update_settings), so keep the page current.
const POLL_MS = 5000;

const ACCESS: { value: AgentSettingsAccess; label: string; hint: string }[] = [
  {
    value: "change",
    label: "Read and change",
    hint: "Agents can read settings and change them when you ask. In Full auto changes apply straight away; otherwise you approve each one in the session.",
  },
  { value: "read", label: "Read only", hint: "Agents can see the settings but not change them." },
  { value: "off", label: "No access", hint: "Agents can't see or change the settings." },
];

const OPEN_SCROLL: { value: SessionOpenScroll; label: string; hint: string }[] = [
  { value: "top", label: "Start at the top", hint: "Sessions open scrolled to the top." },
  {
    value: "last_message",
    label: "Start at my last message (default)",
    hint: "Sessions open at the last message you sent, with Claude's reply below it.",
  },
  {
    value: "bottom",
    label: "Start at the bottom",
    hint: "Sessions open scrolled to the bottom, where the latest output, summary or question is.",
  },
];

type SaveStatus ="idle" | "saving" | "saved" | "error";

/** App-wide settings. Theme settings live on the Appearance page. */
export function SettingsPage() {
  const prefs = useLoader(() => api.preferences(), []);
  const [status, setStatus] = useState<SaveStatus>("idle");
  const [error, setError] = useState<string | null>(null);

  const { reload, setData } = prefs;
  const refresh = useCallback(() => {
    if (status !== "saving") void reload();
  }, [reload, status]);
  usePolling(refresh, POLL_MS);

  async function change(changes: Partial<Preferences>) {
    if (prefs.data) setData({ ...prefs.data, ...changes });
    setStatus("saving");
    try {
      setData(await api.updatePreferences(changes));
      setStatus("saved");
      setError(null);
    } catch (e) {
      setStatus("error");
      setError(e instanceof Error ? e.message : String(e));
      void reload();
    }
  }

  const p = prefs.data;
  return (
    <>
      <header className="project-header" style={{ paddingBottom: 16 }}>
        <h1>Settings</h1>
        <div className="meta">
          <span>App-wide settings for Mercury</span>
          {status === "saving" && <span className="faint">Saving…</span>}
          {status === "saved" && <span className="faint">Saved</span>}
          {status === "error" && <span className="error-text">Couldn't save: {error}</span>}
        </div>
      </header>
      <section className="content">
        {!p ? (
          prefs.error ? <p className="error-text">Couldn't load settings: {prefs.error}</p> : <p className="faint">Loading…</p>
        ) : (
          <div className="settings">
            <div className="card">
              <h3>Agent sessions</h3>
              <label htmlFor="default-model">Default model for new sessions</label>
              <select
                id="default-model"
                value={p.default_model}
                onChange={(e) => void change({ default_model: e.target.value as AgentModel })}
              >
                {AGENT_MODELS.map((m) => (
                  <option key={m.value} value={m.value}>
                    {m.label}
                  </option>
                ))}
              </select>
              <p className="faint settings-hint">
                {modelInfo(p.default_model)?.hint} Projects can override this in Project settings, and you can pick
                another model for each session.
              </p>
              <label htmlFor="default-mode">Default permissions for new sessions</label>
              <select
                id="default-mode"
                value={p.default_permission_mode}
                onChange={(e) => void change({ default_permission_mode: e.target.value as PermissionMode })}
              >
                {MODES.map((m) => (
                  <option key={m.value} value={m.value}>
                    {m.label}
                  </option>
                ))}
              </select>
              <p className="faint settings-hint">
                {modeInfo(p.default_permission_mode)?.hint} Projects can override this in Project settings.
              </p>
              <label htmlFor="open-scroll">When you open a session</label>
              <select
                id="open-scroll"
                value={p.session_open_scroll}
                onChange={(e) => void change({ session_open_scroll: e.target.value as SessionOpenScroll })}
              >
                {OPEN_SCROLL.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
              <p className="faint settings-hint">
                {OPEN_SCROLL.find((o) => o.value === p.session_open_scroll)?.hint} Links to a specific message still
                open at that message.
              </p>
              <label className="settings-choice" style={{ marginTop: 12 }}>
                <input
                  type="checkbox"
                  checked={p.agent_notifications}
                  onChange={(e) => void change({ agent_notifications: e.target.checked })}
                />
                <span>
                  <b>Desktop notifications</b>
                  <span className="faint">
                    Notify me when a session finishes or needs my answer while Mercury is in the background.
                  </span>
                </span>
              </label>
            </div>

            <div className="card">
              <h3>Agent access to settings</h3>
              <div className="settings-choices" role="radiogroup" aria-label="Agent access to settings">
                {ACCESS.map((a) => (
                  <label key={a.value} className={`settings-choice ${p.agent_settings_access === a.value ? "active" : ""}`}>
                    <input
                      type="radio"
                      name="agent-settings-access"
                      checked={p.agent_settings_access === a.value}
                      onChange={() => void change({ agent_settings_access: a.value })}
                    />
                    <span>
                      <b>{a.label}</b>
                      <span className="faint">{a.hint}</span>
                    </span>
                  </label>
                ))}
              </div>
              <p className="faint settings-hint">Only you can change this setting, so an agent can't give itself more access.</p>
            </div>
          </div>
        )}
      </section>
    </>
  );
}
