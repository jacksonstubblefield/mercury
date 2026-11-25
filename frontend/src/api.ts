import type {
  AgentEvent,
  AgentRun,
  Appearance,
  ClaudeUsage,
  DiffKind,
  GhIssue,
  GhPull,
  GitSummary,
  Health,
  Doc,
  DocCollection,
  DocListItem,
  DocSearchHit,
  DocSearchStatus,
  DocStatus,
  MemoryImportPreview,
  PermissionMode,
  Preferences,
  Project,
  ProjectInput,
  ScmActionResult,
  ScmDiff,
  ScmFileChanges,
  ScmFileContent,
  ScmRepoStatus,
  ScmStatus,
  Task,
} from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let message = res.statusText;
    try {
      const data = await res.json();
      message = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail ?? data);
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, message);
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}

const get = <T>(p: string) => request<T>("GET", p);
const post = <T>(p: string, b?: unknown) => request<T>("POST", p, b ?? {});
const patch = <T>(p: string, b: unknown) => request<T>("PATCH", p, b);
const put = <T>(p: string, b: unknown) => request<T>("PUT", p, b);
const del = (p: string) => request<void>("DELETE", p);

export const api = {
  health: () => get<Health>("/api/health"),
  appearance: () => get<Appearance>("/api/appearance"),
  saveAppearance: (a: Appearance) => put<Appearance>("/api/appearance", a),
  preferences: () => get<Preferences>("/api/preferences"),
  /** Changes only the given settings, so the Settings page and agents don't undo each other's changes. */
  updatePreferences: (changes: Partial<Preferences>) => patch<Preferences>("/api/preferences", changes),

  claudeUsage: (refresh = false) => get<ClaudeUsage>(`/api/usage/claude-code${refresh ? "?refresh=true" : ""}`),

  projects: () => get<Project[]>("/api/projects"),
  project: (id: number) => get<Project>(`/api/projects/${id}`),
  createProject: (body: ProjectInput) => post<Project>("/api/projects", body),
  updateProject: (id: number, body: ProjectInput) => patch<Project>(`/api/projects/${id}`, body),
  deleteProject: (id: number) => del(`/api/projects/${id}`),
  git: (id: number) => get<GitSummary>(`/api/projects/${id}/git`),

  tasks: (projectId: number) => get<Task[]>(`/api/projects/${projectId}/tasks`),
  createTask: (projectId: number, body: Partial<Task>) => post<Task>(`/api/projects/${projectId}/tasks`, body),
  updateTask: (id: number, body: Partial<Task>) => patch<Task>(`/api/tasks/${id}`, body),
  deleteTask: (id: number) => del(`/api/tasks/${id}`),

  docs: (projectId: number) => get<DocListItem[]>(`/api/projects/${projectId}/docs`),
  searchDocs: (projectId: number, q: string, collectionId?: number) =>
    get<DocSearchHit[]>(
      `/api/projects/${projectId}/docs/search?q=${encodeURIComponent(q)}` +
        (collectionId != null ? `&collection_id=${collectionId}` : ""),
    ),
  moveDocs: (projectId: number, docIds: number[], collectionId: number | null) =>
    post<{ moved: number }>(`/api/projects/${projectId}/docs/move`, { doc_ids: docIds, collection_id: collectionId }),
  collections: (projectId: number) => get<DocCollection[]>(`/api/projects/${projectId}/collections`),
  createCollection: (projectId: number, body: { name: string; description?: string; parent_id?: number | null }) =>
    post<DocCollection>(`/api/projects/${projectId}/collections`, body),
  updateCollection: (id: number, body: Partial<Pick<DocCollection, "name" | "description" | "parent_id" | "summary">>) =>
    patch<DocCollection>(`/api/collections/${id}`, body),
  deleteCollection: (id: number) => del(`/api/collections/${id}`),
  regenerateCollectionSummary: (id: number) => post<DocCollection>(`/api/collections/${id}/summary`),
  docSearchStatus: (projectId: number) => get<DocSearchStatus>(`/api/projects/${projectId}/docs/search-status`),
  doc: (id: number) => get<Doc>(`/api/docs/${id}`),
  createDoc: (projectId: number, body: Partial<Doc>) => post<Doc>(`/api/projects/${projectId}/docs`, body),
  updateDoc: (id: number, body: Partial<Doc>) => patch<Doc>(`/api/docs/${id}`, body),
  deleteDoc: (id: number) => del(`/api/docs/${id}`),
  regenerateDocSummary: (id: number) => post<Doc>(`/api/docs/${id}/summary`),
  previewMemoryImport: (projectId: number, memoryDir?: string) =>
    get<MemoryImportPreview>(
      `/api/projects/${projectId}/docs/import/claude-memory` +
        (memoryDir ? `?memory_dir=${encodeURIComponent(memoryDir)}` : ""),
    ),
  applyMemoryImport: (
    projectId: number,
    body: { memory_dir?: string; choices: Record<string, { include?: boolean; status?: DocStatus }> },
  ) => post<{ created: number; updated: number; skipped: number }>(`/api/projects/${projectId}/docs/import/claude-memory`, body),
  removeMemoryImport: (projectId: number) =>
    request<{ deleted: number }>("DELETE", `/api/projects/${projectId}/docs/import/claude-memory`),

  issues: (projectId: number, state = "open") => get<GhIssue[]>(`/api/projects/${projectId}/github/issues?state=${state}`),
  pulls: (projectId: number, state = "open") => get<GhPull[]>(`/api/projects/${projectId}/github/pulls?state=${state}`),
  syncIssues: (projectId: number) =>
    post<{ created: number; updated: number; closed: number }>(`/api/projects/${projectId}/github/sync-issues`),

  scmStatus: (projectId: number) => get<ScmStatus>(`/api/projects/${projectId}/scm/status`),
  scmInit: (projectId: number) => post<ScmRepoStatus>(`/api/projects/${projectId}/scm/init`),
  scmDiff: (projectId: number, path: string, kind: DiffKind, origPath?: string | null) => {
    const q = new URLSearchParams({ path, kind });
    if (origPath) q.set("orig_path", origPath);
    return get<ScmDiff>(`/api/projects/${projectId}/scm/diff?${q}`);
  },
  /** `path` may be absolute (as agents report it) or repo-relative. */
  scmFileChanges: (projectId: number, path: string) =>
    get<ScmFileChanges>(`/api/projects/${projectId}/scm/file-changes?${new URLSearchParams({ path })}`),
  scmFile: (projectId: number, path: string) => get<ScmFileContent>(`/api/projects/${projectId}/scm/file?${new URLSearchParams({ path })}`),
  /** `paths: null` means every file in the group. */
  scmStage: (projectId: number, paths: string[] | null) => post<ScmRepoStatus>(`/api/projects/${projectId}/scm/stage`, { paths }),
  scmUnstage: (projectId: number, paths: string[] | null) => post<ScmRepoStatus>(`/api/projects/${projectId}/scm/unstage`, { paths }),
  scmDiscard: (projectId: number, paths: string[] | null) => post<ScmRepoStatus>(`/api/projects/${projectId}/scm/discard`, { paths }),
  scmCommit: (projectId: number, body: { message: string; stage_all: boolean; push: boolean }) =>
    post<ScmActionResult>(`/api/projects/${projectId}/scm/commit`, body),
  scmPush: (projectId: number) => post<ScmActionResult>(`/api/projects/${projectId}/scm/push`),
  scmPull: (projectId: number) => post<ScmActionResult>(`/api/projects/${projectId}/scm/pull`),
  scmFetch: (projectId: number) => post<ScmActionResult>(`/api/projects/${projectId}/scm/fetch`),
  scmGenerateMessage: (projectId: number) => post<{ message: string }>(`/api/projects/${projectId}/scm/generate-message`),

  runs: (projectId: number, taskId?: number) =>
    get<AgentRun[]>(`/api/projects/${projectId}/runs${taskId == null ? "" : `?task_id=${taskId}`}`),
  run: (id: number) => get<AgentRun>(`/api/runs/${id}`),
  runEvents: (id: number) => get<AgentEvent[]>(`/api/runs/${id}/events`),
  startRun: (
    projectId: number,
    body: {
      prompt: string;
      task_id?: number | null;
      permission_mode: PermissionMode;
      model?: string | null;
      max_budget_usd?: number | null;
      /** base64 image data (no data: prefix) */
      images?: string[];
    },
  ) => post<AgentRun>(`/api/projects/${projectId}/runs`, body),
  /** Starts an organizer run, or returns the one already organizing that target. */
  organize: (projectId: number, target: "board" | "docs") => post<AgentRun>(`/api/projects/${projectId}/organize`, { target }),
  cancelRun: (id: number) => post<AgentRun>(`/api/runs/${id}/cancel`),
  followUp: (id: number, prompt: string, permission_mode?: PermissionMode, images?: string[]) =>
    post<AgentRun>(`/api/runs/${id}/follow-up`, { prompt, permission_mode, images }),
  attachmentUrl: (name: string) => `/api/attachments/${encodeURIComponent(name)}`,
  setRunMode: (id: number, permission_mode: PermissionMode) =>
    post<AgentRun>(`/api/runs/${id}/permission-mode`, { permission_mode }),
  decide: (runId: number, approvalId: string, allow: boolean, message = "") =>
    post(`/api/runs/${runId}/approvals/${approvalId}`, { allow, message }),

  streamUrl: (runId: number) => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    return `${proto}://${location.host}/api/runs/${runId}/stream`;
  },
};

/** Desktop bridge exposed by Electron's preload script (absent in a plain browser). */
export interface MercuryBridge {
  pickFolder(): Promise<string | null>;
  openExternal(url: string): Promise<void>;
}

declare global {
  interface Window {
    mercury?: MercuryBridge;
  }
}

export function openExternal(url: string) {
  if (window.mercury) void window.mercury.openExternal(url);
  else window.open(url, "_blank", "noopener");
}
