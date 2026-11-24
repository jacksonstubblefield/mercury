"""Docs storage and search: the one place docs are saved, split into sections and indexed.

Every writer (the REST API, agents' tools, the memory importer, the summary worker)
goes through save_doc/delete_doc so the section rows and the full-text index stay in
step with Doc.body.

Search is hybrid, over sections:
- keyword: SQLite FTS5 (BM25, Porter stemming), with each section's doc title and
  summary indexed alongside it;
- meaning: cosine similarity of local embeddings (services/embeddings.py), when the
  model is loaded and the section has been embedded.
The two rankings are merged with reciprocal rank fusion, hits are grouped per doc,
and superseded, archived and draft docs are ranked lower.
"""

from __future__ import annotations

import hashlib
import re
import threading
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import text
from sqlmodel import Session, select

from ..models import Doc, DocKind, DocSection, DocStatus, MercuryMeta, utcnow

FTS_TABLE = "doc_fts"
# Sections longer than this are split further at paragraph breaks, so a search hit
# points at a readable amount of text and fits the embedding model's 512-token input.
MAX_SECTION_CHARS = 2_000
# Bump when sectioning or indexing changes: every doc is reindexed on the next start.
INDEX_VERSION = "2"
# Reciprocal rank fusion constant, and the lowest cosine similarity a meaning-only hit
# needs (below it, results are noise rather than related).
RRF_K = 60
CANDIDATES = 200
MIN_VECTOR_SIMILARITY = 0.60  # tuned with search_eval on real docs (bge-small)
SNIPPET_TOKENS = 24
# BM25 column weights: title, heading, summary, text.
BM25_WEIGHTS = (8.0, 4.0, 3.0, 1.0)
# Multipliers on a doc's score (higher ranks first).
STATUS_WEIGHT = {
    DocStatus.current: 1.0,
    DocStatus.draft: 0.8,
    DocStatus.superseded: 0.4,
    DocStatus.archived: 0.3,
}
DUPLICATE_TITLE_SIMILARITY = 0.6

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_WORD = re.compile(r"\w+", re.UNICODE)
_STOPWORDS = frozenset(
    "a an and are as at be by for from how in into is it of on or the to vs was we what when where which why with".split()
)


_generation = 0
_generation_lock = threading.Lock()


def generation() -> int:
    """Changes whenever sections or embeddings change; caches of section data key on it."""
    return _generation


def bump_generation() -> None:
    global _generation
    with _generation_lock:
        _generation += 1


def embed_text(title: str, heading: str, text_: str) -> str:
    """What a section is embedded as: its doc title and heading give it context."""
    return "\n".join(p for p in (title, heading, text_) if p)


def content_hash(title: str, body: str) -> str:
    return hashlib.sha256(f"{title}\n{body}".encode()).hexdigest()[:16]


def summary_stale(doc: Doc) -> bool:
    """A doc with text needs a summary that was written for its current content."""
    return bool(doc.body.strip()) and doc.summary_hash != content_hash(doc.title, doc.body)


def stale_summary_clause():
    """SQL version of summary_stale(), using the stored content_hash."""
    return (Doc.summary_hash != Doc.content_hash) & (Doc.body != "")


# --------------------------------------------------------------------------- sections


@dataclass
class Section:
    heading: str
    start_line: int  # 1-based, inclusive
    end_line: int
    text: str


def split_sections(body: str) -> list[Section]:
    """Split Markdown at its headings (ignoring code fences); long sections are split at paragraphs.

    Always returns at least one section, so a doc's title and summary are searchable
    even when its body is empty.
    """
    lines = body.split("\n")
    raw: list[tuple[str, int, int]] = []  # heading path, start index, end index (exclusive)
    stack: list[tuple[int, str]] = []
    heading, start = "", 0
    fence: Optional[str] = None
    for i, line in enumerate(lines):
        m = _FENCE.match(line)
        if m:
            marker = m.group(1)
            fence = None if fence == marker else (fence or marker)
            continue
        if fence:
            continue
        h = _HEADING.match(line)
        if not h:
            continue
        raw.append((heading, start, i))
        level = len(h.group(1))
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, h.group(2)))
        heading, start = " › ".join(t for _, t in stack), i
    raw.append((heading, start, len(lines)))

    sections: list[Section] = []
    for path, a, b in raw:
        chunk = lines[a:b]
        if not "\n".join(chunk).strip():
            continue
        for n, (pa, pb) in enumerate(_split_long(chunk)):
            sections.append(
                Section(
                    heading=path if n == 0 else f"{path} (cont.)".strip(),
                    start_line=a + pa + 1,
                    end_line=a + pb,
                    text="\n".join(chunk[pa:pb]).strip("\n"),
                )
            )
    return sections or [Section(heading="", start_line=1, end_line=max(1, len(lines)), text=body)]


