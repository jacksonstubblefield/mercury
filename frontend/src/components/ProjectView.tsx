import { useEffect, useState } from "react";
import { openExternal } from "../api";
import type { Project, Task } from "../types";
import { AgentsTab, type FocusRequest } from "./AgentsTab";
import { BoardTab } from "./BoardTab";
import { GitHubTab } from "./GitHubTab";
import { DocsTab } from "./DocsTab";
import { OverviewTab } from "./OverviewTab";
import { ProjectForm } from "./ProjectForm";
import { SourceControlTab } from "./SourceControlTab";

type TabId = "overview" | "board" | "docs" | "agents" | "scm" | "github";

const TABS: { id: TabId; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "board", label: "Board" },
  { id: "docs", label: "Docs" },
  { id: "agents", label: "Agents" },
  { id: "scm", label: "Source Control" },
  { id: "github", label: "GitHub" },
];

/** Scratch isn't a codebase: just its sessions, board and docs, with sessions first. */
const SCRATCH_TABS: TabId[] = ["agents", "board", "docs"];

interface Props {
  project: Project;
  onChanged: () => void;
  /** Open a session in the Agents tab, e.g. from the sidebar's Sessions list. A new nonce reopens it. */
  openSession?: FocusRequest | null;
}

export function ProjectView({ project, onChanged, openSession }: Props) {
  const tabs = project.scratch ? SCRATCH_TABS.map((id) => TABS.find((t) => t.id === id)!) : TABS;
  const [tab, setTab] = useState<TabId>(openSession || project.scratch ? "agents" : "overview");
  const [editing, setEditing] = useState(false);
  // A task handed to the Agents tab from the board ("Run agent").
  const [agentTask, setAgentTask] = useState<Task | null>(null);
  const [focus, setFocus] = useState<FocusRequest | null>(openSession ?? null);

  useEffect(() => {
    if (!openSession) return;
    setFocus(openSession);
    setTab("agents");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openSession?.nonce]);

  function runAgentOn(task: Task) {
    setAgentTask(task);
    setTab("agents");
  }

  /** "run" goes to that message; "open" opens its session where the session_open_scroll setting says. */
  function openRun(runId: number, scroll: FocusRequest["scroll"] = "run") {
    setFocus({ runId, nonce: Date.now(), scroll });
    setTab("agents");
  }

  return (
    <>
      <header className="project-header">
        <h1>
          <span className="dot" style={{ background: project.color, width: 12, height: 12 }} />
          {project.name}
          <button className="btn ghost small" onClick={() => setEditing(true)} title="Project settings">
            ⚙ Settings
          </button>
        </h1>
        <div className="meta">
          {project.scratch && <span>Sessions that don't belong to a project</span>}
          {project.repo_path ? <code>{project.repo_path}</code> : <span className="faint">No local repo set</span>}
          {!project.scratch && project.github_repo && (
            <a href="#" onClick={(e) => (e.preventDefault(), openExternal(`https://github.com/${project.github_repo}`))}>
              github.com/{project.github_repo}
            </a>
          )}
          {!project.scratch && <span>Status: {project.status}</span>}
        </div>
        <nav className="tabs">
          {tabs.map((t) => (
            <button key={t.id} className={`tab ${tab === t.id ? "active" : ""}`} onClick={() => setTab(t.id)}>
              {t.label}
              {t.id === "agents" && project.active_runs > 0 && <span className="badge live" style={{ marginLeft: 6 }}>{project.active_runs}</span>}
            </button>
          ))}
        </nav>
      </header>

      <section className="content">
        {tab === "overview" && <OverviewTab project={project} onOpenRun={(id) => openRun(id, "open")} />}
        {tab === "board" && <BoardTab project={project} onRunAgent={runAgentOn} onOpenRun={openRun} onChanged={onChanged} />}
        {tab === "docs" && <DocsTab project={project} onOpenRun={openRun} />}
        {tab === "agents" && (
          <AgentsTab
            project={project}
            initialTask={agentTask}
            onConsumeInitialTask={() => setAgentTask(null)}
            focus={focus}
            onChanged={onChanged}
          />
        )}
        {tab === "scm" && <SourceControlTab project={project} />}
        {tab === "github" && <GitHubTab project={project} onChanged={onChanged} />}
      </section>

      {editing && (
        <ProjectForm
          project={project}
          onClose={() => setEditing(false)}
          onSaved={() => {
            setEditing(false);
            onChanged();
          }}
          onDeleted={() => {
            setEditing(false);
            onChanged();
          }}
        />
      )}
    </>
  );
}
