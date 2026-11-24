import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { useLoader, usePolling } from "../hooks";
import { DiffTable, parseDiff } from "./Diff";
import type { DiffKind, Project, ScmFile, ScmRepoStatus, ScmStatus } from "../types";

interface Selection {
  path: string;
  kind: DiffKind;
  orig_path?: string | null;
}

type Notice = { kind: "error" | "info"; text: string };

const LETTER_CLASS: Record<string, string> = { M: "mod", T: "mod", A: "add", U: "add", D: "del", R: "ren", C: "ren", "!": "del" };

const KIND_LABEL: Record<DiffKind, string> = { staged: "Staged", unstaged: "Working tree", untracked: "Untracked" };

function draftKey(projectId: number) {
  return `mercury.commitDraft.${projectId}`;
}

function readDraft(projectId: number): string {
  try {
    return localStorage.getItem(draftKey(projectId)) ?? "";
  } catch {
    return "";
  }
}

function writeDraft(projectId: number, value: string) {
  try {
    if (value) localStorage.setItem(draftKey(projectId), value);
    else localStorage.removeItem(draftKey(projectId));
  } catch {
    /* storage unavailable; the draft just won't survive a reload */
  }
}

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

export function SourceControlTab({ project }: { project: Project }) {
  const status = useLoader<ScmStatus | null>(
    () => (project.repo_path ? api.scmStatus(project.id) : Promise.resolve(null)),
    [project.id, project.repo_path],
  );
  const [selected, setSelected] = useState<Selection | null>(null);
  const [message, setMessageState] = useState(() => readDraft(project.id));
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);

  const setMessage = useCallback(
    (value: string) => {
      setMessageState(value);
      writeDraft(project.id, value);
    },
    [project.id],
  );

  useEffect(() => {
    setMessageState(readDraft(project.id));
    setSelected(null);
    setNotice(null);
  }, [project.id]);

  // Agents and editors change files behind our back: keep the lists fresh.
  const refresh = useCallback(() => {
    if (document.visibilityState === "visible") void status.reload();
  }, [status.reload]);
  usePolling(refresh, 4000, busy === null && !!project.repo_path);
  useEffect(() => {
    window.addEventListener("focus", refresh);
    return () => window.removeEventListener("focus", refresh);
  }, [refresh]);

  const repo: ScmRepoStatus | null = status.data && status.data.is_repo ? status.data : null;

  // Keep the selection pointing at a file that still exists; follow it if it moved between groups.
  useEffect(() => {
    if (!repo || !selected) return;
    const groups: [DiffKind, ScmFile[]][] = [
      [selected.kind, selected.kind === "staged" ? repo.staged : selected.kind === "unstaged" ? repo.unstaged : repo.untracked],
      ["unstaged", repo.unstaged],
      ["unstaged", repo.conflicted],
      ["staged", repo.staged],
      ["untracked", repo.untracked],
    ];
    for (const [kind, files] of groups) {
      const f = files.find((x) => x.path === selected.path);
      if (f) {
        if (kind !== selected.kind) setSelected({ path: f.path, kind, orig_path: f.orig_path });
        return;
      }
    }
    setSelected(null);
  }, [repo, selected]);

  async function act(label: string, fn: () => Promise<void>) {
    setBusy(label);
    setNotice(null);
    try {
      await fn();
    } catch (e) {
      setNotice({ kind: "error", text: errText(e) });
      void status.reload();
    } finally {
      setBusy(null);
    }
  }

  const setRepo = (s: ScmRepoStatus) => status.setData(s);

  if (!project.repo_path) {
    return <div className="empty">Link a local folder in project Settings to use source control.</div>;
  }
  if (status.error && !status.data) {
    return <div className="empty error-text">{status.error}</div>;
  }
  if (status.data === undefined) {
    return <div className="empty">Loading…</div>;
  }
  if (!repo) {
    return (
      <div className="empty">
        <p>This folder isn't a git repository yet.</p>
        {notice && <p className="error-text">{notice.text}</p>}
        <button className="btn primary" disabled={busy !== null} onClick={() => act("init", async () => setRepo(await api.scmInit(project.id)))}>
          Initialize repository
        </button>
      </div>
    );
  }

  const changes = [...repo.unstaged.map((f) => ({ ...f, kind: "unstaged" as const })), ...repo.untracked.map((f) => ({ ...f, kind: "untracked" as const }))].sort(
    (a, b) => a.path.localeCompare(b.path),
  );
  const nothingStaged = repo.staged.length === 0;
  const hasChanges = repo.staged.length + changes.length > 0;
  const canCommit = busy === null && message.trim() !== "" && hasChanges && repo.conflicted.length === 0;
  const commitLabel = nothingStaged ? "Commit All" : "Commit";
  const canPublish = !repo.upstream && repo.remotes.length > 0 && repo.branch !== null && repo.head !== null;

  function commit(push: boolean) {
    if (!canCommit) return;
    void act(push ? "commit-push" : "commit", async () => {
      const r = await api.scmCommit(project.id, { message, stage_all: nothingStaged, push });
      setRepo(r.status);
      setMessage("");
      setNotice(r.error ? { kind: "error", text: r.error } : { kind: "info", text: `Committed ${r.sha}${push ? " and pushed" : ""}.` });
    });
  }

  function sync(label: "push" | "pull" | "fetch") {
    const call = { push: api.scmPush, pull: api.scmPull, fetch: api.scmFetch }[label];
    void act(label, async () => {
      const r = await call(project.id);
      setRepo(r.status);
      const done = { push: "Pushed", pull: "Pulled", fetch: "Fetched" }[label];
      setNotice({ kind: "info", text: `${done}${r.output ? `:\n${r.output}` : "."}` });
    });
  }

  function generate() {
    void act("generate", async () => {
      const r = await api.scmGenerateMessage(project.id);
      setMessage(r.message);
    });
  }

  function discard(paths: string[] | null, label: string) {
    const what = paths === null ? `all ${changes.length} changes` : label;
    if (!window.confirm(`Discard ${what}? This can't be undone. New (untracked) files will be deleted.`)) return;
    void act("discard", async () => setRepo(await api.scmDiscard(project.id, paths)));
  }

  const stage = (paths: string[] | null) => act("stage", async () => setRepo(await api.scmStage(project.id, paths)));
  const unstage = (paths: string[] | null) => act("unstage", async () => setRepo(await api.scmUnstage(project.id, paths)));

  return (
    <div className="scm">
      <div className="scm-side">
        <div className="scm-toolbar">
          <span className="scm-branch" title={repo.upstream ? `Tracking ${repo.upstream}` : "Not tracking a remote branch"}>
            ⎇ {repo.branch ?? (repo.head ? `detached at ${repo.head.slice(0, 7)}` : "no commits")}
          </span>
          <span className="spacer" />
          <button className="btn small" disabled={busy !== null || repo.remotes.length === 0} onClick={() => sync("fetch")} title="Fetch from the remote">
            {busy === "fetch" ? "Fetching…" : "Fetch"}
          </button>
          <button className="btn small" disabled={busy !== null || !repo.upstream} onClick={() => sync("pull")} title={repo.upstream ? `Pull from ${repo.upstream}` : "No remote branch to pull from"}>
            {busy === "pull" ? "Pulling…" : `Pull${repo.behind ? ` ↓${repo.behind}` : ""}`}
          </button>
          {canPublish ? (
            <button className="btn small" disabled={busy !== null} onClick={() => sync("push")} title={`Push this branch to ${repo.remotes.includes("origin") ? "origin" : repo.remotes[0]} and track it`}>
              {busy === "push" ? "Publishing…" : "Publish branch"}
            </button>
          ) : (
            <button className="btn small" disabled={busy !== null || !repo.upstream} onClick={() => sync("push")} title={repo.upstream ? `Push to ${repo.upstream}` : repo.remotes.length ? "Make a commit first" : "This repository has no remote"}>
              {busy === "push" ? "Pushing…" : `Push${repo.ahead ? ` ↑${repo.ahead}` : ""}`}
            </button>
          )}
          <button className="btn small ghost" disabled={busy !== null} onClick={() => void status.reload()} title="Refresh">
            ⟳
          </button>
        </div>

        <div className="scm-commit">
          <div className="scm-message">
            <textarea
              value={message}
              placeholder={`Message (Ctrl+Enter to commit on "${repo.branch ?? "HEAD"}")`}
              onChange={(e) => setMessage(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                  e.preventDefault();
                  commit(false);
                }
              }}
              disabled={busy === "generate"}
            />
            <button
              className="btn small ghost scm-generate"
              onClick={generate}
              disabled={busy !== null || !hasChanges}
              title={nothingStaged ? "Write a message for all changes with Claude" : "Write a message for the staged changes with Claude"}
            >
              {busy === "generate" ? "Writing…" : "✨ Generate"}
            </button>
          </div>
          <div className="row">
            <button
              className="btn primary"
              style={{ flex: 1 }}
              disabled={!canCommit}
              onClick={() => commit(false)}
              title={nothingStaged ? "Nothing is staged, so every change will be staged and committed" : "Commit the staged changes"}
            >
              {busy === "commit" ? "Committing…" : `✓ ${commitLabel}`}
            </button>
            <button className="btn" disabled={!canCommit || (!repo.upstream && repo.remotes.length === 0)} onClick={() => commit(true)} title="Commit, then push">
              {busy === "commit-push" ? "Working…" : `${commitLabel} & Push`}
            </button>
          </div>
        </div>

        {notice && (
          <div className={`scm-notice ${notice.kind}`}>
            <pre>{notice.text}</pre>
            <button className="btn small ghost" onClick={() => setNotice(null)} title="Dismiss">
              ✕
            </button>
          </div>
        )}

        <div className="scm-groups">
          {repo.conflicted.length > 0 && (
            <FileGroup
              title="Merge Changes"
              files={repo.conflicted.map((f) => ({ ...f, kind: "unstaged" as const }))}
              selected={selected}
              onSelect={setSelected}
              disabled={busy !== null}
              groupActions={[]}
              fileActions={(f) => [{ label: "+", title: "Mark resolved (stage)", run: () => stage([f.path]) }]}
            />
          )}
          {repo.staged.length > 0 && (
            <FileGroup
              title="Staged Changes"
              files={repo.staged.map((f) => ({ ...f, kind: "staged" as const }))}
              selected={selected}
              onSelect={setSelected}
              disabled={busy !== null}
              groupActions={[{ label: "−", title: "Unstage all", run: () => unstage(null) }]}
              fileActions={(f) => [{ label: "−", title: "Unstage", run: () => unstage([f.path]) }]}
            />
          )}
          <FileGroup
            title="Changes"
            files={changes}
            selected={selected}
            onSelect={setSelected}
            disabled={busy !== null}
            groupActions={
              changes.length
                ? [
                    { label: "↶", title: "Discard all changes", run: () => discard(null, "") },
                    { label: "+", title: "Stage all changes", run: () => stage(changes.map((f) => f.path)) },
                  ]
                : []
            }
            fileActions={(f) => [
              { label: "↶", title: f.kind === "untracked" ? "Delete this new file" : "Discard changes", run: () => discard([f.path], f.path) },
              { label: "+", title: "Stage", run: () => stage([f.path]) },
            ]}
            empty={repo.staged.length === 0 && repo.conflicted.length === 0 ? "No changes. The working tree is clean." : undefined}
          />
          {repo.truncated && <p className="faint scm-hint">Showing the first 5,000 files. Add a .gitignore to hide build output.</p>}
        </div>
      </div>

      <DiffView projectId={project.id} selection={selected} statusData={repo} />
    </div>
  );
}

