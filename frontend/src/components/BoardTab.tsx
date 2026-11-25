import { useState } from "react";
import { api } from "../api";
import { useLoader } from "../hooks";
import { taskRef } from "../refs";
import { TASK_COLUMNS, type Project, type Task, type TaskStatus } from "../types";
import { Organizer } from "./Organizer";
import { TaskModal } from "./TaskModal";

interface Props {
  project: Project;
  onRunAgent: (task: Task) => void;
  /** "open" (from a task's session list) opens the session per the session_open_scroll setting. */
  onOpenRun: (runId: number, scroll?: "run" | "open") => void;
  onChanged: () => void;
}

export function BoardTab({ project, onRunAgent, onOpenRun, onChanged }: Props) {
  const tasks = useLoader(() => api.tasks(project.id), [project.id]);
  const [dragId, setDragId] = useState<number | null>(null);
  const [dropCol, setDropCol] = useState<TaskStatus | null>(null);
  const [openTask, setOpenTask] = useState<Task | null>(null);
  const [drafts, setDrafts] = useState<Partial<Record<TaskStatus, string>>>({});

  const all = tasks.data ?? [];

  async function moveTask(taskId: number, status: TaskStatus, beforeId: number | null) {
    const column = all.filter((t) => t.status === status && t.id !== taskId).sort((a, b) => a.position - b.position);
    const idx = beforeId === null ? column.length : column.findIndex((t) => t.id === beforeId);
    const prev = column[idx - 1]?.position;
    const next = column[idx]?.position;
    const position =
      prev === undefined && next === undefined ? 1 : prev === undefined ? next! - 1 : next === undefined ? prev + 1 : (prev + next) / 2;

    // Optimistic update, then persist.
    tasks.setData(all.map((t) => (t.id === taskId ? { ...t, status, position } : t)));
    try {
      await api.updateTask(taskId, { status, position });
    } finally {
      void tasks.reload();
      onChanged();
    }
  }

  async function addTask(status: TaskStatus) {
    const title = drafts[status]?.trim();
    if (!title) return;
    setDrafts((d) => ({ ...d, [status]: "" }));
    await api.createTask(project.id, { title, status });
    void tasks.reload();
    onChanged();
  }

  if (tasks.error) return <p className="error-text">{tasks.error}</p>;

  return (
    <div className="board-wrap">
      <Organizer
        project={project}
        target="board"
        onChanged={() => {
          void tasks.reload();
          onChanged();
        }}
        onOpenRun={onOpenRun}
      />
      <div className="board">
        {TASK_COLUMNS.map((col) => {
          const items = all.filter((t) => t.status === col.status).sort((a, b) => a.position - b.position);
          return (
            <div
              key={col.status}
              className={`column ${dropCol === col.status ? "drop" : ""}`}
              onDragOver={(e) => {
                e.preventDefault();
                setDropCol(col.status);
              }}
              onDragLeave={() => setDropCol(null)}
              onDrop={(e) => {
                e.preventDefault();
                setDropCol(null);
                if (dragId !== null) void moveTask(dragId, col.status, null);
              }}
            >
              <div className="column-head">
                {col.label} <span className="badge">{items.length}</span>
              </div>
              <div className="column-body">
                {items.map((t) => (
                  <div
                    key={t.id}
                    className="task-card"
                    draggable
                    onDragStart={() => setDragId(t.id)}
                    onDragEnd={() => setDragId(null)}
                    onDrop={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      setDropCol(null);
                      if (dragId !== null && dragId !== t.id) void moveTask(dragId, col.status, t.id);
                    }}
                    onClick={() => setOpenTask(t)}
                  >
                    <div>{t.title}</div>
                    <div className="t-meta">
                      <span className={`prio ${t.priority}`}>{t.priority}</span>
                      {t.github_issue_number && <span>#{t.github_issue_number}</span>}
                      {t.images?.length > 0 && (
                        <span className="task-pics" title={`${t.images.length} picture${t.images.length > 1 ? "s" : ""}`}>
                          <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden>
                            <rect x="1.75" y="2.75" width="12.5" height="10.5" rx="1.5" />
                            <circle cx="5.5" cy="6.5" r="1.25" />
                            <path d="M2.5 12.5l4-4 3 3 2-2 2.5 2.5" />
                          </svg>
                          {t.images.length}
                        </span>
                      )}
                      <span className="spacer" />
                      <span>{taskRef(project.key, t)}</span>
                    </div>
                  </div>
                ))}
                <div className="add-task">
                  <input
                    placeholder="+ Add task"
                    value={drafts[col.status] ?? ""}
                    onChange={(e) => setDrafts((d) => ({ ...d, [col.status]: e.target.value }))}
                    onKeyDown={(e) => e.key === "Enter" && void addTask(col.status)}
                  />
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {openTask && (
        <TaskModal
          task={openTask}
          projectKey={project.key}
          canRunAgent={!!project.repo_path}
          onClose={() => setOpenTask(null)}
          onSaved={() => {
            setOpenTask(null);
            void tasks.reload();
            onChanged();
          }}
          onRunAgent={(t) => {
            setOpenTask(null);
            onRunAgent(t);
          }}
          onOpenRun={(runId) => {
            setOpenTask(null);
            onChanged();
            onOpenRun(runId, "open");
          }}
        />
      )}
    </div>
  );
}
