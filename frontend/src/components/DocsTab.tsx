import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { timeAgo, useLoader, usePolling } from "../hooks";
import type { Doc, DocCollection, DocKind, DocListItem, DocSearchHit, DocSearchStatus, DocStatus, Project } from "../types";
import { Markdown } from "./Markdown";
import { MemoryImportModal } from "./MemoryImportModal";
import { Organizer } from "./Organizer";

const MAX_LISTED = 300;
const RETIRED: DocStatus[] = ["superseded", "archived"];
const STATUS_LABELS: Record<DocStatus, string> = {
  current: "Current",
  draft: "Draft",
  superseded: "Superseded",
  archived: "Archived",
};

type Selection = { kind: "doc"; id: number } | { kind: "collection"; id: number } | null;
type GroupKey = number | "unfiled";

export function DocsTab({ project, onOpenRun }: { project: Project; onOpenRun: (runId: number) => void }) {
  const docs = useLoader(() => api.docs(project.id), [project.id]);
  const cols = useLoader(() => api.collections(project.id), [project.id]);
  const [sel, setSel] = useState<Selection>(null);
  const [expanded, setExpanded] = useState<Set<GroupKey>>(new Set());
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<DocSearchHit[] | null>(null);
  const [showRetired, setShowRetired] = useState(false);
  const [importing, setImporting] = useState(false);
  const [naming, setNaming] = useState<{ parentId: number | null } | null>(null);
  // Bumped when an organizer finishes, so the open doc or collection shows what it changed.
  const [paneVersion, setPaneVersion] = useState(0);

  const allDocs = docs.data ?? [];
  const collections = cols.data ?? [];
  const visible = useMemo(() => allDocs.filter((d) => showRetired || !RETIRED.includes(d.status)), [allDocs, showRetired]);
  const retiredCount = allDocs.filter((d) => RETIRED.includes(d.status)).length;
  const grouped = useMemo(() => {
    const m = new Map<GroupKey, DocListItem[]>();
    for (const d of visible) {
      const key = d.collection_id ?? "unfiled";
      m.set(key, [...(m.get(key) ?? []), d]);
    }
    return m;
  }, [visible]);
  const topLevel = collections.filter((c) => c.parent_id === null);
  const childrenOf = (id: number) => collections.filter((c) => c.parent_id === id);

  useEffect(() => {
    if (sel === null && collections.length === 0 && visible.length > 0) setSel({ kind: "doc", id: visible[0].id });
  }, [visible, collections.length, sel]);

  // Server-side search, debounced.
  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setHits(null);
      return;
    }
    const id = window.setTimeout(async () => setHits(await api.searchDocs(project.id, q)), 250);
    return () => window.clearTimeout(id);
  }, [query, project.id]);

  function toggle(key: GroupKey, open?: boolean) {
    setExpanded((s) => {
      const next = new Set(s);
      if (open ?? !next.has(key)) next.add(key);
      else next.delete(key);
      return next;
    });
  }

  async function createDoc() {
    const collectionId = sel?.kind === "collection" ? sel.id : null;
    const created = await api.createDoc(project.id, { title: "Untitled", body: "", collection_id: collectionId });
    setQuery("");
    if (collectionId !== null) toggle(collectionId, true);
    await Promise.all([docs.reload(), cols.reload()]);
    setSel({ kind: "doc", id: created.id });
  }

  function onDocSaved(d: Doc) {
    const before = allDocs.find((x) => x.id === d.id);
    docs.setData(allDocs.map((x) => (x.id === d.id ? { ...x, ...listItem(d) } : x)));
    if (before && before.collection_id !== d.collection_id) void cols.reload();
  }

  function reloadAll() {
    void docs.reload();
    void cols.reload();
  }

  const docRow = (d: DocListItem, depth: number) => (
    <DocRow
      key={d.id}
      doc={d}
      depth={depth}
      active={sel?.kind === "doc" && sel.id === d.id}
      onClick={() => setSel({ kind: "doc", id: d.id })}
    />
  );

  const docRows = (key: GroupKey, depth: number) => {
    const list = grouped.get(key) ?? [];
    return (
      <>
        {list.slice(0, MAX_LISTED).map((d) => docRow(d, depth))}
        {list.length > MAX_LISTED && (
          <p className="faint" style={{ paddingLeft: 10 + depth * 14 }}>
            Showing {MAX_LISTED} of {list.length}. Search to find the rest.
          </p>
        )}
      </>
    );
  };

  const collectionRow = (c: DocCollection, depth: number) => {
    const kids = childrenOf(c.id);
    const open = expanded.has(c.id);
    const count = c.doc_count + kids.reduce((n, k) => n + k.doc_count, 0);
    return (
      <div key={c.id}>
        <div
          className={`doc-collection ${sel?.kind === "collection" && sel.id === c.id ? "active" : ""}`}
          style={{ paddingLeft: 6 + depth * 14 }}
          onClick={() => {
            toggle(c.id, true);
            setSel({ kind: "collection", id: c.id });
          }}
        >
          <span
            className="doc-caret"
            onClick={(e) => {
              e.stopPropagation();
              toggle(c.id);
            }}
          >
            {open ? "▾" : "▸"}
          </span>
          <span className="doc-collection-name">{c.name}</span>
          <span className="faint">{count}</span>
        </div>
        {open && (
          <>
            {kids.map((k) => collectionRow(k, depth + 1))}
            {docRows(c.id, depth + 1)}
          </>
        )}
      </div>
    );
  };

  const selectedCollection = sel?.kind === "collection" ? collections.find((c) => c.id === sel.id) : undefined;

  return (
    <div className="docs">
      <div className="doc-list">
        <div className="row" style={{ marginBottom: 8 }}>
          <button className="btn small" style={{ flex: 1 }} onClick={createDoc}>
            + Doc
          </button>
          <button className="btn small" onClick={() => setNaming({ parentId: null })}>
            + Collection
          </button>
          <button className="btn small" title="Copy this project's Claude Code memory into docs" onClick={() => setImporting(true)}>
            Import…
          </button>
        </div>
        <Organizer
          project={project}
          target="docs"
          onChanged={reloadAll}
          onOpenRun={onOpenRun}
          onFinished={() => setPaneVersion((v) => v + 1)}
        />
        {naming && (
          <NewCollection
            projectId={project.id}
            parentId={naming.parentId}
            onDone={(created) => {
              setNaming(null);
              if (created) {
                if (created.parent_id !== null) toggle(created.parent_id, true);
                void cols.reload();
                setSel({ kind: "collection", id: created.id });
              }
            }}
          />
        )}
        <input
          className="doc-search"
          placeholder="Search docs…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Escape" && setQuery("")}
        />
        <SearchStatus projectId={project.id} />
        {hits ? (
          <>
            {hits.map((h) => (
              <div
                key={h.doc_id}
                className={`doc-item ${sel?.kind === "doc" && sel.id === h.doc_id ? "active" : ""} ${RETIRED.includes(h.status) ? "retired" : ""}`}
                onClick={() => setSel({ kind: "doc", id: h.doc_id })}
              >
                <div className="title">
                  {h.title} <StatusBadge status={h.status} />
                </div>
                <div className="faint doc-hit-section">
                  {h.collection ? `${h.collection} · ` : ""}
                  {h.section ? `§ ${h.section} · ` : ""}
                  {h.matched === "meaning" ? "related meaning" : h.matched === "words" ? "words" : "words + meaning"}
                </div>
                <div className="doc-hit-snippet">
                  <Highlighted text={h.snippet} />
                </div>
              </div>
            ))}
            {hits.length === 0 && <p className="faint">No matches.</p>}
          </>
        ) : (
          <>
            {topLevel.map((c) => collectionRow(c, 0))}
            {collections.length > 0 ? (
              (grouped.get("unfiled")?.length ?? 0) > 0 && (
                <div>
                  <div className="doc-collection" style={{ paddingLeft: 6 }} onClick={() => toggle("unfiled")}>
                    <span className="doc-caret">{expanded.has("unfiled") ? "▾" : "▸"}</span>
                    <span className="doc-collection-name faint">Unfiled</span>
                    <span className="faint">{grouped.get("unfiled")?.length}</span>
                  </div>
                  {expanded.has("unfiled") && docRows("unfiled", 1)}
                </div>
              )
            ) : (
              docRows("unfiled", 0)
            )}
            {docs.data && allDocs.length === 0 && (
              <p className="faint">
                Specs, decisions, research, findings. This is the project's memory: agents search it, and save what
                they learn here.
              </p>
            )}
            {retiredCount > 0 && (
              <label className="faint doc-retired-toggle">
                <input type="checkbox" checked={showRetired} onChange={(e) => setShowRetired(e.target.checked)} /> Show{" "}
                {retiredCount} superseded/archived
              </label>
            )}
          </>
        )}
      </div>
      {sel?.kind === "doc" ? (
        <DocPane
          key={`doc-${sel.id}-${paneVersion}`}
          docId={sel.id}
          collections={collections}
          onSaved={onDocSaved}
          onOpen={(id) => setSel({ kind: "doc", id })}
          onDeleted={() => {
            setSel(null);
            reloadAll();
          }}
        />
      ) : selectedCollection ? (
        <CollectionPane
          key={`col-${selectedCollection.id}-${paneVersion}`}
          collection={selectedCollection}
          collections={collections}
          docs={grouped.get(selectedCollection.id) ?? []}
          onChanged={reloadAll}
          onDeleted={() => {
            setSel(null);
            reloadAll();
          }}
          onOpenDoc={(id) => setSel({ kind: "doc", id })}
          onOpenCollection={(id) => {
            toggle(id, true);
            setSel({ kind: "collection", id });
          }}
          onNewSubcollection={() => setNaming({ parentId: selectedCollection.id })}
        />
      ) : (
        <div className="empty">{collections.length ? "Pick a collection or a doc." : "No doc selected."}</div>
      )}
      {importing && (
        <MemoryImportModal
          project={project}
          onClose={() => setImporting(false)}
          onImported={() => {
            setSel(null);
            reloadAll();
          }}
        />
      )}
    </div>
  );
}

