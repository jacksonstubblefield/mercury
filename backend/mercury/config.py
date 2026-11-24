"""Runtime configuration, read from environment variables."""

from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    """Where Mercury keeps its database. Override with MERCURY_DATA_DIR."""
    path = Path(os.environ.get("MERCURY_DATA_DIR") or Path.home() / ".mercury")
    path.mkdir(parents=True, exist_ok=True)
    return path


def database_url() -> str:
    return os.environ.get("MERCURY_DATABASE_URL") or f"sqlite:///{data_dir() / 'mercury.db'}"


def host() -> str:
    return os.environ.get("MERCURY_HOST", "127.0.0.1")


def port() -> int:
    return int(os.environ.get("MERCURY_PORT", "8765"))


def frontend_dist() -> Path | None:
    """Built frontend to serve at / (used by the packaged desktop app)."""
    configured = os.environ.get("MERCURY_FRONTEND_DIST")
    path = Path(configured) if configured else Path(__file__).resolve().parents[2] / "frontend" / "dist"
    return path if (path / "index.html").exists() else None


# Default model for agent runs. Overridable per run.
DEFAULT_AGENT_MODEL = os.environ.get("MERCURY_AGENT_MODEL", "claude-opus-5-5")

# Model that writes commit messages in the Source Control tab. A fast model keeps the button snappy.
COMMIT_MESSAGE_MODEL = os.environ.get("MERCURY_COMMIT_MESSAGE_MODEL", "claude-haiku-5-5")

# Model that writes the agent-facing summaries of docs.
DOC_SUMMARY_MODEL = os.environ.get("MERCURY_DOC_SUMMARY_MODEL", "claude-haiku-5-5")


def embeddings_enabled() -> bool:
    """Meaning-based doc search with a local model. MERCURY_EMBEDDINGS=0 turns it off (tests do)."""
    return os.environ.get("MERCURY_EMBEDDINGS", "1") != "0"


# Local embedding model, run in-process by fastembed (ONNX Runtime, CPU). Changing it
# re-embeds every section in the background.
EMBEDDING_MODEL = os.environ.get("MERCURY_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
# CPU threads for embedding. Gains flatten out past ~4, and fewer threads leave the
# machine responsive while a large import is indexed.
EMBEDDING_THREADS = int(os.environ.get("MERCURY_EMBEDDING_THREADS", "4"))


def models_dir() -> Path:
    """Where downloaded embedding models are kept."""
    path = data_dir() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def doc_summaries_enabled() -> bool:
    """Background summary writing. MERCURY_DOC_SUMMARIES=0 turns it off (tests do)."""
    return os.environ.get("MERCURY_DOC_SUMMARIES", "1") != "0"
