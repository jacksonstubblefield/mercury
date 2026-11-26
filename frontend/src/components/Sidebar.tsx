import { attentionDetail, attentionLabel, attentionState } from "../sessions";
import type { Project, ProjectStatus, SessionAttention } from "../types";

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
  /** Sessions that are running, waiting on the user, or finished and not yet looked at. */
  sessions: SessionAttention[];
  onOpenSession: (session: SessionAttention) => void;
}

export function Sidebar({ projects, selectedId, onSelect, onNew, page, onOpenPage, sessions, onOpenSession }: Props) {
  const byId = new Map(projects.map((p) => [p.id, p]));
  const shown = sessions.filter((s) => byId.has(s.project_id));
  // Scratch is pinned below the user's projects, just above the app pages, apart from their status groups.
  const scratch = projects.find((p) => p.scratch);
  const projectItem = (p: Project) => (
    <div
      key={p.id}
      className={`project-item ${!page && p.id === selectedId ? "active" : ""}`}
      onClick={() => onSelect(p.id)}
      title={p.scratch ? "Sessions that don't belong to a project" : (p.repo_path ?? undefined)}
    >
      {p.scratch ? (
        <svg className="nav-icon scratch-icon" style={{ color: p.color }} viewBox="0 0 16 16" aria-hidden="true">
          <path d="M10.8 2.4a1.6 1.6 0 0 1 2.3 2.3l-7.6 7.6-3 .7.7-3 7.6-7.6ZM9.5 3.8l2.3 2.3" />
        </svg>
      ) : (
        <span className="dot" style={{ background: p.color }} />
      )}
      <span className="name">{p.name}</span>
      {p.active_runs > 0 && <span className="badge live" title="Agents running">● {p.active_runs}</span>}
      {p.open_tasks > 0 && <span className="badge" title="Open tasks">{p.open_tasks}</span>}
    </div>
  );
  return (
    <aside className="sidebar">
      <div className="brand">
        <img className="brand-icon" src="/favicon-128.png" alt="" /> Mercury
      </div>
      {shown.length > 0 && (
        <div className="sidebar-sessions">
          <div className="sidebar-heading">Sessions</div>
          {shown.map((s) => {
            const project = byId.get(s.project_id)!;
            const state = attentionState(s);
            const label = attentionLabel(s);
            return (
              <div
                key={s.root_run_id}
                className={`session-item ${state}`}
                onClick={() => onOpenSession(s)}
                title={`${project.name}: ${label}`}
              >
                <span className={`session-state ${state}`} aria-hidden="true">
                  {state === "done" ? "✓" : state === "failed" ? "!" : ""}
                </span>
                <span className="session-text">
                  <span className="name">{label}</span>
                  <span className="session-meta">
                    <span className="dot" style={{ background: project.color, width: 6, height: 6 }} />
                    <span className="session-project">{project.name}</span>
                    <span className={`session-detail ${state}`}>{attentionDetail(s)}</span>
                  </span>
                </span>
              </div>
            );
          })}
        </div>
      )}
      <div className="sidebar-section">
        {GROUPS.map(({ status, label }) => {
          const group = projects.filter((p) => !p.scratch && p.status === status);
          if (group.length === 0) return null;
          return (
            <div key={status}>
              <div className="sidebar-heading">{label}</div>
              {group.map(projectItem)}
            </div>
          );
        })}
      </div>
      {scratch && <div className="sidebar-scratch">{projectItem(scratch)}</div>}
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