/** Inline name field for a new collection (Electron has no window.prompt). */
function NewCollection({
  projectId,
  parentId,
  onDone,
}: {
  projectId: number;
  parentId: number | null;
  onDone: (created: DocCollection | null) => void;
}) {
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (!name.trim()) return onDone(null);
    try {
      onDone(await api.createCollection(projectId, { name: name.trim(), parent_id: parentId }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div className="doc-new-collection">
      <input
        autoFocus
        placeholder={parentId === null ? "New collection name" : "New subcollection name"}
        value={name}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") void submit();
          if (e.key === "Escape") onDone(null);
        }}
        onBlur={() => !name.trim() && onDone(null)}
      />
      {error && <div className="error-text">{error}</div>}
    </div>
  );
}

/** One line under the search box: whether meaning-based search is ready, and indexing progress. */
function SearchStatus({ projectId }: { projectId: number }) {
  const status = useLoader<DocSearchStatus | null>(() => api.docSearchStatus(projectId).catch(() => null), [projectId]);
  const s = status.data;
  const busy = !!s && (s.state === "loading" || (s.state === "ready" && s.embedded < s.sections));
  usePolling(status.reload, 3000, busy);
  if (!s || s.sections === 0) return null;
  let text: string;
  if (s.state === "ready") {
    text = s.embedded < s.sections ? `Indexing for meaning search: ${s.embedded} of ${s.sections} sections` : "Searches words and meaning";
  } else if (s.state === "loading") {
    text = "Loading the search model (the first time, it downloads ~65 MB)…";
  } else if (s.state === "error") {
    text = "Searching words only: the search model couldn't load";
  } else {
    text = "Searching words only";
  }
  return (
    <div className="faint doc-search-status" title={s.error ?? s.model}>
      {text}
    </div>
  );
}

