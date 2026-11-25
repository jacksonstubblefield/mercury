import { useEffect, useState } from "react";
import { api } from "../api";
import { MODES, modeInfo } from "../permissions";
import type { PermissionMode, Project, ProjectStatus } from "../types";

const COLORS = ["#7c8cff", "#4fd18b", "#f0b44c", "#ff6b6b", "#4cc9f0", "#c77dff", "#ff9f68", "#9aa1b2"];

interface Props {
  project?: Project;
  onClose: () => void;
  onSaved: (p: Project) => void;
  onDeleted?: () => void;
}

export function ProjectForm({ project, onClose, onSaved, onDeleted }: Props) {
  const [name, setName] = useState(project?.name ?? "");
  const [description, setDescription] = useState(project?.description ?? "");
  const [repoPath, setRepoPath] = useState(project?.repo_path ?? "");
  const [githubRepo, setGithubRepo] = useState(project?.github_repo ?? "");
  const [status, setStatus] = useState<ProjectStatus>(project?.status ?? "active");
  const [color, setColor] = useState(project?.color ?? COLORS[0]);
  const [mode, setMode] = useState<PermissionMode | null>(project?.default_permission_mode ?? null);
  const [appMode, setAppMode] = useState<PermissionMode | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let live = true;
    api
      .preferences()
      .then((p) => live && setAppMode(p.default_permission_mode))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, []);

  async function pickFolder() {
    const folder = await window.mercury?.pickFolder();
    if (folder) {
      setRepoPath(folder);
      if (!name) setName(folder.split(/[\\/]/).filter(Boolean).pop() ?? "");
    }
  }

  async function save() {
    setSaving(true);
    setError(null);
    const body = {
      name: name.trim(),
      description,
      repo_path: repoPath.trim() || null,
      github_repo: githubRepo.trim() || null,
      status,
      color,
      default_permission_mode: mode,
    };
    try {
      const saved = project ? await api.updateProject(project.id, body) : await api.createProject(body);
      onSaved(saved);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setSaving(false);
    }
  }

  async function remove() {
    if (!project || !confirm(`Delete "${project.name}" and all its tasks, docs and agent runs? Files on disk are not touched.`)) return;
    await api.deleteProject(project.id);
    onDeleted?.();
  }

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <h2>{project ? "Project settings" : "New project"}</h2>

        <label>Name</label>
        <input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Mercury" />

        <label>Description</label>
        <textarea value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What is this project? (Markdown)" />

        <label>Local repository folder — where agents do their work</label>
        <div className="row">
          <input value={repoPath} onChange={(e) => setRepoPath(e.target.value)} placeholder="C:\src\my-project" />
          {window.mercury && (
            <button className="btn" onClick={pickFolder}>
              Browse…
            </button>
          )}
        </div>

        <label>GitHub repository (owner/name) — detected from the git remote if left blank</label>
        <input value={githubRepo} onChange={(e) => setGithubRepo(e.target.value)} placeholder="octocat/hello-world" />

        <label>Default permissions for new agent sessions</label>
        <select value={mode ?? ""} onChange={(e) => setMode((e.target.value || null) as PermissionMode | null)}>
          <option value="">App default{appMode ? ` (${modeInfo(appMode)?.label})` : ""}</option>
          {MODES.map((m) => (
            <option key={m.value} value={m.value}>
              {m.label}
            </option>
          ))}
        </select>
        {mode && (
          <p className="faint" style={{ fontSize: 12, margin: "4px 0 0" }}>
            {modeInfo(mode)?.hint}
          </p>
        )}

        <div className="row" style={{ alignItems: "flex-start", gap: 24 }}>
          <div>
            <label>Status</label>
            <select value={status} onChange={(e) => setStatus(e.target.value as ProjectStatus)} style={{ width: 140 }}>
              <option value="active">Active</option>
              <option value="paused">Paused</option>
              <option value="archived">Archived</option>
            </select>
          </div>
          <div>
            <label>Color</label>
            <div className="row" style={{ height: 36 }}>
              {COLORS.map((c) => (
                <span
                  key={c}
                  onClick={() => setColor(c)}
                  className="dot"
                  style={{ background: c, width: 18, height: 18, cursor: "pointer", outline: c === color ? "2px solid var(--text)" : "none", outlineOffset: 2 }}
                />
              ))}
            </div>
          </div>
        </div>

        {error && <p className="error-text">{error}</p>}

        <div className="modal-actions">
          {project && (
            <button className="btn danger" onClick={remove}>
              Delete project
            </button>
          )}
          <span className="spacer" />
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn primary" disabled={!name.trim() || saving} onClick={save}>
            {project ? "Save" : "Create"}
          </button>
        </div>
      </div>
    </div>
  );
}
