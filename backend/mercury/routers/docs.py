from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, select

from ..models import (
    Doc,
    DocCollectionCreate,
    DocCollectionUpdate,
    DocCreate,
    DocListItem,
    DocMove,
    DocOrigin,
    DocRead,
    DocStatus,
    DocUpdate,
    Project,
    utcnow,
)
from ..services import doc_collections, doc_store, doc_summaries, embeddings, memory_import
from .deps import SessionDep, get_project_or_404

router = APIRouter(prefix="/api", tags=["docs"])


def to_read(doc: Doc) -> DocRead:
    return DocRead.model_validate(doc, update={"summary_stale": doc_store.summary_stale(doc)})


def check_superseded_by(session: Session, doc_project_id: int, doc_id: Optional[int], target: Optional[int]) -> None:
    if target is None:
        return
    other = session.get(Doc, target)
    if other is None or other.project_id != doc_project_id or other.id == doc_id:
        raise HTTPException(422, "superseded_by must be another doc in the same project.")


def check_collection(session: Session, project_id: int, collection_id: Optional[int]) -> None:
    if collection_id is not None and doc_collections.get(session, project_id, collection_id) is None:
        raise HTTPException(422, "collection_id must be a collection in the same project.")


@router.get("/projects/{project_id}/docs", response_model=list[DocListItem])
def list_docs(project_id: int, session: SessionDep):
    """Every doc without bodies; the editor loads one doc at a time."""
    get_project_or_404(session, project_id)
    return session.exec(
        select(Doc).where(Doc.project_id == project_id).order_by(Doc.pinned.desc(), Doc.updated_at.desc())
    ).all()


@router.get("/projects/{project_id}/docs/search")
def search_docs(
    project_id: int,
    q: str,
    session: SessionDep,
    limit: int = 30,
    include_archived: bool = True,
    collection_id: Optional[int] = None,
):
    get_project_or_404(session, project_id)
    scope = None
    if collection_id is not None:
        check_collection(session, project_id, collection_id)
        scope = doc_collections.with_children(session, project_id, collection_id)
    hits = doc_store.search(
        session, project_id, q, limit=min(limit, 100), include_archived=include_archived, collection_ids=scope
    )
    return [h.as_dict() for h in hits]


@router.get("/projects/{project_id}/docs/search-status")
def search_status(project_id: int, session: SessionDep):
    """Whether meaning-based search is available, and how much of the project is embedded."""
    get_project_or_404(session, project_id)
    st = embeddings.status()
    sections, embedded = embeddings.counts(session, project_id, st.model) if st.model else (0, 0)
    return {"state": st.state, "model": st.model, "error": st.error, "sections": sections, "embedded": embedded}


@router.post("/projects/{project_id}/docs", response_model=DocRead, status_code=201)
def create_doc(project_id: int, body: DocCreate, session: SessionDep):
    get_project_or_404(session, project_id)
    check_superseded_by(session, project_id, None, body.superseded_by)
    check_collection(session, project_id, body.collection_id)
    doc = Doc.model_validate(body.model_dump(exclude={"summary"}), update={"project_id": project_id, "origin": DocOrigin.user})
    return to_read(doc_store.save_doc(session, doc, summary=body.summary or None))


def _get_doc(session, doc_id: int) -> Doc:
    doc = session.get(Doc, doc_id)
    if doc is None:
        raise HTTPException(404, "Doc not found")
    return doc


@router.get("/docs/{doc_id}", response_model=DocRead)
def get_doc(doc_id: int, session: SessionDep):
    return to_read(_get_doc(session, doc_id))


@router.patch("/docs/{doc_id}", response_model=DocRead)
def update_doc(doc_id: int, body: DocUpdate, session: SessionDep):
    doc = _get_doc(session, doc_id)
    changes = body.model_dump(exclude_unset=True)
    summary = changes.pop("summary", None)
    if "superseded_by" in changes:
        check_superseded_by(session, doc.project_id, doc.id, changes["superseded_by"])
        if changes["superseded_by"] is not None and "status" not in changes:
            changes["status"] = DocStatus.superseded
    if "collection_id" in changes:
        check_collection(session, doc.project_id, changes["collection_id"])
    doc.sqlmodel_update(changes)
    if summary is not None or "title" in changes or "body" in changes:
        return to_read(doc_store.save_doc(session, doc, summary=summary))
    # Metadata only: nothing to reindex. Moving to another collection isn't an edit.
    if set(changes) - {"collection_id"}:
        doc.updated_at = utcnow()
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return to_read(doc)


@router.post("/projects/{project_id}/docs/move")
def move_docs(project_id: int, body: DocMove, session: SessionDep):
    get_project_or_404(session, project_id)
    try:
        return {"moved": doc_collections.move_docs(session, project_id, body.doc_ids, body.collection_id)}
    except doc_collections.CollectionError as e:
        raise HTTPException(422, str(e)) from e


