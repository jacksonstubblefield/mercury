import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { timeAgo, useLoader, usePolling } from "../hooks";
import type { AgentEvent, Project, ScmRepoStatus } from "../types";
import { type DiffLine, DiffStat, DiffTable, diffStats, lineDiff, parseDiff } from "./Diff";
import { Markdown } from "./Markdown";

// ------------------------------------------------------------------------- edits from agent events

/** Claude Code's file-writing tools. Changes made through shell commands don't appear here. */
const EDIT_TOOLS = new Set(["Edit", "MultiEdit", "Write", "NotebookEdit"]);

export function isEditTool(name: string): boolean {
  return EDIT_TOOLS.has(name);
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Input = Record<string, any>;

/** The diff an edit tool call describes, worked out from its input alone. */
export function editDiff(name: string, input: Input): { path: string; lines: DiffLine[]; note?: string } | null {
  if (!input) return null;
  const str = (v: unknown) => (typeof v === "string" ? v : "");
  switch (name) {
    case "Edit":
      return {
        path: str(input.file_path),
        lines: lineDiff(str(input.old_string), str(input.new_string)),
        note: input.replace_all ? "every occurrence" : undefined,
      };
    case "MultiEdit": {
      const edits: Input[] = Array.isArray(input.edits) ? input.edits : [];
      const lines: DiffLine[] = [];
      edits.forEach((e, i) => {
        if (edits.length > 1) lines.push({ type: "hunk", text: `Edit ${i + 1} of ${edits.length}${e.replace_all ? " (every occurrence)" : ""}` });
        lines.push(...lineDiff(str(e.old_string), str(e.new_string)));
      });
      return { path: str(input.file_path), lines };
    }
    case "Write":
      return { path: str(input.file_path), lines: lineDiff("", str(input.content)), note: "whole file" };
    case "NotebookEdit": {
      const cell = input.cell_id ? `cell ${input.cell_id}` : "new cell";
      if (input.edit_mode === "delete") return { path: str(input.notebook_path), lines: [{ type: "meta", text: `Deleted ${cell}` }] };
      return { path: str(input.notebook_path), lines: lineDiff("", str(input.new_source)), note: input.edit_mode === "insert" ? "inserted cell" : cell };
    }
  }
  return null;
}

export interface FileEdit {
  runId: number;
  seq: number;
  tool: string;
  path: string;
  lines: DiffLine[];
  note?: string;
  added: number;
  removed: number;
  /** The tool result says the file was created rather than changed. */
  created: boolean;
  at: string;
}

/** Edits from one turn that the tool reported as successful. */
export function fileEditsFrom(runId: number, events: AgentEvent[]): FileEdit[] {
  const results = new Map(events.filter((e) => e.kind === "tool_result").map((e) => [e.payload.tool_use_id, e.payload]));
  const out: FileEdit[] = [];
  for (const e of events) {
    if (e.kind !== "tool_use" || !isEditTool(e.payload.name)) continue;
    const result = results.get(e.payload.id);
    if (!result || result.is_error) continue; // still running, or it failed and changed nothing
    const d = editDiff(e.payload.name, e.payload.input);
    if (!d?.path) continue;
    out.push({
      runId,
      seq: e.seq,
      tool: e.payload.name,
      ...d,
      ...diffStats(d.lines),
      created: /created/i.test(String(result.content ?? "")),
      at: e.created_at,
    });
  }
  return out;
}

// ------------------------------------------------------------------------- paths

const norm = (p: string) => p.replace(/\\/g, "/").replace(/\/+$/, "");
/** Paths agents report can differ in case from the repo path on Windows. */
export const pathKey = (p: string) => norm(p).toLowerCase();

/** `path` relative to `base`, or null if it isn't inside it. */
function relativeTo(path: string, base: string | null): string | null {
  if (!base) return null;
  const p = norm(path);
  const b = norm(base);
  if (p.toLowerCase().startsWith(b.toLowerCase() + "/")) return p.slice(b.length + 1);
  if (!/^([a-zA-Z]:)?\//.test(p)) return p; // already relative
  return null;
}

export function displayPath(path: string, repoPath: string | null): string {
  return relativeTo(path, repoPath) ?? norm(path);
}

function splitPath(p: string) {
  const i = p.lastIndexOf("/");
  return { name: p.slice(i + 1), dir: i >= 0 ? p.slice(0, i) : "" };
}

// ------------------------------------------------------------------------- transcript card

/** Lets transcript events open a file in the session's changes panel. */
export const FilesContext = createContext<{ repoPath: string | null; openFile: (path: string) => void } | null>(null);

/** An edit tool call in the transcript, shown as a diff. */
export function EditCard({ name, input, result }: { name: string; input: Input; result?: Input }) {
  const files = useContext(FilesContext);
  const d = useMemo(() => editDiff(name, input), [name, input]);
  if (!d) return null;
  const { added, removed } = diffStats(d.lines);
  const failed = !!result?.is_error;
  const shown = displayPath(d.path, files?.repoPath ?? null);
  const verb = name === "Write" ? (result && /created/i.test(String(result.content ?? "")) ? "Create" : "Write") : name;
  return (
    <details className={`ev-tool ev-edit ${failed ? "failed" : ""}`} open={!failed}>
      <summary>
        <b>{verb}</b>{" "}
        <a
          href="#"
          className="ev-edit-path"
          title={`${d.path}\nOpen in the session's changes panel`}
          onClick={(e) => {
            e.preventDefault();
            files?.openFile(d.path);
          }}
        >
          {shown}
        </a>{" "}
        {!failed && <DiffStat added={added} removed={removed} />}
        {d.note && !failed && <span className="faint"> · {d.note}</span>}
        {failed && <span className="error-text"> · failed, file unchanged</span>}
        {!result && <span className="faint"> · pending</span>}
      </summary>
      <div className="ev-edit-diff">
        <DiffTable lines={d.lines} numbers={false} limit={name === "Write" || name === "NotebookEdit" ? 40 : 120} />
      </div>
      {failed && (
        <div className="body err">
          <pre>{result.content}</pre>
        </div>
      )}
    </details>
  );
}

// ------------------------------------------------------------------------- session panel

interface ChangedFile {
  key: string;
  path: string; // as the agent reported it (absolute), or repo-relative for "other" files
  shown: string;
  inRepo: boolean;
  edits: FileEdit[];
  created: boolean;
}

type ViewMode = "commit" | "session" | "file";

export function ChangesPanel({
  project,
  edits,
  active,
  selected,
  onSelect,
  onClose,
}: {
  project: Project;
  edits: FileEdit[];
  active: boolean;
  selected: string | null;
  onSelect: (path: string) => void;
  onClose: () => void;
}) {
  const repoPath = project.repo_path;
  const files = useMemo(() => {
    const byKey = new Map<string, ChangedFile>();
    for (const e of edits) {
      const key = pathKey(e.path);
      let f = byKey.get(key);
      if (!f) {
        const rel = relativeTo(e.path, repoPath);
        f = { key, path: e.path, shown: rel ?? norm(e.path), inRepo: rel !== null, edits: [], created: e.created };
        byKey.set(key, f);
      }
      f.edits.push(e);
    }
    return [...byKey.values()].sort((a, b) => Number(b.inRepo) - Number(a.inRepo) || a.shown.localeCompare(b.shown));
  }, [edits, repoPath]);

  // Uncommitted changes the file tools didn't make (shell commands, other editors, earlier work).
  const status = useLoader(() => (repoPath ? api.scmStatus(project.id).catch(() => null) : Promise.resolve(null)), [project.id, repoPath]);
  usePolling(status.reload, 4000, active);
  useEffect(() => {
    void status.reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [edits.length, active]);
  const repo: ScmRepoStatus | null = status.data && status.data.is_repo ? status.data : null;
  const others = useMemo(() => {
    if (!repo) return [];
    const mine = new Set(files.map((f) => f.key));
    const seen = new Set<string>();
    const out: { path: string; abs: string; status: string }[] = [];
    for (const f of [...repo.conflicted, ...repo.staged, ...repo.unstaged, ...repo.untracked]) {
      const abs = `${norm(repo.root)}/${f.path}`;
      if (mine.has(pathKey(abs)) || seen.has(f.path)) continue;
      seen.add(f.path);
      out.push({ path: f.path, abs, status: f.status });
    }
    return out.sort((a, b) => a.path.localeCompare(b.path));
  }, [repo, files]);

  const selFile = selected ? files.find((f) => f.key === pathKey(selected)) : undefined;
  const selOther = !selFile && selected ? others.find((o) => pathKey(o.abs) === pathKey(selected)) : undefined;

  return (
    <div className="changes-panel">
      <div className="changes-head">
        <b>Files changed</b>
        <span className="badge">{files.length}</span>
        <span className="spacer" />
        <button className="btn small ghost" onClick={onClose} title="Close">
          ✕
        </button>
      </div>
      <div className="changes-list">
        {files.length === 0 && <p className="faint scm-hint">Claude hasn't edited any files in this session yet.</p>}
        <ul className="scm-files">
          {files.map((f) => {
            const { name, dir } = splitPath(f.shown);
            const added = f.edits.reduce((s, e) => s + e.added, 0);
            const removed = f.edits.reduce((s, e) => s + e.removed, 0);
            return (
              <li
                key={f.key}
                className={`scm-file ${selFile?.key === f.key ? "active" : ""}`}
                onClick={() => onSelect(f.path)}
                title={f.inRepo ? f.path : `${f.path}\n(outside the project folder)`}
              >
                <span className="scm-name">{name}</span>
                <span className="scm-dir">{f.inRepo ? dir : `${dir} · outside project`}</span>
                <span className="faint changes-count">{f.edits.length > 1 ? `${f.edits.length} edits` : f.created ? "new" : ""}</span>
                <DiffStat added={added} removed={removed} />
              </li>
            );
          })}
        </ul>
        {others.length > 0 && (
          <>
            <div className="changes-subhead" title="Not changed through Claude's file tools in this session: shell commands, other editors, or earlier work.">
              Other uncommitted changes <span className="badge">{others.length}</span>
            </div>
            <ul className="scm-files">
              {others.map((o) => {
                const { name, dir } = splitPath(o.path);
                return (
                  <li key={o.path} className={`scm-file ${selOther?.path === o.path ? "active" : ""}`} onClick={() => onSelect(o.abs)} title={o.path}>
                    <span className="scm-name">{name}</span>
                    <span className="scm-dir">{dir}</span>
                    <span className="scm-letter">{o.status}</span>
                  </li>
                );
              })}
            </ul>
          </>
        )}
      </div>
      {selFile ? (
        <FileViewer key={selFile.key} project={project} path={selFile.path} shown={selFile.shown} inRepo={selFile.inRepo} edits={selFile.edits} refreshKey={status.data} />
      ) : selOther ? (
        <FileViewer key={selOther.abs} project={project} path={selOther.abs} shown={selOther.path} inRepo edits={[]} refreshKey={status.data} />
      ) : (
        <div className="changes-viewer empty faint">Select a file to see what changed.</div>
      )}
    </div>
  );
}

const isMarkdown = (p: string) => /\.(md|markdown|mdx)$/i.test(p);

function FileViewer({
  project,
  path,
  shown,
  inRepo,
  edits,
  refreshKey,
}: {
  project: Project;
  path: string;
  shown: string;
  inRepo: boolean;
  edits: FileEdit[];
  refreshKey: unknown;
}) {
  const modes: { id: ViewMode; label: string; title: string }[] = [
    ...(inRepo ? [{ id: "commit" as const, label: "Since last commit", title: "Everything that differs from the last commit, whoever changed it" }] : []),
    ...(edits.length ? [{ id: "session" as const, label: `Claude's edits (${edits.length})`, title: "Each edit Claude made to this file in this session, in order" }] : []),
    ...(inRepo ? [{ id: "file" as const, label: "File", title: "The file as it is now" }] : []),
  ];
  const [mode, setMode] = useState<ViewMode>(modes[0]?.id ?? "session");
  const [raw, setRaw] = useState(false);

  return (
    <div className="changes-viewer">
      <div className="changes-viewer-head">
        <code title={path}>{shown}</code>
        <span className="spacer" />
        {mode === "file" && isMarkdown(path) && (
          <button className="btn small ghost" onClick={() => setRaw(!raw)}>
            {raw ? "Rendered" : "Source"}
          </button>
        )}
      </div>
      {modes.length > 1 && (
        <div className="seg">
          {modes.map((m) => (
            <button key={m.id} className={mode === m.id ? "active" : ""} onClick={() => setMode(m.id)} title={m.title}>
              {m.label}
            </button>
          ))}
        </div>
      )}
      <div className="changes-viewer-body">
        {!inRepo && <p className="faint scm-hint">This file is outside the project folder, so only Claude's own edits can be shown.</p>}
        {mode === "commit" && <SinceCommit projectId={project.id} path={path} refreshKey={refreshKey} />}
        {mode === "session" && <SessionEdits edits={edits} />}
        {mode === "file" && <FileContents projectId={project.id} path={path} raw={raw} refreshKey={refreshKey} />}
      </div>
    </div>
  );
}

/** Reload when the panel's status poll changes, so the view follows the agent's edits. */
function useFreshLoader<T>(load: () => Promise<T>, deps: unknown[], refreshKey: unknown) {
  const res = useLoader(load, deps);
  useEffect(() => {
    void res.reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);
  return res;
}

function SinceCommit({ projectId, path, refreshKey }: { projectId: number; path: string; refreshKey: unknown }) {
  const res = useFreshLoader(() => api.scmFileChanges(projectId, path), [projectId, path], refreshKey);
  const lines = useMemo(() => (res.data ? parseDiff(res.data.diff) : []), [res.data]);
  if (res.error) return <p className="error-text scm-hint">{res.error}</p>;
  if (!res.data) return <p className="faint scm-hint">Loading…</p>;
  if (!res.data.exists && !res.data.tracked) return <p className="faint scm-hint">This file no longer exists.</p>;
  if (lines.length === 0) return <p className="faint scm-hint">No changes since the last commit. Anything Claude changed here has been committed or undone.</p>;
  return (
    <>
      {!res.data.tracked && <p className="faint scm-hint">New file, not yet committed.</p>}
      <DiffTable lines={lines} />
      {res.data.truncated && <p className="faint scm-hint">Diff truncated: the file is too large to show in full.</p>}
    </>
  );
}

function SessionEdits({ edits }: { edits: FileEdit[] }) {
  return (
    <div className="session-edits">
      {edits.map((e) => (
        <div key={`${e.runId}:${e.seq}`} className="session-edit">
          <div className="session-edit-head faint">
            {e.tool === "Write" ? (e.created ? "Created" : "Rewrote") : e.tool} · <DiffStat added={e.added} removed={e.removed} />
            {e.note && ` · ${e.note}`} · {timeAgo(e.at)}
          </div>
          <DiffTable lines={e.lines} numbers={false} limit={200} />
        </div>
      ))}
    </div>
  );
}

function FileContents({ projectId, path, raw, refreshKey }: { projectId: number; path: string; raw: boolean; refreshKey: unknown }) {
  const res = useFreshLoader(() => api.scmFile(projectId, path), [projectId, path], refreshKey);
  if (res.error) return <p className="error-text scm-hint">{res.error}</p>;
  if (!res.data) return <p className="faint scm-hint">Loading…</p>;
  if (res.data.binary) return <p className="faint scm-hint">Binary file: not shown.</p>;
  return (
    <>
      {isMarkdown(path) && !raw ? (
        <div className="file-markdown">
          <Markdown>{res.data.content}</Markdown>
        </div>
      ) : (
        <table className="diff">
          <tbody>
            {res.data.content
              .replace(/\r?\n$/, "")
              .split(/\r?\n/)
              .map((text, i) => (
                <tr key={i}>
                  <td className="ln">{i + 1}</td>
                  <td className="code">{text}</td>
                </tr>
              ))}
          </tbody>
        </table>
      )}
      {res.data.truncated && <p className="faint scm-hint">Showing the first 1 MB.</p>}
    </>
  );
}
