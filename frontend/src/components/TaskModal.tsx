import { useMemo, useState } from "react";
import { api, openExternal } from "../api";
import { timeAgo, useLoader, usePolling } from "../hooks";
import { ACTIVE_STATUSES, groupSessions, plainExcerpt, sessionCost, sessionSummary, type Session } from "../sessions";
import { TASK_COLUMNS, type Task, type TaskPriority, type TaskStatus } from "../types";
import { Markdown } from "./Markdown";

interface Props {
  task: Task;
  canRunAgent: boolean;
  onClose: () => void;
  onSaved: () => void;
  onRunAgent: (task: Task) => void;
  onOpenRun: (runId: number) => void;
}

export function TaskModal({ task, canRunAgent, onClose, onSaved, onRunAgent, onOpenRun }: Props) {
  const [title, setTitle] = useState(task.title);
  const [description, setDescription] = useState(task.description);
  const [status, setStatus] = useState<TaskStatus>(task.status);
  const [priority, setPriority] = useState<TaskPriority>(task.priority);
  const [preview, setPreview] = useState(false);
  const dirty = title !== task.title || description !== task.description || status !== task.status || priority !== task.priority;

  const runs = useLoader(() => api.runs(task.project_id, task.id), [task.project_id, task.id]);
  const sessions = useMemo(() => groupSessions(runs.data ?? []), [runs.data]);
  usePolling(runs.reload, 3000, sessions.some((s) => ACTIVE_STATUSES.has(s.latest.status)));

  async function save(): Promise<Task> {
    return api.updateTask(task.id, { title, description, status, priority });
  }

  /** Open a session in the Agents tab, keeping any edits made here. */
  async function openSession(session: Session) {
    if (dirty && title.trim()) await save();
    onOpenRun(session.latest.id);
  }

  async function remove() {
    if (!confirm(`Delete task "${task.title}"?`)) return;
    await api.deleteTask(task.id);
    onSaved();
  }

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="row">
          <span className="faint">T-{task.id}</span>
          {task.github_url && (
            <a href="#" onClick={(e) => (e.preventDefault(), openExternal(task.github_url!))}>
              GitHub #{task.github_issue_number}
            </a>
          )}
        </div>
        <label>Title</label>
        <input className="task-title" value={title} onChange={(e) => setTitle(e.target.value)} />

        <div className="row">
          <div style={{ flex: 1 }}>
            <label>Status</label>
            <select value={status} onChange={(e) => setStatus(e.target.value as TaskStatus)}>
              {TASK_COLUMNS.map((c) => (
                <option key={c.status} value={c.status}>
                  {c.label}
                </option>
              ))}
            </select>
          </div>
          <div style={{ flex: 1 }}>
            <label>Priority</label>
            <select value={priority} onChange={(e) => setPriority(e.target.value as TaskPriority)}>
              {(["low", "medium", "high", "urgent"] as const).map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="row" style={{ marginTop: 10 }}>
          <label style={{ margin: 0 }}>Description (Markdown) — the agent sees this</label>
          <span className="spacer" />
          <button className="btn small ghost" onClick={() => setPreview(!preview)}>
            {preview ? "Edit" : "Preview"}
          </button>
        </div>
        {preview ? (
          <div className="card" style={{ marginTop: 4, minHeight: 80 }} onDoubleClick={() => setPreview(false)}>
            {description ? <Markdown>{description}</Markdown> : <span className="faint">Nothing yet.</span>}
          </div>
        ) : (
          <textarea style={{ minHeight: 220 }} value={description} onChange={(e) => setDescription(e.target.value)} />
        )}

        {sessions.length > 0 && (
          <>
            <label>
              Agent sessions on this task <span className="faint">({sessions.length})</span>
            </label>
            <div className="task-sessions">
              {sessions.map((s) => (
                <TaskSession key={s.rootId} session={s} task={task} onOpen={() => void openSession(s)} />
              ))}
            </div>
          </>
        )}
        {runs.error && <p className="error-text">Couldn't load this task's agent sessions: {runs.error}</p>}

        <div className="modal-actions">
          <button className="btn danger" onClick={remove}>
            Delete
          </button>
          <span className="spacer" />
          <button
            className="btn"
            disabled={!canRunAgent}
            title={canRunAgent ? "Hand this task to a Claude agent" : "Set a local repo folder in project settings first"}
            onClick={async () => onRunAgent(await save())}
          >
            ▶ Run agent
          </button>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn primary" disabled={!title.trim()} onClick={async () => (await save(), onSaved())}>
            Save
          </button>
        </div>
      </div>
    </div>
  );
}

const STATUS_NOTE: Partial<Record<string, string>> = {
  queued: "Starting…",
  running: "Claude is working on this now.",
  waiting_approval: "Waiting for your approval.",
  cancelled: "Stopped before Claude wrapped up.",
};

function TaskSession({ session, task, onOpen }: { session: Session; task: Task; onOpen: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const { runs, latest } = session;
  const cost = sessionCost(session);
  const summary = sessionSummary(session);
  const active = ACTIVE_STATUSES.has(latest.status);
  // "Run agent" seeds every session with the same prompt; only show prompts the user actually wrote.
  const opener = runs[0].prompt.startsWith(`Complete task T-${task.id}:`) ? null : runs[0].prompt;
  const followUps = runs.length - 1;
  const excerpt = summary.kind === "none" ? "" : plainExcerpt(summary.text);
  const truncated = summary.kind !== "none" && excerpt !== summary.text.replace(/\s+/g, " ").trim();

  return (
    <div
      className="task-session"
      role="button"
      tabIndex={0}
      title="Open this session in the Agents tab"
      onClick={onOpen}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onOpen())}
    >
      <div className="row" style={{ fontSize: 12 }}>
        <span className={`status ${latest.status}`}>{latest.status.replace("_", " ")}</span>
        <span className="task-session-title" title={runs[0].prompt}>
          {opener ?? <span className="faint">Agent run</span>}
        </span>
        <span className="spacer" />
        {followUps > 0 && <span className="faint">+{followUps} follow-up{followUps > 1 ? "s" : ""}</span>}
        {cost > 0 && <span className="faint">${cost.toFixed(2)}</span>}
        <span className="faint">{timeAgo(latest.finished_at ?? latest.created_at)}</span>
        <span className="task-session-open">Open →</span>
      </div>

      {(active || summary.kind === "none") && (
        <div className="task-session-summary faint">{STATUS_NOTE[latest.status] ?? "No summary was recorded."}</div>
      )}
      {!active && summary.kind !== "none" && (
        <div className={`task-session-summary ${summary.kind === "error" ? "error-text" : ""}`}>
          {expanded ? <Markdown>{summary.text}</Markdown> : excerpt}
          {truncated && (
            <button
              className="btn small ghost task-session-more"
              onClick={(e) => {
                e.stopPropagation();
                setExpanded(!expanded);
              }}
            >
              {expanded ? "Less" : "More"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