function listItem(d: Doc): DocListItem {
  const { id, title, pinned, status, kind, origin, collection_id, updated_at } = d;
  return { id, title, pinned, status, kind, origin, collection_id, updated_at };
}

/** Collections as select options: each top-level collection followed by its subcollections. */
function collectionOptions(collections: DocCollection[], exclude?: number) {
  const out: { id: number; label: string }[] = [];
  for (const c of collections.filter((c) => c.parent_id === null)) {
    if (c.id === exclude) continue;
    out.push({ id: c.id, label: c.name });
    for (const k of collections.filter((k) => k.parent_id === c.id)) {
      if (k.id !== exclude) out.push({ id: k.id, label: `${c.name} › ${k.name}` });
    }
  }
  return out;
}

function DocRow({ doc, active, depth = 0, onClick }: { doc: DocListItem; active: boolean; depth?: number; onClick: () => void }) {
  return (
    <div
      className={`doc-item ${active ? "active" : ""} ${RETIRED.includes(doc.status) ? "retired" : ""}`}
      style={{ paddingLeft: 10 + depth * 14 }}
      onClick={onClick}
    >
      <div className="title">
        {doc.pinned && "📌 "}
        {doc.title || "Untitled"}
      </div>
      <div className="faint doc-item-meta">
        {timeAgo(doc.updated_at)}
        {doc.kind === "rules" && <span className="badge">rules</span>}
        {doc.origin !== "user" && <span className="badge">{doc.origin === "agent" ? "by agent" : "imported"}</span>}
        <StatusBadge status={doc.status} />
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: DocStatus }) {
  if (status === "current") return null;
  return <span className={`badge doc-status ${status}`}>{status}</span>;
}

