import { useState } from "react";
import { api, openExternal } from "../api";
import { timeAgo, useLoader } from "../hooks";
import type { Project } from "../types";

export function GitHubTab({ project, onChanged }: { project: Project; onChanged: () => void }) {
  const repo = project.github_repo;
  const issues = useLoader(() => (repo ? api.issues(project.id) : Promise.resolve([])), [project.id, repo]);
  const pulls = useLoader(() => (repo ? api.pulls(project.id) : Promise.resolve([])), [project.id, repo]);
  const [syncMsg, setSyncMsg] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);

  if (!repo) {
    return <div className="empty">Link a GitHub repository in project Settings (or set a local repo with a GitHub remote).</div>;
  }

  async function sync() {
    setSyncing(true);
    setSyncMsg(null);
    try {
      const r = await api.syncIssues(project.id);
      setSyncMsg(`${r.created} new task(s), ${r.updated} renamed, ${r.closed} closed.`);
      onChanged();
    } catch (e) {
      setSyncMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setSyncing(false);
    }
  }

  const link = (url: string) => (e: React.MouseEvent) => (e.preventDefault(), openExternal(url));

  return (
    <>
      <div className="row" style={{ marginBottom: 16 }}>
        <button className="btn primary" disabled={syncing} onClick={sync}>
          {syncing ? "Syncing…" : "Sync issues → board"}
        </button>
        <span className="muted">{syncMsg ?? "Open issues become Backlog tasks; closed issues mark their tasks Done."}</span>
        <span className="spacer" />
        <button className="btn" onClick={() => (void issues.reload(), void pulls.reload())}>
          Refresh
        </button>
      </div>
      <div className="grid-2">
        <div className="card">
          <h3>Pull requests ({pulls.data?.length ?? "…"})</h3>
          {pulls.error && <p className="error-text">{pulls.error}</p>}
          <ul className="list">
            {pulls.data?.map((pr) => (
              <li key={pr.number}>
                <span className="faint">#{pr.number}</span>
                <a href={pr.url} onClick={link(pr.url)} style={{ flex: 1 }}>
                  {pr.isDraft && "[draft] "}
                  {pr.title}
                </a>
                <span className="faint" style={{ fontSize: 12 }}>
                  {pr.headRefName} · {pr.author?.login} · {timeAgo(pr.updatedAt)}
                </span>
              </li>
            ))}
            {pulls.data?.length === 0 && <li className="faint">No open pull requests.</li>}
          </ul>
        </div>
        <div className="card">
          <h3>Issues ({issues.data?.length ?? "…"})</h3>
          {issues.error && <p className="error-text">{issues.error}</p>}
          <ul className="list">
            {issues.data?.map((i) => (
              <li key={i.number}>
                <span className="faint">#{i.number}</span>
                <a href={i.url} onClick={link(i.url)} style={{ flex: 1 }}>
                  {i.title}
                </a>
                {i.labels.map((l) => (
                  <span key={l.name} className="badge" style={{ background: `#${l.color}33`, color: `#${l.color}` }}>
                    {l.name}
                  </span>
                ))}
              </li>
            ))}
            {issues.data?.length === 0 && <li className="faint">No open issues.</li>}
          </ul>
        </div>
      </div>
    </>
  );
}