def _split_long(chunk: list[str]) -> list[tuple[int, int]]:
    """Split a section's lines into ranges of at most ~MAX_SECTION_CHARS, preferring blank lines."""
    if sum(len(l) + 1 for l in chunk) <= MAX_SECTION_CHARS:
        return [(0, len(chunk))]
    parts: list[tuple[int, int]] = []
    start, size, last_break = 0, 0, None
    for i, line in enumerate(chunk):
        size += len(line) + 1
        if not line.strip():
            last_break = i
        if size > MAX_SECTION_CHARS and i > start:
            cut = last_break + 1 if last_break is not None and last_break > start else i
            parts.append((start, cut))
            start, last_break = cut, None
            size = sum(len(l) + 1 for l in chunk[start : i + 1])
    parts.append((start, len(chunk)))
    return [p for p in parts if p[1] > p[0]]


# --------------------------------------------------------------------------- index


def fts_available(session: Session) -> bool:
    bind = session.get_bind()
    if bind.dialect.name != "sqlite":
        return False
    row = session.exec(text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:n").bindparams(n=FTS_TABLE)).first()
    return row is not None


def create_fts(conn) -> None:
    conn.execute(
        text(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} "
            "USING fts5(title, heading, summary, text, tokenize='porter unicode61')"
        )
    )


def reindex(session: Session, doc: Doc) -> None:
    """Rebuild a doc's sections and their full-text rows. Caller commits."""
    use_fts = fts_available(session)
    old_ids = session.exec(select(DocSection.id).where(DocSection.doc_id == doc.id)).all()
    if old_ids and use_fts:
        session.execute(text(f"DELETE FROM {FTS_TABLE} WHERE rowid IN ({','.join(map(str, old_ids))})"))
    for sid in old_ids:
        session.delete(session.get(DocSection, sid))
    session.flush()
    for pos, s in enumerate(split_sections(doc.body)):
        row = DocSection(
            doc_id=doc.id,
            position=pos,
            heading=s.heading,
            start_line=s.start_line,
            end_line=s.end_line,
            text=s.text,
            embed_hash=hashlib.sha256(embed_text(doc.title, s.heading, s.text).encode()).hexdigest()[:24],
        )
        session.add(row)
        session.flush()
        if use_fts:
            session.execute(
                text(f"INSERT INTO {FTS_TABLE} (rowid, title, heading, summary, text) VALUES (:id, :t, :h, :s, :x)").bindparams(
                    id=row.id, t=doc.title, h=s.heading, s=doc.summary, x=s.text
                )
            )
    bump_generation()


def save_doc(session: Session, doc: Doc, *, summary: Optional[str] = None, touch: bool = True) -> Doc:
    """Persist a new or changed doc, keep its summary bookkeeping right, and reindex it.

    Pass `summary` when the caller wrote one for the doc's current content. Otherwise
    a changed doc's summary goes stale and the summary worker rewrites it.
    """
    doc.content_hash = content_hash(doc.title, doc.body)
    if summary is not None:
        doc.summary = summary.strip()
        doc.summary_hash = doc.content_hash
    if touch:
        doc.updated_at = utcnow()
    session.add(doc)
    session.flush()
    reindex(session, doc)
    session.commit()
    session.refresh(doc)
    return doc


def delete_doc(session: Session, doc: Doc) -> None:
    if fts_available(session):
        ids = session.exec(select(DocSection.id).where(DocSection.doc_id == doc.id)).all()
        if ids:
            session.execute(text(f"DELETE FROM {FTS_TABLE} WHERE rowid IN ({','.join(map(str, ids))})"))
    for other in session.exec(select(Doc).where(Doc.superseded_by == doc.id)).all():
        other.superseded_by = None
        session.add(other)
    session.delete(doc)  # sections go with it (ON DELETE CASCADE)
    session.commit()
    bump_generation()


