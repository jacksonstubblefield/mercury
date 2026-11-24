from __future__ import annotations

import subprocess


def _project(client, **kw):
    body = {"name": "Demo", **kw}
    r = client.post("/api/projects", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_project_crud_and_counts(client):
    p = _project(client, description="hello")
    assert p["open_tasks"] == 0

    client.post(f"/api/projects/{p['id']}/tasks", json={"title": "A"})
    client.post(f"/api/projects/{p['id']}/tasks", json={"title": "B", "status": "done"})
    p2 = client.get(f"/api/projects/{p['id']}").json()
    assert p2["open_tasks"] == 1

    r = client.patch(f"/api/projects/{p['id']}", json={"status": "paused"})
    assert r.json()["status"] == "paused"

    assert client.delete(f"/api/projects/{p['id']}").status_code == 204
    assert client.get(f"/api/projects/{p['id']}").status_code == 404


def test_task_positions_and_update(client):
    p = _project(client)
    t1 = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "one"}).json()
    t2 = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "two"}).json()
    assert t2["position"] > t1["position"]

    r = client.patch(f"/api/tasks/{t1['id']}", json={"status": "in_progress", "priority": "high"})
    assert r.json()["status"] == "in_progress"
    assert r.json()["priority"] == "high"

    tasks = client.get(f"/api/projects/{p['id']}/tasks").json()
    assert {t["title"] for t in tasks} == {"one", "two"}


def test_docs(client):
    p = _project(client)
    d = client.post(f"/api/projects/{p['id']}/docs", json={"title": "Spec", "body": "# Hi"}).json()
    client.patch(f"/api/docs/{d['id']}", json={"pinned": True})
    docs = client.get(f"/api/projects/{p['id']}/docs").json()
    assert docs[0]["pinned"] is True
    assert client.get(f"/api/docs/{d['id']}").json()["body"] == "# Hi"
    assert client.delete(f"/api/docs/{d['id']}").status_code == 204
    assert client.get(f"/api/docs/{d['id']}").status_code == 404


def test_notes_table_is_migrated_to_docs(tmp_path, monkeypatch):
    import sqlite3

    from mercury import db

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE project (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL);
        CREATE TABLE note (
            title VARCHAR NOT NULL, body VARCHAR NOT NULL, pinned BOOLEAN NOT NULL,
            id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES project (id) ON DELETE CASCADE,
            created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
        );
        CREATE INDEX ix_note_project_id ON note (project_id);
        INSERT INTO project (id, name) VALUES (1, 'Old');
        INSERT INTO note VALUES ('Kept', 'body', 1, 7, 1, '2026-01-01 00:00:00', '2026-01-01 00:00:00');
        """
    )
    con.commit()
    con.close()

    monkeypatch.setenv("MERCURY_DATABASE_URL", f"sqlite:///{path}")
    db.reset_engine()
    try:
        db.init_db()
        db.init_db()  # idempotent
    finally:
        db.reset_engine()

    con = sqlite3.connect(path)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'index')")}
    rows = con.execute("SELECT id, title, project_id FROM doc").fetchall()
    con.close()
    assert "note" not in tables and "ix_note_project_id" not in tables and "ix_doc_project_id" in tables
    assert rows == [(7, "Kept", 1)]

    # The doc gained the new columns, was indexed, and is searchable.
    monkeypatch.setenv("MERCURY_DATABASE_URL", f"sqlite:///{path}")
    from sqlmodel import Session

    from mercury.models import Doc
    from mercury.services import doc_store

    try:
        with Session(db.get_engine()) as s:
            doc = s.get(Doc, 7)
            assert doc.status.value == "current" and doc.origin.value == "user" and doc.content_hash
            assert doc_store.summary_stale(doc)  # the worker will write one
            assert [h.doc.id for h in doc_store.search(s, 1, "body")] == [7]
    finally:
        db.reset_engine()


def test_git_summary_and_github_detection(client, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-C", str(repo)]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "remote", "add", "origin", "git@github.com:octo/widgets.git"], check=True)
    (repo / "a.txt").write_text("x")
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run(
        [*git, "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-qm", "first"], check=True
    )
    (repo / "b.txt").write_text("y")

    p = _project(client, repo_path=str(repo))
    assert p["github_repo"] == "octo/widgets"

    info = client.get(f"/api/projects/{p['id']}/git").json()
    assert info["commits"][0]["subject"] == "first"
    assert any(f["path"] == "b.txt" for f in info["changed_files"])


def test_cross_origin_requests_rejected(client):
    r = client.post("/api/projects", json={"name": "x"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.get("/api/health", headers={"Host": "evil.example"})
    assert r.status_code == 403
    r = client.post("/api/projects", json={"name": "ok"}, headers={"Origin": "http://localhost:5173"})
    assert r.status_code == 201
