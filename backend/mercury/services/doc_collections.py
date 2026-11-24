"""Doc collections: shallow folders (at most two levels) that orient agents in a large doc set.

Each collection has a name, an optional description of what belongs in it, and a
summary that Mercury writes from its docs' and subcollections' summaries (see
doc_summaries.SummaryWorker). A summary is stale when the contents it was written
from changed: contents_hash() covers the collection's name and description, its
subcollections' names and summaries, and its docs' titles, summaries and statuses.

Deleting a collection never deletes docs: its docs and subcollections move up a level.
Moving docs doesn't touch their content, so it doesn't reindex them or change updated_at.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any, Optional

from sqlmodel import Session, func, select

from ..models import Doc, DocCollection, DocStatus, utcnow

# Limits on what a collection summary is written from, so huge collections stay cheap.
MAX_SUMMARY_DOCS = 150
MAX_DOC_SUMMARY_CHARS = 220
MAX_EXTRA_TITLES_CHARS = 6_000


class CollectionError(ValueError):
    """A request that would break the collection rules; the message says why."""


def get(session: Session, project_id: int, collection_id: Any) -> Optional[DocCollection]:
    c = session.get(DocCollection, collection_id) if isinstance(collection_id, int) else None
    return c if c is not None and c.project_id == project_id else None


def all_for(session: Session, project_id: int) -> list[DocCollection]:
    return list(session.exec(select(DocCollection).where(DocCollection.project_id == project_id).order_by(DocCollection.name)).all())


def path(c: DocCollection, by_id: dict[int, DocCollection]) -> str:
    parent = by_id.get(c.parent_id) if c.parent_id else None
    return f"{parent.name} › {c.name}" if parent else c.name


def paths(session: Session, project_id: int) -> dict[int, str]:
    cols = all_for(session, project_id)
    by_id = {c.id: c for c in cols}
    return {c.id: path(c, by_id) for c in cols}


def with_children(session: Session, project_id: int, collection_id: int) -> set[int]:
    """The collection and its subcollections."""
    ids = {collection_id}
    ids.update(
        session.exec(
            select(DocCollection.id).where(DocCollection.project_id == project_id, DocCollection.parent_id == collection_id)
        ).all()
    )
    return ids


def _check(session: Session, project_id: int, c: DocCollection) -> None:
    name = (c.name or "").strip()
    if not name:
        raise CollectionError("A collection needs a name.")
    if "›" in name:
        raise CollectionError('Collection names can\'t contain "›".')
    c.name = name
    if c.parent_id is not None:
        parent = get(session, project_id, c.parent_id)
        if parent is None or parent.id == c.id:
            raise CollectionError("parent_id must be another collection in this project.")
        if parent.parent_id is not None:
            raise CollectionError("Collections nest at most two levels: the parent must be a top-level collection.")
        if c.id is not None and session.exec(select(DocCollection.id).where(DocCollection.parent_id == c.id)).first():
            raise CollectionError("This collection has subcollections, so it has to stay at the top level.")
    siblings = session.exec(
        select(DocCollection).where(
            DocCollection.project_id == project_id,
            DocCollection.parent_id == c.parent_id if c.parent_id is not None else DocCollection.parent_id.is_(None),
        )
    ).all()
    if any(s.id != c.id and s.name.lower() == name.lower() for s in siblings):
        raise CollectionError(f'There\'s already a collection named "{name}" here.')


def create(
    session: Session,
    project_id: int,
    name: str,
    description: str = "",
    parent_id: Optional[int] = None,
    *,
    source_key: Optional[str] = None,
) -> DocCollection:
    c = DocCollection(
        project_id=project_id, name=name, description=(description or "").strip(), parent_id=parent_id, source_key=source_key
    )
    _check(session, project_id, c)
    session.add(c)
    session.commit()
    session.refresh(c)
    return c


def update(session: Session, c: DocCollection, changes: dict[str, Any]) -> DocCollection:
    for key in ("name", "description", "parent_id"):
        if key in changes:
            setattr(c, key, (changes[key] or "").strip() if key == "description" else changes[key])
    if "summary" in changes and changes["summary"] is not None:
        c.summary = changes["summary"].strip()
    _check(session, c.project_id, c)
    if "summary" in changes and changes["summary"] is not None:
        c.summary_hash = contents_hash(session, c)  # a hand-written summary counts as current
    c.updated_at = utcnow()
    session.add(c)
    session.commit()
    session.refresh(c)
    return c


def delete(session: Session, c: DocCollection) -> None:
    """Delete a collection; its docs and subcollections move to its parent (or the top level)."""
    for doc in session.exec(select(Doc).where(Doc.collection_id == c.id)).all():
        doc.collection_id = c.parent_id
        session.add(doc)
    for child in session.exec(select(DocCollection).where(DocCollection.parent_id == c.id)).all():
        child.parent_id = None
        session.add(child)
    session.delete(c)
    session.commit()


def move_docs(session: Session, project_id: int, doc_ids: list[int], collection_id: Optional[int]) -> int:
    if collection_id is not None and get(session, project_id, collection_id) is None:
        raise CollectionError("collection_id must be a collection in this project (or null for unfiled).")
    docs = session.exec(select(Doc).where(Doc.project_id == project_id, Doc.id.in_(doc_ids))).all()
    if len(docs) != len(set(doc_ids)):
        found = {d.id for d in docs}
        raise CollectionError(f"These doc ids aren't in this project: {sorted(set(doc_ids) - found)}. Nothing was moved.")
    for doc in docs:
        doc.collection_id = collection_id
        session.add(doc)
    session.commit()
    return len(docs)


def doc_counts(session: Session, project_id: int) -> dict[Optional[int], int]:
    """Docs directly in each collection (key None: unfiled)."""
    rows = session.exec(
        select(Doc.collection_id, func.count()).where(Doc.project_id == project_id).group_by(Doc.collection_id)
    ).all()
    return {cid: n for cid, n in rows}


# --------------------------------------------------------------------------- summaries


def _members(session: Session, c: DocCollection) -> tuple[list[DocCollection], list[Doc]]:
    children = list(session.exec(select(DocCollection).where(DocCollection.parent_id == c.id).order_by(DocCollection.name)).all())
    docs = list(
        session.exec(
            select(Doc).where(Doc.collection_id == c.id).order_by(Doc.pinned.desc(), Doc.updated_at.desc())
        ).all()
    )
    return children, docs


def contents_hash(session: Session, c: DocCollection) -> str:
    children, docs = _members(session, c)
    h = hashlib.sha256()
    h.update(f"{c.name}\n{c.description}\n".encode())
    for ch in children:
        h.update(f"c{ch.id}\t{ch.name}\t{ch.summary}\n".encode())
    for d in sorted(docs, key=lambda d: d.id):
        h.update(f"d{d.id}\t{d.title}\t{d.summary}\t{d.status.value}\n".encode())
    return h.hexdigest()[:16]


def summary_input(session: Session, c: DocCollection) -> tuple[str, bool]:
    """The text a collection summary is written from, and whether it's ready to be written.

    Not ready while any included doc's summary is itself being rewritten, so a collection
    summary isn't written from stale doc summaries.
    """
    from .doc_store import summary_stale as doc_summary_stale

    children, docs = _members(session, c)
    active = [d for d in docs if d.status in (DocStatus.current, DocStatus.draft)]
    retired = len(docs) - len(active)
    shown = active[:MAX_SUMMARY_DOCS]
    ready = not any(doc_summary_stale(d) for d in shown)
    lines = [f"Collection: {c.name}"]
    if c.description:
        lines.append(f"Meant for: {c.description}")
    if children:
        lines.append("\nSubcollections:")
        lines += [f"- {ch.name}: {ch.summary or ch.description or '(no summary yet)'}" for ch in children]
    lines.append(f"\nDocs ({len(active)} current or draft, {retired} superseded or archived):")
    for d in shown:
        summary = d.summary if len(d.summary) <= MAX_DOC_SUMMARY_CHARS else d.summary[:MAX_DOC_SUMMARY_CHARS] + "…"
        lines.append(f"- {d.title}" + (f": {summary}" if summary else ""))
    extra = active[MAX_SUMMARY_DOCS:]
    if extra:
        titles, used = [], 0
        for d in extra:
            if used + len(d.title) > MAX_EXTRA_TITLES_CHARS:
                break
            titles.append(d.title)
            used += len(d.title) + 2
        lines.append(f"\n{len(extra)} more docs, titled: " + "; ".join(titles) + ("; …" if len(titles) < len(extra) else ""))
    return "\n".join(lines), ready


def summary_stale(session: Session, c: DocCollection) -> bool:
    has_content = session.exec(select(Doc.id).where(Doc.collection_id == c.id)).first() or session.exec(
        select(DocCollection.id).where(DocCollection.parent_id == c.id)
    ).first()
    return bool(has_content) and c.summary_hash != contents_hash(session, c)


def read(session: Session, c: DocCollection, counts: Optional[dict[Optional[int], int]] = None) -> dict[str, Any]:
    counts = counts if counts is not None else doc_counts(session, c.project_id)
    return {
        "id": c.id,
        "project_id": c.project_id,
        "name": c.name,
        "description": c.description,
        "parent_id": c.parent_id,
        "summary": c.summary,
        "summary_stale": summary_stale(session, c),
        "doc_count": counts.get(c.id, 0),
        "created_at": c.created_at,
        "updated_at": c.updated_at,
    }


def tree(session: Session, project_id: int) -> list[dict[str, Any]]:
    """Top-level collections with their subcollections, and doc counts including subcollections."""
    cols = all_for(session, project_id)
    counts = doc_counts(session, project_id)
    children: dict[int, list[DocCollection]] = defaultdict(list)
    for c in cols:
        if c.parent_id:
            children[c.parent_id].append(c)
    out = []
    for c in cols:
        if c.parent_id:
            continue
        kids = children.get(c.id, [])
        out.append(
            {
                "collection": c,
                "children": kids,
                "doc_count": counts.get(c.id, 0) + sum(counts.get(k.id, 0) for k in kids),
            }
        )
    return out
