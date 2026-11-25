import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { usePolling, useLoader } from "./hooks";
import { ProjectForm } from "./components/ProjectForm";
import { ProjectView } from "./components/ProjectView";
import { AppearancePage } from "./components/AppearancePage";
import { SettingsPage } from "./components/SettingsPage";
import { Sidebar, type Page } from "./components/Sidebar";
import { UsagePage } from "./components/UsagePage";

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

  useEffect(() => {
    const list = projects.data;
    if (!list) return;
    if (list.length === 0) setSelectedId(null);
    else if (selectedId === null || !list.some((p) => p.id === selectedId)) setSelectedId(list[0].id);
  }, [projects.data, selectedId]);

  useEffect(() => {
    if (selectedId !== null) localStorage.setItem(LAST_PROJECT_KEY, String(selectedId));
  }, [selectedId]);

  const selected = projects.data?.find((p) => p.id === selectedId) ?? null;
  const onChanged = useCallback(() => void reloadProjects(), [reloadProjects]);

  return (
    <div className="app">
      <Sidebar
        projects={projects.data ?? []}
        selectedId={selectedId}
        onSelect={(id) => {
          setSelectedId(id);
          setPage(null);
        }}
        onNew={() => setCreating(true)}
        page={page}
        onOpenPage={setPage}
      />
      <main className="main">
        {projects.error && !projects.data && (
          <div className="banner">Can't reach the Mercury backend: {projects.error}</div>
        )}
        {page === "usage" ? (
          <UsagePage />
        ) : page === "appearance" ? (
          <AppearancePage />
        ) : page === "settings" ? (
          <SettingsPage />
        ) : selected ? (
          <ProjectView key={selected.id} project={selected} onChanged={onChanged} />
        ) : (
          projects.data && (
            <div className="empty">
              <h2>Welcome to Mercury</h2>
              <p>Track projects, plan tasks, keep notes, and hand work to Claude coding agents.</p>
              <button className="btn primary" onClick={() => setCreating(true)}>
                Create your first project
              </button>
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
