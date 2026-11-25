"""Database engine and session helpers."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import event, inspect, text
from sqlmodel import Session, SQLModel, create_engine

from . import config
from .services import refs as _refs  # noqa: F401  (registers the hook that numbers new tasks and docs)

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        url = config.database_url()
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, connect_args=connect_args)
        if url.startswith("sqlite"):

            @event.listens_for(_engine, "connect")
            def _sqlite_pragmas(dbapi_conn, _record):
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA journal_mode=WAL")
                cur.close()

    return _engine


def reset_engine() -> None:
    """Drop the cached engine (tests point MERCURY_DATABASE_URL elsewhere)."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


def init_db() -> None:
    from . import models  # noqa: F401  (register tables)

    from .services import doc_store, refs

    engine = get_engine()
    _migrate(engine)
    SQLModel.metadata.create_all(engine)
    with engine.begin() as conn:
        refs.backfill(conn)  # project keys and per-project task/doc numbers for rows from before them
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_project_key ON project (key)"))
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_task_project_number ON task (project_id, number)"))
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_doc_project_number ON doc (project_id, number)"))
        conn.execute(
            text("CREATE UNIQUE INDEX IF NOT EXISTS ux_embedding_model_hash ON embedding (model, text_hash)")
        )
        if engine.dialect.name == "sqlite":
            doc_store.create_fts(conn)
    with Session(engine) as session:
        doc_store.backfill(session)


# Columns added to existing tables after their first release: (table, column, DDL).
_ADDED_COLUMNS = [
    ("doc", "status", "VARCHAR(10) NOT NULL DEFAULT 'current'"),
    ("doc", "kind", "VARCHAR(5) NOT NULL DEFAULT 'doc'"),
    ("doc", "superseded_by", "INTEGER"),
    ("doc", "origin", "VARCHAR(6) NOT NULL DEFAULT 'user'"),
    ("doc", "content_hash", "VARCHAR NOT NULL DEFAULT ''"),
    ("doc", "summary", "VARCHAR NOT NULL DEFAULT ''"),
    ("doc", "summary_hash", "VARCHAR NOT NULL DEFAULT ''"),
    ("doc", "source_key", "VARCHAR"),
    ("doc", "source_hash", "VARCHAR"),
    ("docsection", "embed_hash", "VARCHAR NOT NULL DEFAULT ''"),
    ("doc", "collection_id", "INTEGER"),
    ("agentrun", "purpose", "VARCHAR(14) NOT NULL DEFAULT 'work'"),
    ("agentrun", "images", "JSON NOT NULL DEFAULT '[]'"),
    ("project", "default_permission_mode", "VARCHAR(17)"),
    ("project", "default_model", "VARCHAR(40)"),
    # Default 1: runs from before this column existed shouldn't all show up as unseen in the sidebar.
    ("agentrun", "seen", "BOOLEAN NOT NULL DEFAULT 1"),
    # Backfilled by _split_run_costs when first added.
    ("agentrun", "session_cost_usd", "FLOAT"),
    # Filled in by refs.backfill (init_db).
    ("project", "key", "VARCHAR(4)"),
    ("project", "task_seq", "INTEGER NOT NULL DEFAULT 0"),
    ("project", "doc_seq", "INTEGER NOT NULL DEFAULT 0"),
    ("task", "number", "INTEGER"),
    ("doc", "number", "INTEGER"),
    ("task", "images", "JSON NOT NULL DEFAULT '[]'"),
]


def _migrate(engine) -> None:
    """Small, idempotent schema changes for databases made by older versions."""
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    # Notes were renamed to docs: keep the user's notes by renaming the table.
    if "note" in tables and "doc" not in tables:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE note RENAME TO doc"))
            # Rename the index too (create_all skips indexes of tables that already exist).
            conn.execute(text("DROP INDEX IF EXISTS ix_note_project_id"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_doc_project_id ON doc (project_id)"))
        insp = inspect(engine)
        tables = set(insp.get_table_names())
    with engine.begin() as conn:
        added = set()
        for table, column, ddl in _ADDED_COLUMNS:
            if table in tables and column not in {c["name"] for c in insp.get_columns(table)}:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
                added.add((table, column))
        if ("agentrun", "session_cost_usd") in added:
            _split_run_costs(conn)
        if "doc" in tables:
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_doc_source_key ON doc (source_key)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_doc_collection_id ON doc (collection_id)"))
        if "docsection" in tables:
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_docsection_embed_hash ON docsection (embed_hash)"))


def _split_run_costs(conn) -> None:
    """Older runs stored the SDK's session running total as cost_usd, so a session's follow-ups were
    counted again and again when summed. Keep the total in session_cost_usd and make cost_usd the run's share."""
    rows = conn.execute(text("SELECT id, parent_run_id, session_id, cost_usd FROM agentrun ORDER BY id")).all()
    by_id = {r.id: r for r in rows}
    for r in rows:
        if r.cost_usd is None:
            continue
        baseline, parent = 0.0, by_id.get(r.parent_run_id)
        while parent is not None:  # nearest ancestor in the same session that recorded a total
            if parent.cost_usd is not None and parent.session_id == r.session_id:
                baseline = parent.cost_usd
                break
            parent = by_id.get(parent.parent_run_id)
        conn.execute(
            text("UPDATE agentrun SET session_cost_usd = :total, cost_usd = :own WHERE id = :id"),
            {"total": r.cost_usd, "own": max(0.0, r.cost_usd - baseline), "id": r.id},
        )


def get_session() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session
