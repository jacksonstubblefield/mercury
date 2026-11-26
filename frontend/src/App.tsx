import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { usePolling, useLoader } from "./hooks";
import type { FocusRequest } from "./components/AgentsTab";
import { ProjectForm } from "./components/ProjectForm";
import { ProjectView } from "./components/ProjectView";
import { AppearancePage } from "./components/AppearancePage";
import { SettingsPage } from "./components/SettingsPage";
import { Sidebar, type Page } from "./components/Sidebar";
import { UsageGauge } from "./components/UsageGauge";
import { UsagePage } from "./components/UsagePage";
import { useAgentNotifications } from "./notifications";
import { useClaudeUsage } from "./usage";

const LAST_PROJECT_KEY = "mercury.lastProject";

export default function App() {
  const projects = useLoader(() => api.projects(), []);
  const [selectedId, setSelectedId] = useState<number | null>(() => {
    const saved = localStorage.getItem(LAST_PROJECT_KEY);
    return saved ? Number(saved) : null;
  });
  const [creating, setCreating] = useState(false);
  // An app page (Usage, Appearance, Settings) shown instead of the selected project.
  const [page, setPage] = useState<Page | null>(null);

  // Keep run badges in the sidebar fresh.
  const reloadProjects = projects.reload;
  usePolling(reloadProjects, 5000);

  // Sessions across all projects that are running, need an answer, or finished unseen (sidebar).
  const attention = useLoader(() => api.attention(), []);
  const reloadAttention = attention.reload;
  usePolling(reloadAttention, 3000);
  useAgentNotifications(attention.data, projects.data);
  const usage = useClaudeUsage();

  // A session to open in its project's Agents tab (from the sidebar or a notification).
  const [openSession, setOpenSession] = useState<(FocusRequest & { projectId: number }) | null>(null);
  const openRun = useCallback((projectId: number, runId: number) => {
    setPage(null);
    setSelectedId(projectId);
    setOpenSession({ projectId, runId, nonce: Date.now(), scroll: "open" });
  }, []);

  useEffect(() => window.mercury?.onOpenRun?.(({ projectId, runId }) => openRun(projectId, runId)), [openRun]);

  useEffect(() => {
    const list = projects.data;
    if (!list) return;
    // With nothing chosen, open the user's first project; Scratch only when they pick it (else the welcome page).
    if (selectedId === null || !list.some((p) => p.id === selectedId)) setSelectedId(list.find((p) => !p.scratch)?.id ?? null);
  }, [projects.data, selectedId]);

  useEffect(() => {
    if (selectedId !== null) localStorage.setItem(LAST_PROJECT_KEY, String(selectedId));
  }, [selectedId]);

  const selected = projects.data?.find((p) => p.id === selectedId) ?? null;
  const scratch = projects.data?.find((p) => p.scratch);
  const onChanged = useCallback(() => {
    void reloadProjects();
    void reloadAttention();
  }, [reloadProjects, reloadAttention]);

  return (
    <div className="app">
      <Sidebar
        projects={projects.data ?? []}
        selectedId={selectedId}
        onSelect={(id) => {
          setSelectedId(id);
          setPage(null);
          setOpenSession(null);
        }}
        onNew={() => setCreating(true)}
        page={page}
        onOpenPage={setPage}
        sessions={attention.data ?? []}
        onOpenSession={(s) => openRun(s.project_id, s.run_id)}
      />
      <main className="main">
        <UsageGauge usage={usage} onOpen={() => setPage("usage")} />
        {projects.error && !projects.data && (
          <div className="banner">Can't reach the Mercury backend: {projects.error}</div>
        )}
        {page === "usage" ? (
          <UsagePage usage={usage} />
        ) : page === "appearance" ? (
          <AppearancePage />
        ) : page === "settings" ? (
          <SettingsPage />
        ) : selected ? (
          <ProjectView
            key={selected.id}
            project={selected}
            onChanged={onChanged}
            openSession={openSession?.projectId === selected.id ? openSession : null}
          />
        ) : (
          projects.data && (
            <div className="empty">
              <h2>Welcome to Mercury</h2>
              <p>Track projects, plan tasks, keep notes, and hand work to Claude coding agents.</p>
              <button className="btn primary" onClick={() => setCreating(true)}>
                Create your first project
              </button>
              {scratch && (
                <p>
                  <button className="link-btn" onClick={() => setSelectedId(scratch.id)}>
                    Or start a session in Scratch, without a project
                  </button>
                </p>
              )}
            </div>
          )
        )}
      </main>
      {creating && (
        <ProjectForm
          onClose={() => setCreating(false)}
          onSaved={(p) => {
            setCreating(false);
            setPage(null);
            void projects.reload().then(() => setSelectedId(p.id));
          }}
        />
      )}
    </div>
  );
}
