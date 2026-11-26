import { useEffect, useState } from "react";
import { api } from "../api";
import { MODES, modeInfo } from "../permissions";
import { AGENT_MODELS, modelInfo } from "../agentModels";
import { PROJECT_KEY_RE } from "../refs";
import type { AgentModel, PermissionMode, Preferences, Project, ProjectStatus } from "../types";

const COLORS = ["#7c8cff", "#4fd18b", "#f0b44c", "#ff6b6b", "#4cc9f0", "#c77dff", "#ff9f68", "#9aa1b2"];

interface Props {
  project?: Project;
  onClose: () => void;
  onSaved: (p: Project) => void;
  onDeleted?: () => void;
}

export function ProjectForm({ project, onClose, onSaved, onDeleted }: Props) {
  // The built-in Scratch project has no name, status or GitHub repo to edit, and can't be deleted.
  const scratch = !!project?.scratch;
  const [name, setName] = useState(project?.name ?? "");
  const [key, setKey] = useState(project?.key ?? "");
  const [suggestedKey, setSuggestedKey] = useState("");
  const keyValid = key === "" ? !project : PROJECT_KEY_RE.test(key);
  const [description, setDescription] = useState(project?.description ?? "");
  const [repoPath, setRepoPath] = useState(project?.repo_path ?? "");
  const [githubRepo, setGithubRepo] = useState(project?.github_repo ?? "");
  const [status, setStatus] = useState<ProjectStatus>(project?.status ?? "active");
  const [color, setColor] = useState(project?.color ?? COLORS[0]);
  const [mode, setMode] = useState<PermissionMode | null>(project?.default_permission_mode ?? null);
  const [model, setModel] = useState<AgentModel | null>(project?.default_model ?? null);
  const [appPrefs, setAppPrefs] = useState<Preferences | null>(null);
  const appMode = appPrefs?.default_permission_mode;
  const appModel = appPrefs?.default_model;
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let live = true;
    api
      .preferences()
      .then((p) => live && setAppPrefs(p))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, []);

  // A new project left without a key gets one from its name: show which.
  useEffect(() => {
    if (project) return;
    if (!name.trim()) return setSuggestedKey("");
    let live = true;
    const id = window.setTimeout(() => {
      api
        .suggestProjectKey(name)
        .then((r) => live && setSuggestedKey(r.key))
        .catch(() => {});
    }, 250);
    return () => {
      live = false;
      window.clearTimeout(id);
    };
  }, [name, project]);

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
      default_model: model,
      ...(key && key !== project?.key ? { key } : {}),
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
        <h2>{scratch ? "Scratch settings" : project ? "Project settings" : "New project"}</h2>

        <div className="row" style={{ alignItems: "flex-start" }}>
          <div style={{ flex: 1 }}>
            <label>Name</label>
            <input
              autoFocus={!scratch}
              disabled={scratch}
              title={scratch ? "Scratch is built in, so its name is fixed." : undefined}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Mercury"
            />
          </div>
          <div style={{ width: 110 }}>
            <label title="2-4 letters or digits, starting with a letter. Task and doc ids start with it: MER-12, MER-D3.">
              Key
            </label>
            <input
              value={key}
              maxLength={4}
              onChange={(e) => setKey(e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, ""))}
              placeholder={suggestedKey || "MER"}
              style={{ fontFamily: "var(--mono, monospace)" }}
            />
          </div>
        </div>
        {!keyValid && key !== "" && (
          <p className="error-text" style={{ margin: "4px 0 0" }}>
            The key is 2-4 letters or digits, starting with a letter.
          </p>
        )}
        {project && key !== project.key && keyValid && (
          <p className="faint" style={{ fontSize: 12, margin: "4px 0 0" }}>
            Tasks and docs will show as {key}-12 and {key}-D3. Ids already written down (in docs, commits or
            messages) as {project.key}-… won't change.
          </p>
        )}

        <label>Description</label>
        <textarea value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What is this project? (Markdown)" />

        <label>{scratch ? "Scratch folder" : "Local repository folder"} — where agents do their work</label>
        <div className="row">
          <input
            value={repoPath}
            onChange={(e) => setRepoPath(e.target.value)}
            placeholder={scratch ? "Leave blank for Mercury's own scratch folder" : "C:\\src\\my-project"}
          />
          {window.mercury && (
            <button className="btn" onClick={pickFolder}>
              Browse…
            </button>
          )}
        </div>
        {scratch && repoPath.trim() !== (project?.repo_path ?? "") && (
          <p className="faint" style={{ fontSize: 12, margin: "4px 0 0" }}>
            Earlier scratch sessions ran in the old folder, so follow-ups to them may not be able to continue.
          </p>
        )}

        {!scratch && (
          <>
            <label>GitHub repository (owner/name) — detected from the git remote if left blank</label>
            <input value={githubRepo} onChange={(e) => setGithubRepo(e.target.value)} placeholder="octocat/hello-world" />
          </>
        )}

        <label>Default model for new agent sessions</label>
        <select value={model ?? ""} onChange={(e) => setModel((e.target.value || null) as AgentModel | null)}>
          <option value="">App default{appModel ? ` (${modelInfo(appModel)?.label ?? appModel})` : ""}</option>
          {AGENT_MODELS.map((m) => (
            <option key={m.value} value={m.value}>
              {m.label}
            </option>
          ))}
        </select>
        {model && (
          <p className="faint" style={{ fontSize: 12, margin: "4px 0 0" }}>
            {modelInfo(model)?.hint}
          </p>
        )}

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
          {!scratch && (
            <div>
              <label>Status</label>
              <select value={status} onChange={(e) => setStatus(e.target.value as ProjectStatus)} style={{ width: 140 }}>
                <option value="active">Active</option>
                <option value="paused">Paused</option>
                <option value="archived">Archived</option>
              </select>
            </div>
          )}
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
          {project && !scratch && (
            <button className="btn danger" onClick={remove}>
              Delete project
            </button>
          )}
          <span className="spacer" />
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn primary" disabled={!name.trim() || !keyValid || saving} onClick={save}>
            {project ? "Save" : "Create"}
          </button>
        </div>
      </div>
    </div>
  );
}
