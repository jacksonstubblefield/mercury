import { useState } from "react";
import { api } from "../api";
import { timeAgo, useLoader, usePolling } from "../hooks";
import type { UsageWindow } from "../types";

const POLL_MS = 60_000;

function resetLabel(iso: string): string {
  const at = new Date(iso);
  const now = new Date();
  const time = at.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  if (at.toDateString() === now.toDateString()) return `Resets at ${time}`;
  const day = at.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
  return `Resets ${day} at ${time}`;
}

export function countdown(iso: string): string {
  const s = Math.max(0, Math.round((new Date(iso).getTime() - Date.now()) / 1000));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return `in ${d}d ${h}h`;
  if (h > 0) return `in ${h}h ${m}m`;
  return `in ${m}m`;
}

export function level(pct: number): "ok" | "warn" | "crit" {
  return pct >= 90 ? "crit" : pct >= 75 ? "warn" : "ok";
}

function UsageBar({ w }: { w: UsageWindow }) {
  const pct = Math.min(100, w.utilization);
  // How far through the window we are, so you can see whether usage is ahead of time.
  let elapsed: number | null = null;
  if (w.resets_at) {
    const end = new Date(w.resets_at).getTime();
    const start = end - w.window_seconds * 1000;
    elapsed = Math.min(1, Math.max(0, (Date.now() - start) / (end - start))) * 100;
  }
  return (
    <div className="usage-window">
      <div className="usage-row">
        <span>{w.label}</span>
        <span className="usage-pct">{Math.round(w.utilization)}%</span>
      </div>
      <div
        className={`usage-bar ${level(w.utilization)}`}
        role="progressbar"
        aria-label={w.label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(pct)}
      >
        <div className="usage-fill" style={{ width: `${pct}%` }} />
        {elapsed !== null && (
          <div
            className="usage-tick"
            style={{ left: `${elapsed}%` }}
            title={`${Math.round(elapsed)}% of this window has passed`}
          />
        )}
      </div>
      {w.resets_at && (
        <div className="usage-reset faint">
          {resetLabel(w.resets_at)} · {countdown(w.resets_at)}
        </div>
      )}
    </div>
  );
}

export function UsagePage() {
  const usage = useLoader(() => api.claudeUsage(), []);
  usePolling(usage.reload, POLL_MS);
  const [refreshing, setRefreshing] = useState(false);

  const refresh = async () => {
    setRefreshing(true);
    try {
      usage.setData(await api.claudeUsage(true));
    } catch {
      await usage.reload();
    } finally {
      setRefreshing(false);
    }
  };

  const data = usage.data;
  return (
    <>
      <header className="project-header" style={{ paddingBottom: 16 }}>
        <h1>Usage</h1>
        <div className="meta">
          <span>Claude Code{data?.plan ? ` · ${data.plan[0].toUpperCase()}${data.plan.slice(1)} plan` : ""}</span>
          {data?.fetched_at && <span className="faint">Updated {timeAgo(data.fetched_at)}</span>}
        </div>
      </header>
      <section className="content">
        <div className="usage-page">
          <div className="card">
            <div className="row" style={{ marginBottom: 14 }}>
              <h3 style={{ margin: 0 }}>Plan limits</h3>
              <span className="spacer" />
              <button className="btn small" onClick={() => void refresh()} disabled={refreshing}>
                {refreshing ? "Refreshing…" : "Refresh"}
              </button>
            </div>

            {usage.error && !data && <p className="error-text">Can't load usage: {usage.error}</p>}
            {!data && !usage.error && <p className="faint">Loading…</p>}

            {data && data.status !== "ok" && (
              <p className={data.status === "error" ? "error-text" : "muted"}>
                {data.message}
                {data.status === "signed_out" && (
                  <>
                    {" "}
                    Run <code>claude</code> and use <code>/login</code> to sign in with a Pro or Max plan. Usage limits
                    don't apply to API-key billing.
                  </>
                )}
              </p>
            )}

            {data?.status === "ok" && data.windows.length === 0 && (
              <p className="muted">Anthropic didn't report any plan limits for this account.</p>
            )}
            {data?.status === "ok" && data.windows.map((w) => <UsageBar key={w.key} w={w} />)}
          </div>
          {data?.status === "ok" && data.windows.length > 0 && (
            <p className="faint usage-note">
              These limits cover everything on your Claude plan, including Claude chats and Mercury's agents when they
              run on this sign-in. The tick on each bar marks how much of that window has passed.
            </p>
          )}
        </div>
      </section>
    </>
  );
}