interface Action {
  label: string;
  title: string;
  run: () => unknown;
}

type KindedFile = ScmFile & { kind: DiffKind };

function FileGroup(props: {
  title: string;
  files: KindedFile[];
  selected: Selection | null;
  onSelect: (s: Selection) => void;
  disabled: boolean;
  groupActions: Action[];
  fileActions: (f: KindedFile) => Action[];
  empty?: string;
}) {
  const [open, setOpen] = useState(true);
  return (
    <div className="scm-group">
      <div className="scm-group-head" onClick={() => setOpen(!open)}>
        <span className="scm-chevron">{open ? "▾" : "▸"}</span>
        <span className="scm-group-title">{props.title}</span>
        <span className="scm-actions">
          {props.groupActions.map((a) => (
            <button key={a.title} className="scm-icon" title={a.title} disabled={props.disabled} onClick={(e) => (e.stopPropagation(), a.run())}>
              {a.label}
            </button>
          ))}
        </span>
        <span className="badge">{props.files.length}</span>
      </div>
      {open && (
        <ul className="scm-files">
          {props.files.map((f) => {
            const slash = f.path.lastIndexOf("/");
            const name = f.path.slice(slash + 1);
            const dir = slash >= 0 ? f.path.slice(0, slash) : "";
            const active = props.selected?.path === f.path && props.selected.kind === f.kind;
            return (
              <li
                key={`${f.kind}:${f.path}`}
                className={`scm-file ${active ? "active" : ""}`}
                onClick={() => props.onSelect({ path: f.path, kind: f.kind, orig_path: f.orig_path })}
                title={f.orig_path ? `${f.orig_path} → ${f.path}` : f.path}
              >
                <span className={`scm-name ${f.status === "D" ? "deleted" : ""}`}>{name}</span>
                <span className="scm-dir">{dir}</span>
                <span className="scm-actions">
                  {props.fileActions(f).map((a) => (
                    <button key={a.title} className="scm-icon" title={a.title} disabled={props.disabled} onClick={(e) => (e.stopPropagation(), a.run())}>
                      {a.label}
                    </button>
                  ))}
                </span>
                <span className={`scm-letter ${LETTER_CLASS[f.status] ?? ""}`}>{f.status}</span>
              </li>
            );
          })}
          {props.files.length === 0 && props.empty && <li className="faint scm-hint">{props.empty}</li>}
        </ul>
      )}
    </div>
  );
}

