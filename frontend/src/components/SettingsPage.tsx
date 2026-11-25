import { useCallback, useState } from "react";
import { api } from "../api";
import { useLoader, usePolling } from "../hooks";
import { MODES, modeInfo } from "../permissions";
import type { AgentSettingsAccess, PermissionMode, Preferences } from "../types";

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

type SaveStatus = "idle" | "saving" | "saved" | "error";

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
