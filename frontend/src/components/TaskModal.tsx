import { useMemo, useRef, useState } from "react";
import { api, openExternal } from "../api";
import { timeAgo, useLoader, usePolling } from "../hooks";
import { ACTIVE_STATUSES, groupSessions, plainExcerpt, runLabel, sessionCost, sessionSummary, type Session } from "../sessions";
import { taskRef } from "../refs";
import { TASK_COLUMNS, type Task, type TaskPriority, type TaskStatus } from "../types";
import { Markdown } from "./Markdown";
import { ImageStrip, MAX_IMAGES, MessageImages, usePastedImages } from "./PastedImages";

interface Props {
  task: Task;
  projectKey: string;
  canRunAgent: boolean;
  onClose: () => void;
  onSaved: () => void;
  onRunAgent: (task: Task) => void;
  onOpenRun: (runId: number) => void;
}

export function TaskModal({ task, projectKey, canRunAgent, onClose, onSaved, onRunAgent, onOpenRun }: Props) {
  const [title, setTitle] = useState(task.title);
  const [description, setDescription] = useState(task.description);
  const [status, setStatus] = useState<TaskStatus>(task.status);
  const [priority, setPriority] = useState<TaskPriority>(task.priority);
  const [preview, setPreview] = useState(false);
  // Pictures already saved on the task that are still kept, and new ones waiting for Save.
  const [kept, setKept] = useState<string[]>(task.images ?? []);
  const pasted = usePastedImages(MAX_IMAGES - kept.length, "one task");
  const fileInput = useRef<HTMLInputElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const imagesChanged = kept.join() !== (task.images ?? []).join();
  const dirty =
    title !== task.title ||
    description !== task.description ||
    status !== task.status ||
    priority !== task.priority ||
    imagesChanged ||
    pasted.images.length > 0;

  const runs = useLoader(() => api.runs(task.project_id, task.id), [task.project_id, task.id]);
  const sessions = useMemo(() => groupSessions(runs.data ?? []), [runs.data]);
  usePolling(runs.reload, 3000, sessions.some((s) => ACTIVE_STATUSES.has(s.latest.status)));

  /** Saves the task; null (with the error shown) if that failed. */
  async function save(): Promise<Task | null> {
    setSaving(true);
    setError(null);
    try {
      const saved = await api.updateTask(task.id, {
        title,
        description,
        status,
        priority,
        // Only send the list when it changed, so pictures added elsewhere meanwhile aren't dropped.
        ...(imagesChanged ? { images: kept } : {}),
        ...(pasted.images.length ? { add_images: pasted.payload } : {}),
      });
      pasted.clear();
      setKept(saved.images);
      return saved;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return null;
    } finally {
      setSaving(false);
    }
  }

  /** Open a session in the Agents tab, keeping any edits made here. */
  async function openSession(session: Session) {
    if (dirty && title.trim() && !(await save())) return;
    onOpenRun(session.latest.id);
  }

  async function remove() {
    if (!confirm(`Delete task "${task.title}"?`)) return;
    await api.deleteTask(task.id);
    onSaved();
  }

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      {/* Screenshots can be pasted or dropped anywhere in the modal. */}
      <div className="modal" {...pasted.handlers}>
        <div className="row">
          <span className="faint">{taskRef(projectKey, task)}</span>
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

        <div className="row" style={{ marginTop: 10 }}>
          <label style={{ margin: 0 }}>
            Pictures{kept.length + pasted.images.length > 0 && <span className="faint"> ({kept.length + pasted.images.length})</span>}{" "}
            — sent to the agent with the task
          </label>
          <span className="spacer" />
          <button
            className="btn small ghost"
            disabled={kept.length + pasted.images.length >= MAX_IMAGES}
            onClick={() => fileInput.current?.click()}
          >
            Add…
          </button>
          <input
            ref={fileInput}
            type="file"
            accept="image/*"
            multiple
            hidden
            onChange={(e) => {
              void pasted.add([...(e.target.files ?? [])]);
              e.target.value = ""; // so picking the same file again still fires
            }}
          />
        </div>
        {kept.length + pasted.images.length > 0 ? (
          <div className="task-images">
            <MessageImages names={kept} onRemove={(name) => setKept(kept.filter((n) => n !== name))} />
            <ImageStrip images={pasted.images} onRemove={pasted.remove} />
          </div>
        ) : (
          <p className="faint task-images-hint">Paste or drop screenshots anywhere here, or use Add….</p>
        )}
        {pasted.error && <p className="error-text">{pasted.error}</p>}

        {sessions.length > 0 && (
          <>
            <label>
              Agent sessions on this task <span className="faint">({sessions.length})</span>
            </label>
            <div className="task-sessions">
              {sessions.map((s) => (
                <TaskSession key={s.rootId} session={s} task={task} projectKey={projectKey} onOpen={() => void openSession(s)} />
              ))}
            </div>
          </>
        )}
        {runs.error && <p className="error-text">Couldn't load this task's agent sessions: {runs.error}</p>}
        {error && <p className="error-text">Couldn't save: {error}</p>}

        <div className="modal-actions">
          <button className="btn danger" onClick={remove}>
            Delete
          </button>
          <span className="spacer" />
          <button
            className="btn"
            disabled={!canRunAgent || saving}
            title={canRunAgent ? "Hand this task to a Claude agent" : "Set a local repo folder in project settings first"}
            onClick={async () => {
              const saved = await save();
              if (saved) onRunAgent(saved);
            }}
          >
            ▶ Run agent
          </button>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            className="btn primary"
            disabled={!title.trim() || saving}
            onClick={async () => {
              if (await save()) onSaved();
            }}
          >
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

function TaskSession({
  session,
  task,
  projectKey,
  onOpen,
}: {
  session: Session;
  task: Task;
  projectKey: string;
  onOpen: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const { runs, latest } = session;
  const cost = sessionCost(session);
  const summary = sessionSummary(session);
  const active = ACTIVE_STATUSES.has(latest.status);
  // "Run agent" seeds every session with the same prompt; only show prompts the user actually wrote.
  // (Sessions from before per-project ids were seeded with "Complete task T-<global id>:".)
  const seeded = [`Complete task ${taskRef(projectKey, task)}:`, `Complete task T-${task.id}:`];
  const opener = seeded.some((p) => runs[0].prompt.startsWith(p)) ? null : runLabel(runs[0]) || null;
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
        <span className="task-session-title" title={runLabel(runs[0])}>
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
