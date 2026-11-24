import { useState } from "react";
import { api } from "../api";
import { useLoader } from "../hooks";
import { TASK_COLUMNS, type Project, type Task, type TaskStatus } from "../types";
import { TaskModal } from "./TaskModal";

interface Props {
  project: Project;
  onRunAgent: (task: Task) => void;
  onOpenRun: (runId: number) => void;
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
    <>
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
                      <span className="spacer" />
                      <span>T-{t.id}</span>
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
            onOpenRun(runId);
          }}
        />
      )}
    </>
  );
}
