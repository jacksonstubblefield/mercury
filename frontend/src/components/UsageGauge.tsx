import { timeAgo } from "../hooks";
import type { ClaudeUsage, UsageWindow } from "../types";
import type { ClaudeUsageState } from "../usage";
import { countdown, hasReset, level } from "./UsagePage";

const SIZE = 40;
const STROKE = 4;
const R = (SIZE - STROKE) / 2;
const CIRC = 2 * Math.PI * R;

/** The window the ring shows: the 5-hour session limit, else whatever Anthropic reported first. */
function primaryWindow(u: ClaudeUsage | undefined): UsageWindow | null {
  if (u?.status !== "ok") return null;
  return u.windows.find((w) => w.key === "five_hour") ?? u.windows[0] ?? null;
}

function tooltip(u: ClaudeUsage | undefined, error: string | null | undefined): string {
  if (!u) return error ? `Can't load usage: ${error}` : "Loading Claude Code usage…";
  if (u.status !== "ok") return u.message ?? "Claude Code usage is unavailable.";
  if (u.windows.length === 0) return "Anthropic didn't report any plan limits.";
  const reset = (at: string) => (hasReset(at) ? "reset since this reading" : `resets ${countdown(at)}`);
  const lines = u.windows.map(
    (w) => `${w.label}: ${Math.round(w.utilization)}%${w.resets_at ? ` · ${reset(w.resets_at)}` : ""}`,
  );
  const stale = u.message ? ["", `Updated ${timeAgo(u.fetched_at)}. ${u.message}`] : [];
  return ["Claude Code usage", ...lines, ...stale, "", "Click for details"].join("\n");
}

/** How far through a window we are, 0-1, or null without a reset time. */
function elapsed(w: UsageWindow): number | null {
  if (!w.resets_at) return null;
  const end = new Date(w.resets_at).getTime();
  const start = end - w.window_seconds * 1000;
  return Math.min(1, Math.max(0, (Date.now() - start) / (end - start)));
}

/** A radial gauge of Claude Code plan usage, in the top right of the app. A full ring is 100%. */
export function UsageGauge({ usage, onOpen }: { usage: ClaudeUsageState; onOpen: () => void }) {
  const w = primaryWindow(usage.data);
  const pct = w ? Math.min(100, w.utilization) : 0;
  const t = w ? elapsed(w) : null;
  // The ring fills counterclockwise from 12 o'clock. The elapsed-time marker follows the same
  // path (SVG angles grow clockwise because y points down).
  const angle = t !== null ? -Math.PI / 2 - t * 2 * Math.PI : 0;
  const c = SIZE / 2;

  return (
    <button
      className={`usage-gauge ${w ? level(w.utilization) : "idle"}`}
      onClick={onOpen}
      title={tooltip(usage.data, usage.error)}
      aria-label={w ? `${w.label} usage ${Math.round(w.utilization)}%` : "Claude Code usage"}
    >
      <svg width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`} aria-hidden>
        <circle className="usage-gauge-track" cx={c} cy={c} r={R} strokeWidth={STROKE} />
        {w && (
          <circle
            className="usage-gauge-fill"
            cx={c}
            cy={c}
            r={R}
            strokeWidth={STROKE}
            strokeDasharray={CIRC}
            strokeDashoffset={CIRC * (1 - pct / 100)}
            // A circle's stroke starts at 3 o'clock and runs clockwise. Rotating it -90° starts it
            // at 12 o'clock; mirroring that left-right keeps the start there but runs it counterclockwise.
            transform={`translate(${SIZE} 0) scale(-1 1) rotate(-90 ${c} ${c})`}
          />
        )}
        {t !== null && (
          <circle className="usage-gauge-tick" cx={c + R * Math.cos(angle)} cy={c + R * Math.sin(angle)} r={1.6} />
        )}
      </svg>
      <span className="usage-gauge-label">{w ? Math.round(w.utilization) : "–"}</span>
    </button>
  );
}
