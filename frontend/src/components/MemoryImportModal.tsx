import { useEffect, useState } from "react";
import { api } from "../api";
import type { DocStatus, MemoryImportAction, MemoryImportPreview, Project } from "../types";

const ACTION_LABELS: Record<MemoryImportAction, string> = {
  create: "New",
  update: "Changed in memory",
  unchanged: "Already imported",
  edited: "Edited in Mercury",
};

interface Choice {
  include: boolean;
  status: DocStatus | "";
}

/** Copy the project's Claude Code memory into docs. The memory folder itself is never changed. */
export function MemoryImportModal({
  project,
  onClose,
  onImported,
}: {
  project: Project;
  onClose: () => void;
  onImported: () => void;
}) {
  const [preview, setPreview] = useState<MemoryImportPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [folder, setFolder] = useState("");
  const [choices, setChoices] = useState<Record<string, Choice>>({});
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  async function load(memoryDir?: string) {
    setError(null);
    try {
      const p = await api.previewMemoryImport(project.id, memoryDir);
      setPreview(p);
      setFolder(p.memory_dir);
      setChoices(Object.fromEntries(p.items.map((i) => [i.key, { include: i.include, status: i.action === "create" ? "current" : "" }])));
    } catch (e) {
      setPreview(null);
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project.id]);

  const items = preview?.items ?? [];
  const selected = items.filter((i) => i.action !== "unchanged" && choices[i.key]?.include).length;

  function set(key: string, patch: Partial<Choice>) {
    setChoices((c) => ({ ...c, [key]: { ...c[key], ...patch } }));
  }

  async function apply() {
    setBusy(true);
    try {
      const r = await api.applyMemoryImport(project.id, {
        memory_dir: preview?.memory_dir,
        choices: Object.fromEntries(
          Object.entries(choices).map(([k, c]) => [k, { include: c.include, ...(c.status ? { status: c.status } : {}) }]),
        ),
      });
      setResult(`Created ${r.created} and updated ${r.updated} docs.`);
      onImported();
      await load(preview?.memory_dir);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function removeAll() {
    if (!confirm("Delete every doc imported from Claude Code memory, including any edits made to them in Mercury?")) return;
    const r = await api.removeMemoryImport(project.id);
    setResult(`Deleted ${r.deleted} imported docs.`);
    onImported();
    await load(preview?.memory_dir);
  }

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal memory-import">
        <h2>Import Claude Code memory</h2>
        <p className="muted">
          Copies this project's Claude Code memory into Mercury docs. The memory folder isn't changed, and later edits
          on either side aren't synced. Feedback memories become one rules doc that every agent run follows. Long
          memories are split into a doc per section. Running this again picks up new and changed memories, but never
          overwrites a doc you've edited in Mercury unless you tick it.
        </p>
        <div className="row">
          <input value={folder} onChange={(e) => setFolder(e.target.value)} placeholder="Memory folder (~/.claude/projects/…/memory)" />
          <button className="btn small" onClick={() => load(folder || undefined)}>
            Look
          </button>
        </div>
        {error && <p className="error-text">{error}</p>}
        {result && <p className="ok-text">{result}</p>}
        {preview && (
          <table className="memory-import-table">
            <thead>
              <tr>
                <th />
                <th>Doc</th>
                <th>State</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {items.map((i) => (
                <tr key={i.key} className={i.action === "unchanged" ? "faint" : ""}>
                  <td>
                    <input
                      type="checkbox"
                      disabled={i.action === "unchanged"}
                      checked={i.action !== "unchanged" && !!choices[i.key]?.include}
                      onChange={(e) => set(i.key, { include: e.target.checked })}
                    />
                  </td>
                  <td>
                    <div>
                      {i.pinned && "📌 "}
                      {i.title} {i.kind === "rules" && <span className="badge">rules</span>}
                    </div>
                    <div className="faint">
                      {i.file} · {Math.max(1, Math.round(i.chars / 1000))}k chars
                      {i.collection && i.action === "create" && ` · into collection "${i.collection}"`}
                      {!i.summary && " · summary written after import"}
                    </div>
                    {i.flags.map((f) => (
                      <div key={f} className="warn-text">
                        {f}
                      </div>
                    ))}
                  </td>
                  <td className="nowrap">{ACTION_LABELS[i.action]}</td>
                  <td>
                    <select
                      className="small"
                      disabled={i.action === "unchanged"}
                      value={choices[i.key]?.status ?? ""}
                      onChange={(e) => set(i.key, { status: e.target.value as DocStatus | "" })}
                    >
                      {i.action !== "create" && <option value="">(keep)</option>}
                      <option value="current">Current</option>
                      <option value="draft">Draft</option>
                      <option value="superseded">Superseded</option>
                      <option value="archived">Archived</option>
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="modal-actions">
          <button className="btn primary" disabled={!preview || busy || selected === 0} onClick={apply}>
            {busy ? "Importing…" : `Import ${selected} doc${selected === 1 ? "" : "s"}`}
          </button>
          <button className="btn" onClick={onClose}>
            Close
          </button>
          <span className="spacer" />
          {items.some((i) => i.doc_id !== null) && (
            <button className="btn danger" onClick={removeAll}>
              Remove imported docs
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
