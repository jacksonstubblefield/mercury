import { useState } from "react";
import { openExternal } from "../api";
import type { Project, Task } from "../types";
import { AgentsTab } from "./AgentsTab";
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

interface Props {
  project: Project;
  onChanged: () => void;
}

export function ProjectView({ project, onChanged }: Props) {
  const [tab, setTab] = useState<TabId>("overview");
  const [editing, setEditing] = useState(false);
  // A task handed to the Agents tab from the board ("Run agent").
  const [agentTask, setAgentTask] = useState<Task | null>(null);
  const [focusRunId, setFocusRunId] = useState<number | null>(null);

  function runAgentOn(task: Task) {
    setAgentTask(task);
    setTab("agents");
  }

  function openRun(runId: number) {
    setFocusRunId(runId);
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
          {project.repo_path ? <code>{project.repo_path}</code> : <span className="faint">No local repo set</span>}
          {project.github_repo && (
            <a href="#" onClick={(e) => (e.preventDefault(), openExternal(`https://github.com/${project.github_repo}`))}>
              github.com/{project.github_repo}
            </a>
          )}
          <span>Status: {project.status}</span>
        </div>
        <nav className="tabs">
          {TABS.map((t) => (
            <button key={t.id} className={`tab ${tab === t.id ? "active" : ""}`} onClick={() => setTab(t.id)}>
              {t.label}
              {t.id === "agents" && project.active_runs > 0 && <span className="badge live" style={{ marginLeft: 6 }}>{project.active_runs}</span>}
            </button>
          ))}
        </nav>
      </header>

      <section className="content">
        {tab === "overview" && <OverviewTab project={project} onOpenRun={openRun} />}
        {tab === "board" && <BoardTab project={project} onRunAgent={runAgentOn} onOpenRun={openRun} onChanged={onChanged} />}
        {tab === "docs" && <DocsTab project={project} onOpenRun={openRun} />}
        {tab === "agents" && (
          <AgentsTab
            project={project}
            initialTask={agentTask}
            onConsumeInitialTask={() => setAgentTask(null)}
            focusRunId={focusRunId}
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
