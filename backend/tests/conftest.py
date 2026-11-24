from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mercury import db
from mercury.app import create_app
from mercury.services.agent_runner import RunManager


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    """Build a TestClient backed by a fresh SQLite DB and an injectable agent client."""
    monkeypatch.setenv("MERCURY_DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("MERCURY_FRONTEND_DIST", str(tmp_path / "no-dist"))
    monkeypatch.setenv("MERCURY_DOC_SUMMARIES", "0")  # tests drive SummaryWorker directly, with a fake
    monkeypatch.setenv("MERCURY_EMBEDDINGS", "0")  # tests install a fake embedder where they need one
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))  # never read the real ~/.claude
    db.reset_engine()
    clients: list[TestClient] = []

    def _make(client_factory=None) -> TestClient:
        manager = RunManager(client_factory=client_factory) if client_factory else RunManager()
        client = TestClient(create_app(manager))
        client.__enter__()
        clients.append(client)
        return client

    yield _make
    for c in clients:
        c.__exit__(None, None, None)
    db.reset_engine()


@pytest.fixture
def client(make_client):
    return make_client()
