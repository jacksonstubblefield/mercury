"""Meaning-based doc search: a local embedding model, a background indexer and an in-memory vector index.

- The model (fastembed + ONNX Runtime, CPU only) runs inside the backend; there's no
  separate service. It's downloaded once from Hugging Face into ~/.mercury/models.
- EmbeddingWorker embeds doc sections in the background. Vectors are cached by the hash
  of the embedded text, so re-saving a doc only embeds the sections that changed.
- vector_search() scores a query against every embedded section of a project. The
  matrix is cached in memory and rebuilt when doc_store's generation changes.
- If the model isn't available (still loading, offline on first use, disabled), search
  quietly falls back to keyword-only.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Optional, Protocol

import numpy as np
from sqlalchemy import text
from sqlmodel import Session, select

from .. import config
from ..db import get_engine
from ..models import Embedding
from . import doc_store

log = logging.getLogger(__name__)

BATCH = 16


class Embedder(Protocol):
    model: str

    def embed_passages(self, texts: list[str]) -> np.ndarray: ...

    def embed_query(self, query: str) -> np.ndarray: ...


def _normalize(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32)
    norms = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.maximum(norms, 1e-12)


class FastEmbedder:
    """fastembed's TextEmbedding, loaded once and shared; calls are serialized."""

    def __init__(self, model: str, threads: int):
        # Mercury only downloads public models: never send the user's (possibly expired)
        # Hugging Face login, and don't send telemetry.
        os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        from fastembed import TextEmbedding  # imported lazily: it's slow to import

        self.model = model
        self._impl = TextEmbedding(model_name=model, cache_dir=str(config.models_dir()), threads=threads)
        self._lock = threading.Lock()

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        with self._lock:
            return _normalize(np.array(list(self._impl.passage_embed(texts, batch_size=BATCH))))

    def embed_query(self, query: str) -> np.ndarray:
        with self._lock:
            return _normalize(np.array(list(self._impl.query_embed(query))[0]))


# --------------------------------------------------------------------------- the shared embedder


@dataclass
class Status:
    state: str = "off"  # off | loading | ready | error
    model: str = ""
    error: Optional[str] = None


_embedder: Optional[Embedder] = None
_status = Status()
_state_lock = threading.Lock()


def status() -> Status:
    return _status


def ready_embedder() -> Optional[Embedder]:
    """The embedder if it's loaded; search never waits for (or triggers) a model load."""
    return _embedder if _status.state == "ready" else None


def set_embedder(embedder: Optional[Embedder]) -> None:
    """Install an embedder (tests pass a fake) or None to turn meaning search off."""
    global _embedder, _status
    with _state_lock:
        _embedder = embedder
        _status = Status("ready", embedder.model) if embedder else Status("off")
    _vectors.clear()


def load_default() -> Optional[Embedder]:
    """Load the configured model (blocking: call from a thread). Records errors in status()."""
    global _embedder, _status
    with _state_lock:
        if _embedder is not None:
            return _embedder
        _status = Status("loading", config.EMBEDDING_MODEL)
    try:
        embedder = FastEmbedder(config.EMBEDDING_MODEL, config.EMBEDDING_THREADS)
    except Exception as e:
        log.warning("Couldn't load embedding model %s", config.EMBEDDING_MODEL, exc_info=True)
        with _state_lock:
            _status = Status("error", config.EMBEDDING_MODEL, str(e) or type(e).__name__)
        return None
    with _state_lock:
        _embedder = embedder
        _status = Status("ready", embedder.model)
    return embedder


# --------------------------------------------------------------------------- indexing


def pending_sections(session: Session, model: str, limit: int) -> list[tuple[str, str]]:
    """(embed_hash, text) of sections with no vector for `model`, newest docs first, deduplicated."""
    rows = session.execute(
        text(
            "SELECT s.embed_hash, d.title, s.heading, s.text FROM docsection s JOIN doc d ON d.id = s.doc_id "
            "WHERE s.embed_hash != '' AND NOT EXISTS "
            "(SELECT 1 FROM embedding e WHERE e.model = :m AND e.text_hash = s.embed_hash) "
            "ORDER BY d.updated_at DESC LIMIT :n"
        ).bindparams(m=model, n=limit * 4)
    ).all()
    out: dict[str, str] = {}
    for h, title, heading, body in rows:
        out.setdefault(h, doc_store.embed_text(title, heading, body))
        if len(out) >= limit:
            break
    return list(out.items())


def counts(session: Session, project_id: int, model: str) -> tuple[int, int]:
    """(sections, embedded sections) of a project."""
    row = session.execute(
        text(
            "SELECT COUNT(*), SUM(CASE WHEN EXISTS (SELECT 1 FROM embedding e WHERE e.model = :m AND "
            "e.text_hash = s.embed_hash) THEN 1 ELSE 0 END) FROM docsection s JOIN doc d ON d.id = s.doc_id "
            "WHERE d.project_id = :p"
        ).bindparams(m=model, p=project_id)
    ).one()
    return int(row[0] or 0), int(row[1] or 0)


