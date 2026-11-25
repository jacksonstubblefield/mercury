export type ProjectStatus = "active" | "paused" | "archived";
export type TaskStatus = "backlog" | "todo" | "in_progress" | "review" | "done";
export type TaskPriority = "low" | "medium" | "high" | "urgent";
export type RunStatus = "queued" | "running" | "waiting_approval" | "succeeded" | "failed" | "cancelled";
export type PermissionMode = "plan" | "default" | "acceptEdits" | "bypassPermissions";

export interface Project {
  id: number;
  name: string;
  description: string;
  repo_path: string | null;
  github_repo: string | null;
  status: ProjectStatus;
  color: string;
  /** Preselected for new agent sessions. null: the app-wide default (Preferences). */
  default_permission_mode: PermissionMode | null;
  created_at: string;
  updated_at: string;
  open_tasks: number;
  active_runs: number;
}

export type ProjectInput = Partial<Omit<Project, "id" | "created_at" | "updated_at" | "open_tasks" | "active_runs">>;

export interface Task {
  id: number;
  project_id: number;
  title: string;
  description: string;
  status: TaskStatus;
  priority: TaskPriority;
  position: number;
  github_issue_number: number | null;
  github_url: string | null;
  created_at: string;
  updated_at: string;
}

export type DocStatus = "current" | "draft" | "superseded" | "archived";
export type DocKind = "doc" | "rules";
export type DocOrigin = "user" | "agent" | "import";

export interface Doc {
  id: number;
  project_id: number;
  title: string;
  body: string;
  pinned: boolean;
  status: DocStatus;
  kind: DocKind;
  superseded_by: number | null;
  /** null: unfiled. */
  collection_id: number | null;
  origin: DocOrigin;
  /** Written for agents: what they see in search results. */
  summary: string;
  /** The summary was written for an older version; Mercury rewrites it shortly. */
  summary_stale: boolean;
  source_key: string | null;
  created_at: string;
  updated_at: string;
}

/** A doc in the list (no body; the editor loads one doc at a time). */
export interface DocListItem {
  id: number;
  title: string;
  pinned: boolean;
  status: DocStatus;
  kind: DocKind;
  origin: DocOrigin;
  collection_id: number | null;
  updated_at: string;
}

/** A folder of docs; collections nest at most two levels. */
export interface DocCollection {
  id: number;
  project_id: number;
  name: string;
  /** What belongs here (guides filing). */
  description: string;
  parent_id: number | null;
  /** Written by Mercury from the docs' summaries, for orienting agents. */
  summary: string;
  summary_stale: boolean;
  /** Docs directly in this collection. */
  doc_count: number;
  created_at: string;
  updated_at: string;
}

export interface DocSearchHit {
  doc_id: number;
  title: string;
  status: DocStatus;
  kind?: DocKind;
  summary: string | null;
  summary_stale: boolean;
  /** Heading path of the best-matching section; null for text before the first heading. */
  section: string | null;
  lines: [number, number];
  /** Matched words are wrapped in « ». */
  snippet: string;
  superseded_by?: number;
  other_matching_sections?: number;
  /** How the section matched the query. */
  matched: "words" | "meaning" | "words+meaning";
  /** The doc's collection path, e.g. "Design › Storage". */
  collection?: string;
}

export interface DocSearchStatus {
  /** Meaning-based search: off, loading (first run downloads the model), ready, or error. */
  state: "off" | "loading" | "ready" | "error";
  model: string;
  error: string | null;
  sections: number;
  embedded: number;
}

export type MemoryImportAction = "create" | "update" | "unchanged" | "edited";

export interface MemoryImportItem {
  key: string;
  file: string;
  title: string;
  kind: DocKind;
  summary: string;
  pinned: boolean;
  flags: string[];
  action: MemoryImportAction;
  doc_id: number | null;
  chars: number;
  include: boolean;
  /** Collection a new doc is filed in (split memories). */
  collection: string | null;
}

export interface MemoryImportPreview {
  memory_dir: string;
  items: MemoryImportItem[];
}

/** What a run is for: coding work, or tidying the board or the docs (the Organize buttons). */
export type RunPurpose = "work" | "organize_board" | "organize_docs";

