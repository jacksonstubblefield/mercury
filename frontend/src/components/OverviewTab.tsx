import { api } from "../api";
import { timeAgo, useLoader } from "../hooks";
import { TASK_COLUMNS, type Project } from "../types";
import { runLabel } from "../sessions";
import { Markdown } from "./Markdown";

interface Props {
  project: Project;
  onOpenRun: (id: number) => void;
}

export function OverviewTab({ project, onOpenRun }: Props) {
  const tasks = useLoader(() => api.tasks(project.id), [project.id]);
  const runs = useLoader(() => api.runs(project.id), [project.id]);
  const git = useLoader(
    () => (project.repo_path ? api.git(project.id) : Promise.resolve(null)),
    [project.id, project.repo_path],
  );

  const counts = TASK_COLUMNS.map((c) => ({ ...c, n: tasks.data?.filter((t) => t.status === c.status).length ?? 0 }));
  const totalCost = (runs.data ?? []).reduce((sum, r) => sum + (r.cost_usd ?? 0), 0);

  return (
    <div className="grid-2">
      <div className="card">
        <h3>About</h3>
        {project.description ? <Markdown>{project.description}</Markdown> : <p className="faint">No description yet. Add one in Settings.</p>}
      </div>

      <div className="card">
        <h3>Tasks</h3>
        <div className="stat-row">
          {counts.map((c) => (
            <div className="stat" key={c.status}>
              <div className="n">{c.n}</div>
              <div className="l">{c.label}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="card">
        <h3>Repository</h3>
        {!project.repo_path && <p className="faint">Link a local folder in Settings to see git status and run agents.</p>}
        {git.error && <p className="error-text">{git.error}</p>}
        {git.data && (
          <>
            <div className="row muted" style={{ marginBottom: 8, flexWrap: "wrap" }}>
              <span>
                Branch <code>{git.data.branch ?? "(no commits)"}</code>
              </span>
              {git.data.ahead !== null && (
                <span>
                  ↑{git.data.ahead} ↓{git.data.behind}
                </span>
              )}
              <span>{git.data.changed_files.length} uncommitted change(s)</span>
              <span className="spacer" />
              <button className="btn small" onClick={git.reload}>
                Refresh
              </button>
            </div>
            <ul className="list">
              {git.data.commits.map((c) => (
                <li key={c.sha}>
                  <code className="faint commit-sha">{c.short_sha}</code>
                  <span className="commit-subject">{c.subject}</span>
                  <span className="faint commit-meta" title={`${c.author} · ${c.date}`}>
                    {c.author} · {timeAgo(c.date)}
                  </span>
                </li>
              ))}
              {git.data.commits.length === 0 && <li className="faint">No commits yet.</li>}
            </ul>
          </>
        )}
      </div>

      <div className="card">
        <h3>Recent agent runs</h3>
        <p className="faint" style={{ marginTop: 0 }}>
          {runs.data?.length ?? 0} runs · ${totalCost.toFixed(2)} total
        </p>
        <ul className="list">
          {(runs.data ?? []).slice(0, 8).map((r) => (
            <li key={r.id} style={{ cursor: "pointer" }} onClick={() => onOpenRun(r.id)}>
              <span className={`status ${r.status}`}>{r.status.replace("_", " ")}</span>
              <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{runLabel(r)}</span>
              <span className="faint" style={{ fontSize: 12 }}>
                {timeAgo(r.created_at)}
              </span>
            </li>
          ))}
          {runs.data?.length === 0 && <li className="faint">No runs yet.</li>}
        </ul>
      </div>
    </div>
  );
}
