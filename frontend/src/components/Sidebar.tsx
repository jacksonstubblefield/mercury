import type { Project, ProjectStatus } from "../types";

const GROUPS: { status: ProjectStatus; label: string }[] = [
  { status: "active", label: "Active" },
  { status: "paused", label: "Paused" },
  { status: "archived", label: "Archived" },
];

export type Page = "usage" | "appearance" | "settings";

interface Props {
  projects: Project[];
  selectedId: number | null;
  onSelect: (id: number) => void;
  onNew: () => void;
  /** The app page open instead of a project, if any. */
  page: Page | null;
  onOpenPage: (page: Page) => void;
}

export function Sidebar({ projects, selectedId, onSelect, onNew, page, onOpenPage }: Props) {
  return (
    <aside className="sidebar">
      <div className="brand">
        <img className="brand-icon" src="/favicon-64.png" alt="" /> Mercury
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
                  className={`project-item ${!page && p.id === selectedId ? "active" : ""}`}
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
      <nav className="sidebar-nav">
        <div className={`project-item ${page === "usage" ? "active" : ""}`} onClick={() => onOpenPage("usage")} title="Claude Code plan usage">
          <svg className="nav-icon" viewBox="0 0 16 16" aria-hidden="true">
            <path d="M2 13.5h12M4 11V8M8 11V4.5M12 11V6.5" />
          </svg>
          <span className="name">Usage</span>
        </div>
        <div
          className={`project-item ${page === "appearance" ? "active" : ""}`}
          onClick={() => onOpenPage("appearance")}
          title="Colors, fonts and themes"
        >
          <svg className="nav-icon" viewBox="0 0 16 16" aria-hidden="true">
            <path d="M8 1.75a6.25 6.25 0 1 0 0 12.5c.9 0 1.4-.6 1.4-1.3 0-.9-.8-1.2-.8-2 0-.7.6-1.2 1.3-1.2h1.6c1.6 0 2.75-1.2 2.75-2.8 0-2.9-2.8-5.2-6.25-5.2Z" />
            <circle cx="4.9" cy="7.4" r=".6" />
            <circle cx="7" cy="4.7" r=".6" />
            <circle cx="10.4" cy="5.1" r=".6" />
          </svg>
          <span className="name">Appearance</span>
        </div>
        <div
          className={`project-item ${page === "settings" ? "active" : ""}`}
          onClick={() => onOpenPage("settings")}
          title="App-wide settings"
        >
          <svg className="nav-icon" viewBox="0 0 16 16" aria-hidden="true">
            <path d="M2.5 4.5h6M11.5 4.5h2M2.5 11.5h2M7.5 11.5h6" />
            <circle cx="10" cy="4.5" r="1.5" />
            <circle cx="6" cy="11.5" r="1.5" />
          </svg>
          <span className="name">Settings</span>
        </div>
      </nav>
      <div className="sidebar-footer">
        <button className="btn" style={{ width: "100%" }} onClick={onNew}>
          + New project
        </button>
      </div>
    </aside>
  );
}