def store_vectors(session: Session, model: str, items: list[tuple[str, np.ndarray]]) -> int:
    existing = set(
        session.exec(
            select(Embedding.text_hash).where(Embedding.model == model, Embedding.text_hash.in_([h for h, _ in items]))
        ).all()
    )
    added = 0
    for h, vec in items:
        if h not in existing:
            session.add(Embedding(model=model, text_hash=h, vector=np.asarray(vec, dtype=np.float32).tobytes()))
            added += 1
    session.commit()
    if added:
        doc_store.bump_generation()
    return added


def collect_garbage(session: Session, model: str) -> int:
    """Drop vectors no section uses any more, and those of other models."""
    result = session.execute(
        text(
            "DELETE FROM embedding WHERE model != :m OR text_hash NOT IN "
            "(SELECT embed_hash FROM docsection WHERE embed_hash != '')"
        ).bindparams(m=model)
    )
    session.commit()
    return result.rowcount or 0


class EmbeddingWorker:
    """Keeps every section embedded. Loads the model on start (downloading it the first time)."""

    def __init__(self, *, idle_seconds: float = 5, retry_load_seconds: float = 600, gc_seconds: float = 600):
        self.idle_seconds = idle_seconds
        self.retry_load_seconds = retry_load_seconds
        self.gc_seconds = gc_seconds
        self._task: asyncio.Task | None = None
        self._last_gc = 0.0

    async def run_once(self, embedder: Embedder) -> int:
        """Embed one batch of pending sections. Returns how many vectors were stored."""
        with Session(get_engine()) as s:
            batch = pending_sections(s, embedder.model, BATCH)
        if not batch:
            return 0
        vectors = await asyncio.to_thread(embedder.embed_passages, [t for _, t in batch])
        with Session(get_engine()) as s:
            return store_vectors(s, embedder.model, [(h, v) for (h, _), v in zip(batch, vectors)])

    async def _loop(self) -> None:
        while True:
            embedder = ready_embedder() or await asyncio.to_thread(load_default)
            if embedder is None:
                await asyncio.sleep(self.retry_load_seconds)
                continue
            try:
                stored = await self.run_once(embedder)
                if time.monotonic() - self._last_gc > self.gc_seconds:
                    with Session(get_engine()) as s:
                        collect_garbage(s, embedder.model)
                    self._last_gc = time.monotonic()
            except Exception:
                log.warning("Embedding pass failed", exc_info=True)
                stored = 0
            await asyncio.sleep(0.05 if stored else self.idle_seconds)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None


# --------------------------------------------------------------------------- search


class _VectorCache:
    """Per-project matrix of section vectors, rebuilt when docs or embeddings change."""

    def __init__(self):
        self._lock = threading.Lock()
        self._data: dict[tuple[int, str], tuple[int, np.ndarray, np.ndarray, np.ndarray]] = {}

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def get(self, session: Session, project_id: int, model: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(section ids, their doc ids, vector matrix) for a project."""
        gen = doc_store.generation()
        key = (project_id, model)
        with self._lock:
            cached = self._data.get(key)
            if cached and cached[0] == gen:
                return cached[1], cached[2], cached[3]
        rows = session.execute(
            text(
                "SELECT s.id, s.doc_id, e.vector FROM docsection s JOIN doc d ON d.id = s.doc_id "
                "JOIN embedding e ON e.text_hash = s.embed_hash AND e.model = :m WHERE d.project_id = :p"
            ).bindparams(m=model, p=project_id)
        ).all()
        ids = np.array([r[0] for r in rows], dtype=np.int64)
        doc_ids = np.array([r[1] for r in rows], dtype=np.int64)
        matrix = np.vstack([np.frombuffer(r[2], dtype=np.float32) for r in rows]) if rows else np.zeros((0, 0), np.float32)
        with self._lock:
            self._data[key] = (gen, ids, doc_ids, matrix)
        return ids, doc_ids, matrix


_vectors = _VectorCache()


def vector_search(
    session: Session, project_id: int, query: str, limit: int, doc_ids: Optional[set[int]] = None
) -> Optional[list[tuple[int, float]]]:
    """(section id, cosine similarity) of the closest sections, best first; None when unavailable.

    doc_ids: only consider sections of these docs.
    """
    embedder = ready_embedder()
    if embedder is None or not query.strip():
        return None
    try:
        ids, section_docs, matrix = _vectors.get(session, project_id, embedder.model)
        if len(ids) == 0:
            return []
        q = embedder.embed_query(query)
        sims = matrix @ q
        if doc_ids is not None:
            sims = np.where(np.isin(section_docs, np.fromiter(doc_ids, dtype=np.int64)), sims, -np.inf)
    except Exception:
        log.warning("Vector search failed; using keyword search only", exc_info=True)
        return None
    top = np.argsort(-sims)[:limit]
    return [(int(ids[i]), float(sims[i])) for i in top if np.isfinite(sims[i])]