export interface AgentRun {
  id: number;
  project_id: number;
  task_id: number | null;
  parent_run_id: number | null;
  purpose: RunPurpose;
  prompt: string;
  model: string | null;
  permission_mode: PermissionMode;
  max_budget_usd: number | null;
  status: RunStatus;
  session_id: string | null;
  /** Pasted images sent with the prompt; load them from api.attachmentUrl(name). */
  images: string[];
  result: string | null;
  error: string | null;
  cost_usd: number | null;
  num_turns: number | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export type EventKind =
  | "system"
  | "text"
  | "thinking"
  | "tool_use"
  | "tool_result"
  | "approval_request"
  | "approval_decision"
  | "result"
  | "status"
  | "error";

export interface AgentEvent {
  type: "event";
  seq: number;
  kind: EventKind;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  payload: Record<string, any>;
  created_at: string;
}

export type StreamMessage = AgentEvent | { type: "run"; run: AgentRun };

export interface GitSummary {
  branch: string | null;
  remote: string | null;
  github_repo: string | null;
  changed_files: { status: string; path: string }[];
  ahead: number | null;
  behind: number | null;
  commits: { sha: string; short_sha: string; author: string; date: string; subject: string }[];
}

export interface GhIssue {
  number: number;
  title: string;
  body: string;
  state: string;
  url: string;
  labels: { name: string; color: string }[];
  updatedAt: string;
}

export interface GhPull {
  number: number;
  title: string;
  state: string;
  url: string;
  headRefName: string;
  isDraft: boolean;
  author: { login: string };
  updatedAt: string;
  reviewDecision: string;
}

export interface Health {
  ok: boolean;
  version: string;
  default_model: string;
  gh_installed: boolean;
  git_installed: boolean;
}

export const TASK_COLUMNS: { status: TaskStatus; label: string }[] = [
  { status: "backlog", label: "Backlog" },
  { status: "todo", label: "To do" },
  { status: "in_progress", label: "In progress" },
  { status: "review", label: "Review" },
  { status: "done", label: "Done" },
];

export interface ScmFile {
  path: string;
  /** Letter shown next to the file: M, A, D, R, C, T, U (untracked) or ! (conflict). */
  status: string;
  orig_path?: string | null;
}

export type ScmStatus =
  | { is_repo: false }
  | {
      is_repo: true;
      root: string;
      branch: string | null;
      head: string | null;
      upstream: string | null;
      ahead: number | null;
      behind: number | null;
      remotes: string[];
      staged: ScmFile[];
      unstaged: ScmFile[];
      untracked: ScmFile[];
      conflicted: ScmFile[];
      truncated: boolean;
    };

export type ScmRepoStatus = Extract<ScmStatus, { is_repo: true }>;

export type DiffKind = "staged" | "unstaged" | "untracked";

export interface ScmDiff {
  path: string;
  kind: DiffKind;
  diff: string;
  truncated: boolean;
}

/** One file's changes since the last commit (staged and unstaged together). */
export interface ScmFileChanges {
  /** Repo-relative path. */
  path: string;
  tracked: boolean;
  exists: boolean;
  diff: string;
  truncated: boolean;
}

export interface ScmFileContent {
  path: string;
  binary: boolean;
  content: string;
  truncated: boolean;
}

/** Result of commit/push/pull/fetch: git's output plus the refreshed status. */
export interface ScmActionResult {
  status: ScmRepoStatus;
  output?: string;
  sha?: string;
  /** Set when a commit succeeded but the push that followed it failed. */
  error?: string;
}

/** One Claude Code plan limit, e.g. the 5-hour session window. */
export interface UsageWindow {
  key: string;
  label: string;
  /** Percent of the limit used, 0-100. */
  utilization: number;
  resets_at: string | null;
  window_seconds: number;
}

export interface ClaudeUsage {
  /** signed_out: no Claude subscription sign-in; expired: renew it by running `claude`. */
  status: "ok" | "signed_out" | "expired" | "error";
  message: string | null;
  plan: string | null;
  windows: UsageWindow[];
  fetched_at: string | null;
}

/** A named palette. Mirrors `Theme` in models.py; system themes can't be changed or deleted. */
export interface Theme {
  id: string;
  name: string;
  system: boolean;
  /** Every base token -> "#rrggbb", plus any derived tokens the theme pins. */
  colors: Record<string, string>;
}

/** The UI theme: the active theme plus the user's changes. Mirrors `Appearance` in models.py. */
export interface Appearance {
  /** The active theme's id (system or custom). */
  preset: string;
  /** CSS custom property (e.g. "--accent") -> "#rrggbb". Overrides the preset. */
  colors: Record<string, string>;
  font_ui: string | null;
  font_mono: string | null;
  /** Corner radius in px, 0-16. null means the default. */
  radius: number | null;
}

/** How much of the settings agents may see and change. Mirrors `AgentSettingsAccess` in models.py. */
export type AgentSettingsAccess = "off" | "read" | "change";

/** App-wide settings (the Settings page). Mirrors `Preferences` in models.py. */
export interface Preferences {
  /** Preselected when starting a new agent session. */
  default_permission_mode: PermissionMode;
  /** Only the user can change this; agents can't widen their own access. */
  agent_settings_access: AgentSettingsAccess;
}
