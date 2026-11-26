import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { ACTIVE_STATUSES } from "../sessions";
import type { AgentRun, Project } from "../types";
import { Markdown } from "./Markdown";

type Target = "board" | "docs";

const POLL_MS = 2000;

const HINTS: Record<Target, string> = {
  board:
    "An agent merges duplicate tasks, gives vague tasks clear titles and descriptions, splits bundled ones and orders " +
    "the backlog and todo columns. It doesn't move tasks between columns, and nothing you wrote is lost.",
  docs:
    "An agent files docs into collections, combines duplicates (the originals are kept, marked superseded), retitles " +
    "vague docs and archives empty ones. Nothing is deleted.",
};

interface Props {
  project: Project;
  target: Target;
  /** Reload what's on screen: called while the organizer works and when it finishes. */
  onChanged: () => void;
  onOpenRun: (runId: number) => void;
  /** Called once when an organizer run finishes (for views that cache what they show). */
  onFinished?: () => void;
}

/** The Organize button for the board or the docs, the run's progress, and its summary when done. */
export function Organizer({ project, target, onChanged, onOpenRun, onFinished }: Props) {
  const [run, setRun] = useState<AgentRun | null>(null);
  const [finished, setFinished] = useState<AgentRun | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const changed = useRef(onChanged);
  changed.current = onChanged;
  const done = useRef(onFinished);
  done.current = onFinished;

  const active = !!run && ACTIVE_STATUSES.has(run.status);

  // Pick up an organizer that's already running (started earlier, or in another window).
  useEffect(() => {
    let cancelled = false;
    setRun(null);
    setFinished(null);
    api
      .runs(project.id)
      .then((runs) => {
        const running = runs.find((r) => r.purpose === `organize_${target}` && ACTIVE_STATUSES.has(r.status));
        if (!cancelled && running) setRun(running);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [project.id, target]);

  const poll = useCallback(async () => {
    if (!run) return;
    try {
      const latest = await api.run(run.id);
      changed.current();
      if (ACTIVE_STATUSES.has(latest.status)) {
        setRun(latest);
        return;
      }
      setRun(null);
      setFinished(latest);
      done.current?.();
    } catch {
      /* try again next tick */
    }
  }, [run]);

  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => void poll(), POLL_MS);
    return () => window.clearInterval(id);
  }, [active, poll]);

  async function start() {
    setStarting(true);
    setError(null);
    setFinished(null);
    try {
      setRun(await api.organize(project.id, target));
      changed.current();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setStarting(false);
    }
  }

  return (
    <div className="organizer">
      <div className="row organizer-bar">
        <button
          className="btn small organizer-btn"
          disabled={active || starting}
          title={HINTS[target]}
          onClick={() => void start()}
        >
          {active ? (
            <>
              <span className="organizer-spin" aria-hidden /> Organizing…
            </>
          ) : (
            "✨ Organize"
          )}
        </button>
        {active && run && (
          <>
            <button className="btn small" title="Open the organizer's session" onClick={() => onOpenRun(run.id)}>
              Watch
            </button>
            {run.status === "waiting_approval" && <span className="faint">waiting for your approval</span>}
          </>
        )}
        {error && <span className="error-text">{error}</span>}
      </div>
      {finished && <OrganizeResult run={finished} onOpen={() => onOpenRun(finished.id)} onDismiss={() => setFinished(null)} />}
    </div>
  );
}

function OrganizeResult({ run, onOpen, onDismiss }: { run: AgentRun; onOpen: () => void; onDismiss: () => void }) {
  const ok = run.status === "succeeded";
  const heading = ok ? "Organized" : run.status === "cancelled" ? "Organizing was cancelled" : "Organizing failed";
  const body = ok ? run.result : run.error;
  return (
    <div className={`card organizer-result ${ok ? "" : "failed"}`}>
      <div className="row">
        <strong>{heading}</strong>
        {run.cost_usd != null && <span className="faint">${run.cost_usd.toFixed(2)}</span>}
        <span className="spacer" />
        <button className="btn ghost small" onClick={onOpen}>
          Open session
        </button>
        <button className="btn ghost small" onClick={onDismiss} title="Dismiss">
          ✕
        </button>
      </div>
      {body?.trim() && (
        <div className="organizer-summary">
          <Markdown>{body}</Markdown>
        </div>
      )}
    </div>
  );
}