@router.delete("/docs/{doc_id}", status_code=204)
def delete_doc(doc_id: int, session: SessionDep):
    doc_store.delete_doc(session, _get_doc(session, doc_id))


@router.post("/docs/{doc_id}/summary", response_model=DocRead)
async def regenerate_summary(doc_id: int, request: Request, session: SessionDep):
    """Write the doc's agent summary now, instead of waiting for the background worker."""
    doc = _get_doc(session, doc_id)
    if not doc.body.strip():
        raise HTTPException(422, "The doc is empty, so there's nothing to summarize.")
    try:
        summary = await doc_summaries.generate(doc.title, doc.body, request.app.state.runs.client_factory)
    except Exception as e:  # surface the reason (auth, network, ...) to the UI
        raise HTTPException(502, str(e) or "Couldn't write a summary.") from e
    session.refresh(doc)
    return to_read(doc_store.save_doc(session, doc, summary=summary, touch=False))


# --------------------------------------------------------------------------- collections


def _get_collection(session, collection_id: int):
    from ..models import DocCollection

    c = session.get(DocCollection, collection_id)
    if c is None:
        raise HTTPException(404, "Collection not found")
    return c


@router.get("/projects/{project_id}/collections")
def list_collections(project_id: int, session: SessionDep):
    get_project_or_404(session, project_id)
    counts = doc_collections.doc_counts(session, project_id)
    return [doc_collections.read(session, c, counts) for c in doc_collections.all_for(session, project_id)]


@router.post("/projects/{project_id}/collections", status_code=201)
def create_collection(project_id: int, body: DocCollectionCreate, session: SessionDep):
    get_project_or_404(session, project_id)
    try:
        c = doc_collections.create(session, project_id, body.name, body.description, body.parent_id)
    except doc_collections.CollectionError as e:
        raise HTTPException(422, str(e)) from e
    return doc_collections.read(session, c)


@router.patch("/collections/{collection_id}")
def update_collection(collection_id: int, body: DocCollectionUpdate, session: SessionDep):
    c = _get_collection(session, collection_id)
    try:
        c = doc_collections.update(session, c, body.model_dump(exclude_unset=True))
    except doc_collections.CollectionError as e:
        session.rollback()
        raise HTTPException(422, str(e)) from e
    return doc_collections.read(session, c)


@router.delete("/collections/{collection_id}", status_code=204)
def delete_collection(collection_id: int, session: SessionDep):
    """Docs and subcollections move up a level; no doc is deleted."""
    doc_collections.delete(session, _get_collection(session, collection_id))


@router.post("/collections/{collection_id}/summary")
async def regenerate_collection_summary(collection_id: int, request: Request, session: SessionDep):
    """Write the collection's summary now, instead of waiting for the background worker."""
    c = _get_collection(session, collection_id)
    contents, _ = doc_collections.summary_input(session, c)
    expected = doc_collections.contents_hash(session, c)
    try:
        summary = await doc_summaries.generate_collection(contents, request.app.state.runs.client_factory)
    except Exception as e:
        raise HTTPException(502, str(e) or "Couldn't write a summary.") from e
    session.refresh(c)
    c.summary, c.summary_hash = summary, expected
    session.add(c)
    session.commit()
    return doc_collections.read(session, c)


# --------------------------------------------------------------------------- import from Claude Code memory


def _memory_dir(project: Project, memory_dir: Optional[str]):
    try:
        return memory_import.resolve_memory_dir(project, memory_dir)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/projects/{project_id}/docs/import/claude-memory")
def preview_memory_import(project_id: int, session: SessionDep, memory_dir: Optional[str] = None):
    project = get_project_or_404(session, project_id)
    path = _memory_dir(project, memory_dir)
    return {"memory_dir": str(path), "items": [i.preview() for i in memory_import.plan(session, project, path)]}


class ImportChoice(BaseModel):
    include: Optional[bool] = None
    status: Optional[DocStatus] = None


class ImportRequest(BaseModel):
    memory_dir: Optional[str] = None
    choices: dict[str, ImportChoice] = {}


@router.post("/projects/{project_id}/docs/import/claude-memory")
def apply_memory_import(project_id: int, body: ImportRequest, session: SessionDep):
    project = get_project_or_404(session, project_id)
    path = _memory_dir(project, body.memory_dir)
    choices: dict[str, dict[str, Any]] = {k: v.model_dump(exclude_none=True) for k, v in body.choices.items()}
    return memory_import.apply(session, project, path, choices)


@router.delete("/projects/{project_id}/docs/import/claude-memory")
def remove_memory_import(project_id: int, session: SessionDep):
    project = get_project_or_404(session, project_id)
    return {"deleted": memory_import.remove_imported(session, project)}