/** Render a search snippet, bolding the «matched» words. */
function Highlighted({ text }: { text: string }) {
  const parts = text.split(/«|»/);
  return (
    <>
      {parts.map((p, i) => (i % 2 === 1 ? <mark key={i}>{p}</mark> : <span key={i}>{p}</span>))}
    </>
  );
}

function CollectionPane({
  collection: c,
  collections,
  docs,
  onChanged,
  onDeleted,
  onOpenDoc,
  onOpenCollection,
  onNewSubcollection,
}: {
  collection: DocCollection;
  collections: DocCollection[];
  docs: DocListItem[];
  onChanged: () => void;
  onDeleted: () => void;
  onOpenDoc: (id: number) => void;
  onOpenCollection: (id: number) => void;
  onNewSubcollection: () => void;
}) {
  const [name, setName] = useState(c.name);
  const [description, setDescription] = useState(c.description);
  const [error, setError] = useState<string | null>(null);
  const kids = collections.filter((k) => k.parent_id === c.id);
  const parent = collections.find((p) => p.id === c.parent_id);

  async function save(fields: Partial<Pick<DocCollection, "name" | "description" | "parent_id" | "summary">>) {
    setError(null);
    try {
      await api.updateCollection(c.id, fields);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setName(c.name);
    }
  }

  async function remove() {
    const where = parent ? `move to "${parent.name}"` : "become unfiled";
    const subs = kids.length ? ` Its ${kids.length} subcollection(s) move to the top level.` : "";
    if (!confirm(`Delete collection "${c.name}"? Its ${c.doc_count} doc(s) ${where}; no doc is deleted.${subs}`)) return;
    await api.deleteCollection(c.id);
    onDeleted();
  }

  return (
    <div className="doc-editor">
      <div className="row">
        <input
          className="doc-title"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onBlur={() => name.trim() !== c.name && void save({ name })}
          onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
        />
        {c.parent_id === null && (
          <button className="btn small" onClick={onNewSubcollection}>
            + Subcollection
          </button>
        )}
        <button className="btn small danger" onClick={remove}>
          Delete
        </button>
      </div>
      <div className="row doc-meta">
        <span className="faint">Collection{parent ? ` in ${parent.name}` : ""}</span>
        {kids.length === 0 && (
          <select
            className="small"
            value={c.parent_id ?? ""}
            title="Collections nest at most two levels"
            onChange={(e) => void save({ parent_id: e.target.value ? Number(e.target.value) : null })}
          >
            <option value="">Top level</option>
            {collections
              .filter((p) => p.parent_id === null && p.id !== c.id)
              .map((p) => (
                <option key={p.id} value={p.id}>
                  Inside {p.name}
                </option>
              ))}
          </select>
        )}
      </div>
      {error && <div className="error-text">{error}</div>}
      <textarea
        className="doc-collection-description"
        rows={2}
        value={description}
        placeholder="What belongs in this collection (helps agents file docs)…"
        onChange={(e) => setDescription(e.target.value)}
        onBlur={() => description !== c.description && void save({ description })}
      />
      <SummaryPanel
        summary={c.summary}
        stale={c.summary_stale}
        hasContent={c.doc_count > 0 || kids.length > 0}
        open
        onSave={(summary) => save({ summary })}
        onRewrite={async () => {
          await api.regenerateCollectionSummary(c.id);
          onChanged();
        }}
      />
      <div className="doc-collection-contents">
        {kids.map((k) => (
          <div key={k.id} className="doc-item" onClick={() => onOpenCollection(k.id)}>
            <div className="title">
              ▸ {k.name} <span className="faint">{k.doc_count}</span>
            </div>
            {k.summary && <div className="faint doc-hit-snippet">{k.summary}</div>}
          </div>
        ))}
        {docs.slice(0, MAX_LISTED).map((d) => (
          <DocRow key={d.id} doc={d} active={false} onClick={() => onOpenDoc(d.id)} />
        ))}
        {docs.length === 0 && kids.length === 0 && <p className="faint">No docs yet. Pick this collection in a doc's editor to file it here.</p>}
      </div>
    </div>
  );
}

function DocPane({
  docId,
  collections,
  onSaved,
  onOpen,
  onDeleted,
}: {
  docId: number;
  collections: DocCollection[];
  onSaved: (d: Doc) => void;
  onOpen: (id: number) => void;
  onDeleted: () => void;
}) {
  const doc = useLoader(() => api.doc(docId), [docId]);
  if (doc.error) return <div className="empty">{doc.error}</div>;
  if (!doc.data) return <div className="empty">Loading…</div>;
  return (
    <DocEditor
      doc={doc.data}
      collections={collections}
      onSaved={(d) => {
        doc.setData(d);
        onSaved(d);
      }}
      onOpen={onOpen}
      onDeleted={onDeleted}
    />
  );
}

function DocEditor({
  doc,
  collections,
  onSaved,
  onOpen,
  onDeleted,
}: {
  doc: Doc;
  collections: DocCollection[];
  onSaved: (d: Doc) => void;
  onOpen: (id: number) => void;
  onDeleted: () => void;
}) {
  const [title, setTitle] = useState(doc.title);
  const [body, setBody] = useState(doc.body);
  const [editing, setEditing] = useState(!doc.body);
  const [saveState, setSaveState] = useState<"saved" | "dirty" | "saving">("saved");
  const timer = useRef<number | undefined>(undefined);
  const latest = useRef({ title, body });
  latest.current = { title, body };

  // Debounced autosave.
  useEffect(() => {
    if (title === doc.title && body === doc.body) return;
    setSaveState("dirty");
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(async () => {
      setSaveState("saving");
      onSaved(await api.updateDoc(doc.id, latest.current));
      setSaveState("saved");
    }, 600);
    return () => window.clearTimeout(timer.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [title, body]);

  async function change(fields: Partial<Doc>) {
    onSaved(await api.updateDoc(doc.id, fields));
  }

  async function remove() {
    if (!confirm(`Delete doc "${doc.title}"?`)) return;
    await api.deleteDoc(doc.id);
    onDeleted();
  }

  return (
    <div className="doc-editor">
      <div className="row">
        <input className="doc-title" value={title} onChange={(e) => setTitle(e.target.value)} />
        <span className="faint" style={{ fontSize: 12 }}>
          {saveState === "saved" ? "Saved" : saveState === "saving" ? "Saving…" : "Unsaved"}
        </span>
        <button className="btn small" onClick={() => setEditing(!editing)}>
          {editing ? "Preview" : "Edit"}
        </button>
        <button className="btn small" onClick={() => change({ pinned: !doc.pinned })}>
          {doc.pinned ? "Unpin" : "Pin"}
        </button>
        <button className="btn small danger" onClick={remove}>
          Delete
        </button>
      </div>
      <div className="row doc-meta">
        <select
          className="small"
          value={doc.collection_id ?? ""}
          title="Collection"
          onChange={(e) => change({ collection_id: e.target.value ? Number(e.target.value) : null })}
        >
          <option value="">Unfiled</option>
          {collectionOptions(collections).map((o) => (
            <option key={o.id} value={o.id}>
              {o.label}
            </option>
          ))}
        </select>
        <select className="small" value={doc.status} onChange={(e) => change({ status: e.target.value as DocStatus })}>
          {(Object.keys(STATUS_LABELS) as DocStatus[]).map((s) => (
            <option key={s} value={s}>
              {STATUS_LABELS[s]}
            </option>
          ))}
        </select>
        <select
          className="small"
          value={doc.kind}
          title="Rules docs are given in full to every agent run in this project"
          onChange={(e) => change({ kind: e.target.value as DocKind })}
        >
          <option value="doc">Doc</option>
          <option value="rules">Rules (every agent run follows these)</option>
        </select>
        {doc.superseded_by && (
          <span className="faint">
            Replaced by{" "}
            <a href="#" onClick={(e) => (e.preventDefault(), onOpen(doc.superseded_by!))}>
              doc {doc.superseded_by}
            </a>
          </span>
        )}
        <span className="spacer" />
        <span className="faint">
          {doc.origin === "agent" ? "Written by an agent" : doc.origin === "import" ? "Imported from Claude Code memory" : ""}
        </span>
      </div>
      <SummaryPanel
        summary={doc.summary}
        stale={doc.summary_stale}
        hasContent={!!doc.body.trim()}
        onSave={async (summary) => onSaved(await api.updateDoc(doc.id, { summary }))}
        onRewrite={async () => onSaved(await api.regenerateDocSummary(doc.id))}
      />
      {editing ? (
        <textarea autoFocus value={body} onChange={(e) => setBody(e.target.value)} placeholder="Write in Markdown…" />
      ) : (
        <div className="card" style={{ flex: 1, overflow: "auto" }} onDoubleClick={() => setEditing(true)}>
          {body ? <Markdown>{body}</Markdown> : <span className="faint">Empty doc. Double-click to edit.</span>}
        </div>
      )}
    </div>
  );
}

/** The summary agents see (for a doc or a collection); written by agents, by hand, or by Mercury in the background. */
function SummaryPanel({
  summary,
  stale,
  hasContent,
  open: startOpen = false,
  onSave,
  onRewrite,
}: {
  summary: string;
  stale: boolean;
  hasContent: boolean;
  open?: boolean;
  onSave: (summary: string) => Promise<unknown>;
  onRewrite: () => Promise<unknown>;
}) {
  const [open, setOpen] = useState(startOpen);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(summary);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function rewrite() {
    setBusy(true);
    setError(null);
    try {
      await onRewrite();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    await onSave(draft);
    setEditing(false);
  }

  const state = !hasContent || !stale ? null : summary ? "out of date, rewritten shortly" : "not written yet";

  return (
    <div className="doc-summary">
      <div className="row">
        <button className="btn ghost small" onClick={() => setOpen(!open)}>
          {open ? "▾" : "▸"} Agent summary
        </button>
        {state && <span className="faint">({state})</span>}
        {!open && summary && <span className="faint doc-summary-peek">{summary}</span>}
      </div>
      {open && (
        <div className="doc-summary-body">
          {editing ? (
            <>
              <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={3} />
              <div className="row">
                <button className="btn small primary" onClick={save}>
                  Save summary
                </button>
                <button className="btn small" onClick={() => setEditing(false)}>
                  Cancel
                </button>
              </div>
            </>
          ) : (
            <>
              <p>{summary || <span className="faint">No summary yet.</span>}</p>
              <div className="row">
                <button
                  className="btn small"
                  onClick={() => {
                    setDraft(summary);
                    setEditing(true);
                  }}
                >
                  Edit
                </button>
                <button className="btn small" disabled={busy || !hasContent} onClick={rewrite}>
                  {busy ? "Writing…" : "Rewrite now"}
                </button>
                {error && <span className="error-text">{error}</span>}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
