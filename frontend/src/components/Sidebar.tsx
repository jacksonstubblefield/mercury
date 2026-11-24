import type { Project, ProjectStatus } from "../types";

const GROUPS: { status: ProjectStatus; label: string }[] = [
  { status: "active", label: "Active" },
  { status: "paused", label: "Paused" },
  { status: "archived", label: "Archived" },
];

interface Props {
  projects: Project[];
  selectedId: number | null;
  onSelect: (id: number) => void;
  onNew: () => void;
}

export function Sidebar({ projects, selectedId, onSelect, onNew }: Props) {
  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-dot" /> Mercury
      </div>
      <div className="sidebar-section">
        {GROUPS.map(({ status, label }) => {
          const group = projects.filter((p) => p.status === status);
          if (group.length === 0) return null;
          return (
            <div key={status}>
              <div className="sidebar-heading">{label}</div>
              {group.map((p) => (
                <div
                  key={p.id}
                  className={`project-item ${p.id === selectedId ? "active" : ""}`}
                  onClick={() => onSelect(p.id)}
                  title={p.repo_path ?? undefined}
                >
                  <span className="dot" style={{ background: p.color }} />
                  <span className="name">{p.name}</span>
                  {p.active_runs > 0 && <span className="badge live" title="Agents running">● {p.active_runs}</span>}
                  {p.open_tasks > 0 && <span className="badge" title="Open tasks">{p.open_tasks}</span>}
                </div>
              ))}
            </div>
          );
        })}
      </div>
      <div className="sidebar-footer">
        <button className="btn" style={{ width: "100%" }} onClick={onNew}>
          + New project
        </button>
      </div>
    </aside>
  );
}