def backfill(session: Session) -> int:
    """Hash and index docs saved before this existed, or all docs when INDEX_VERSION changed."""
    meta = session.get(MercuryMeta, "doc_index_version")
    if meta is None or meta.value != INDEX_VERSION:
        docs = session.exec(select(Doc)).all()
        session.add(MercuryMeta(key="doc_index_version", value=INDEX_VERSION) if meta is None else meta)
        if meta is not None:
            meta.value = INDEX_VERSION
    else:
        indexed = select(DocSection.doc_id).distinct()
        docs = session.exec(select(Doc).where(Doc.id.not_in(indexed) | (Doc.content_hash == ""))).all()
    for doc in docs:
        doc.content_hash = content_hash(doc.title, doc.body)
        session.add(doc)
        reindex(session, doc)
    session.commit()
    return len(docs)


# --------------------------------------------------------------------------- search


@dataclass
class Hit:
    doc: Doc
    heading: str
    start_line: int
    end_line: int
    snippet: str
    score: float
    matching_sections: int
    matched: str = "words"  # how the section matched: words, meaning or words+meaning
    collection: Optional[str] = None  # the doc's collection path, e.g. "Roadmap › Spikes"

    def as_dict(self) -> dict[str, Any]:
        d = self.doc
        data: dict[str, Any] = {
            "doc_id": d.id,
            "title": d.title,
            "status": d.status.value,
            "summary": d.summary or None,
            "summary_stale": summary_stale(d),
            "section": self.heading or None,
            "lines": [self.start_line, self.end_line],
            "snippet": self.snippet,
            "matched": self.matched,
        }
        if self.collection:
            data["collection"] = self.collection
        if d.kind != DocKind.doc:
            data["kind"] = d.kind.value
        if d.superseded_by:
            data["superseded_by"] = d.superseded_by
        if self.matching_sections > 1:
            data["other_matching_sections"] = self.matching_sections - 1
        return data


def words(s: str) -> list[str]:
    return [w for w in _WORD.findall(s.lower()) if w not in _STOPWORDS]


def _fts_query(query: str) -> str:
    terms = list(dict.fromkeys(words(query)))[:32]
    return " OR ".join(f'"{t}"' for t in terms)


def search(
    session: Session,
    project_id: int,
    query: str,
    *,
    limit: int = 8,
    include_archived: bool = False,
    mode: str = "hybrid",
    collection_ids: Optional[set[int]] = None,
) -> list[Hit]:
    """Best-matching docs for a free-text query, one hit (its best section) per doc.

    mode: "hybrid" (default), "keyword" or "meaning"; the last two exist for evaluation.
    collection_ids: only search docs in these collections.
    """
    if not query.strip():
        return []
    scope: Optional[set[int]] = None
    if collection_ids is not None:
        scope = set(
            session.exec(select(Doc.id).where(Doc.project_id == project_id, Doc.collection_id.in_(list(collection_ids)))).all()
        )
        if not scope:
            return []
    if not fts_available(session):
        hits = _search_like(session, project_id, query, limit=limit * 4, include_archived=include_archived)
        return [h for h in hits if scope is None or h.doc.id in scope][:limit]

    keyword: list[Any] = []
    match = _fts_query(query)
    if match and mode != "meaning":
        w = BM25_WEIGHTS
        keyword = session.execute(
            text(
                f"SELECT s.id, bm25({FTS_TABLE}, {w[0]}, {w[1]}, {w[2]}, {w[3]}) AS score, "
                f"snippet({FTS_TABLE}, 3, '«', '»', '…', {SNIPPET_TOKENS}) AS snip "
                f"FROM {FTS_TABLE} JOIN docsection s ON s.id = {FTS_TABLE}.rowid JOIN doc d ON d.id = s.doc_id "
                f"WHERE {FTS_TABLE} MATCH :m AND d.project_id = :p "
                + (f"AND d.id IN ({','.join(map(str, scope))}) " if scope is not None else "")
                + f"ORDER BY score LIMIT {CANDIDATES}"
            ).bindparams(m=match, p=project_id)
        ).all()
    meaning: list[tuple[int, float]] = []
    if mode != "keyword":
        from .embeddings import vector_search  # embeddings imports this module

        found = vector_search(session, project_id, query, CANDIDATES, doc_ids=scope)
        meaning = [(sid, sim) for sid, sim in (found or []) if sim >= MIN_VECTOR_SIMILARITY]

    # Reciprocal rank fusion over sections.
    fused: dict[int, float] = {}
    how: dict[int, set[str]] = {}
    snippets = {row.id: row.snip for row in keyword}
    for rank, row in enumerate(keyword):
        fused[row.id] = fused.get(row.id, 0.0) + 1 / (RRF_K + rank + 1)
        how.setdefault(row.id, set()).add("words")
    for rank, (sid, _) in enumerate(meaning):
        fused[sid] = fused.get(sid, 0.0) + 1 / (RRF_K + rank + 1)
        how.setdefault(sid, set()).add("meaning")
    if not fused:
        return []

    sections = {sec.id: sec for sec in session.exec(select(DocSection).where(DocSection.id.in_(list(fused)))).all()}
    best: dict[int, int] = {}  # doc id -> best section id
    count: dict[int, int] = {}
    for sid in sorted(fused, key=fused.get, reverse=True):
        sec = sections.get(sid)
        if sec is None:
            continue
        count[sec.doc_id] = count.get(sec.doc_id, 0) + 1
        best.setdefault(sec.doc_id, sid)
    docs = {d.id: d for d in session.exec(select(Doc).where(Doc.id.in_(list(best)))).all()}
    from .doc_collections import paths  # doc_collections doesn't import this module at load time

    collection_paths = paths(session, project_id) if any(d.collection_id for d in docs.values()) else {}
    hits: list[Hit] = []
    for doc_id, sid in best.items():
        doc = docs[doc_id]
        if doc.status == DocStatus.archived and not include_archived:
            continue
        sec = sections[sid]
        # Pinning isn't a relevance signal: pinned docs are already listed in every run's instructions.
        score = fused[sid] * STATUS_WEIGHT[doc.status]
        snippet = snippets.get(sid) or _lead(sec.text)
        matched = "+".join(m for m in ("words", "meaning") if m in how[sid])
        hits.append(
            Hit(doc, sec.heading, sec.start_line, sec.end_line, snippet, score, count[doc_id], matched,
                collection_paths.get(doc.collection_id))
        )
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]