function DiffView({ projectId, selection, statusData }: { projectId: number; selection: Selection | null; statusData: ScmRepoStatus }) {
  const diff = useLoader(
    () => (selection ? api.scmDiff(projectId, selection.path, selection.kind, selection.orig_path) : Promise.resolve(null)),
    [projectId, selection?.path, selection?.kind, selection?.orig_path],
  );
  // Re-read the diff whenever the status refreshes, so edits made elsewhere show up.
  const reloadDiff = diff.reload;
  useEffect(() => {
    if (selection) void reloadDiff();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusData]);

  const lines = useMemo(() => (diff.data ? parseDiff(diff.data.diff) : []), [diff.data]);

  if (!selection) {
    return <div className="scm-diff empty">Select a file to see its changes.</div>;
  }
  return (
    <div className="scm-diff">
      <div className="scm-diff-head">
        <code>{selection.orig_path ? `${selection.orig_path} → ${selection.path}` : selection.path}</code>
        <span className="faint">{KIND_LABEL[selection.kind]}</span>
      </div>
      <div className="scm-diff-body">
        {diff.error && <p className="error-text" style={{ padding: 12 }}>{diff.error}</p>}
        {diff.data && lines.length === 0 && <p className="faint" style={{ padding: 12 }}>No textual changes.</p>}
        {lines.length > 0 && <DiffTable lines={lines} />}
        {diff.data?.truncated && <p className="faint" style={{ padding: 12 }}>Diff truncated: the file is too large to show in full.</p>}
      </div>
    </div>
  );
}
