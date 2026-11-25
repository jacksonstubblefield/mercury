"""Database engine and session helpers."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import event, inspect, text
from sqlmodel import Session, SQLModel, create_engine

from . import config

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

    from .services import doc_store

    engine = get_engine()
    _migrate(engine)
    SQLModel.metadata.create_all(engine)
    with engine.begin() as conn:
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
        for table, column, ddl in _ADDED_COLUMNS:
            if table in tables and column not in {c["name"] for c in insp.get_columns(table)}:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
        if "doc" in tables:
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_doc_source_key ON doc (source_key)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_doc_collection_id ON doc (collection_id)"))
        if "docsection" in tables:
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_docsection_embed_hash ON docsection (embed_hash)"))


def get_session() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session