def _lead(text_: str, n: int = 200) -> str:
    flat = " ".join(text_.split())
    return flat if len(flat) <= n else flat[:n].rstrip() + "…"


def _search_like(session: Session, project_id: int, query: str, *, limit: int, include_archived: bool) -> list[Hit]:
    """Fallback for databases without FTS5: count matching terms per section."""
    terms = list(dict.fromkeys(words(query)))
    rows = session.exec(
        select(DocSection, Doc).join(Doc, Doc.id == DocSection.doc_id).where(Doc.project_id == project_id)
    ).all()
    best: dict[int, Hit] = {}
    for sec, doc in rows:
        if doc.status == DocStatus.archived and not include_archived:
            continue
        hay = f"{doc.title} {doc.title} {sec.heading} {doc.summary} {sec.text}".lower()
        n = sum(hay.count(t) for t in terms)
        if not n:
            continue
        score = n * STATUS_WEIGHT[doc.status]
        prev = best.get(doc.id)
        if prev is None or score > prev.score:
            best[doc.id] = Hit(doc, sec.heading, sec.start_line, sec.end_line, sec.text[:200], score, (prev.matching_sections + 1) if prev else 1)
        else:
            prev.matching_sections += 1
    return sorted(best.values(), key=lambda h: h.score, reverse=True)[:limit]


def similar_titles(session: Session, project_id: int, title: str, *, exclude_id: Optional[int] = None) -> list[Doc]:
    """Docs whose titles share most of their words with `title`: likely duplicates."""
    want = set(words(title))
    if not want:
        return []
    found: list[tuple[float, Doc]] = []
    for doc in session.exec(select(Doc).where(Doc.project_id == project_id, Doc.status != DocStatus.archived)).all():
        if doc.id == exclude_id:
            continue
        have = set(words(doc.title))
        if have and len(want & have) / len(want | have) >= DUPLICATE_TITLE_SIMILARITY:
            found.append((len(want & have) / len(want | have), doc))
    found.sort(key=lambda x: x[0], reverse=True)
    return [d for _, d in found[:5]]


# --------------------------------------------------------------------------- reading


def outline(session: Session, doc: Doc) -> list[dict[str, Any]]:
    secs = session.exec(select(DocSection).where(DocSection.doc_id == doc.id).order_by(DocSection.position)).all()
    return [{"section": s.heading or "(intro)", "lines": [s.start_line, s.end_line], "chars": len(s.text)} for s in secs]


def line_range(body: str, start: int, end: Optional[int]) -> tuple[str, int, int, int]:
    """Lines start..end (1-based, inclusive) of body, clamped. Returns text, start, end, total lines."""
    lines = body.split("\n")
    total = len(lines)
    start = max(1, start)
    end = total if end is None else min(max(end, start), total)
    return "\n".join(lines[start - 1 : end]), start, end, total
