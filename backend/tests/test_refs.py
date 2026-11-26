"""Project keys and per-project task/doc ids (services/refs.py)."""

from __future__ import annotations

import sqlite3

import pytest

from mercury.services import refs


def test_suggest_key():
    assert refs.suggest_key("mercury", set()) == "MER"
    assert refs.suggest_key("Big Data Pipeline", set()) == "BDP"
    assert refs.suggest_key("My cool little app thing", set()) == "MCLA"
    assert refs.suggest_key("mercury", {"MER"}) == "MERC"
    assert refs.suggest_key("mercury", {"MER", "MERC", "ME"}) == "MEC"
    assert refs.suggest_key("42 things", set()) == "P42"
    assert refs.suggest_key("x", set()) == "XX"
    assert refs.suggest_key("", set()) == "PX"
    taken = {"AB", "ABX"}
    assert refs.suggest_key("ab", taken) == "AB1"
    for name in ("mercury", "a b c d e f", "Ünïcødé", "  ", "123"):
        assert refs.KEY_RE.fullmatch(refs.suggest_key(name, set()))


def test_normalize_key():
    assert refs.normalize_key(" mer ") == "MER"
    assert refs.normalize_key("a1") == "A1"
    for bad in ("M", "MERCU", "1AB", "M-R", "", None):
        with pytest.raises(refs.ProjectKeyError):
            refs.normalize_key(bad)


def test_parse_refs():
    assert refs.parse_task_number("MER-12", "MER") == 12
    assert refs.parse_task_number("mer-12", "MER") == 12
    assert refs.parse_task_number(12, "MER") == 12
    assert refs.parse_task_number("12", "MER") == 12
    assert refs.parse_task_number("#12", "MER") == 12
    assert refs.parse_task_number("ABC-12", "MER") is None  # another project's
    assert refs.parse_task_number("MER-D12", "MER") is None  # a doc
    assert refs.parse_task_number(True, "MER") is None
    assert refs.parse_task_number(0, "MER") is None
    assert refs.parse_doc_number("MER-D3", "MER") == 3
    assert refs.parse_doc_number("d3", "MER") == 3
    assert refs.parse_doc_number(3, "MER") == 3
    assert refs.parse_doc_number("MER-3", "MER") == 3
    assert refs.parse_doc_number("ABC-D3", "MER") is None
    assert refs.task_ref("MER", 12) == "MER-12" and refs.doc_ref("MER", 3) == "MER-D3"


def test_projects_get_unique_keys(client):
    a = client.post("/api/projects", json={"name": "Demo"}).json()
    b = client.post("/api/projects", json={"name": "Demo"}).json()
    assert a["key"] == "DEM" and b["key"] != "DEM" and refs.KEY_RE.fullmatch(b["key"])

    assert client.get("/api/projects/suggest-key", params={"name": "Demo"}).json()["key"] not in {a["key"], b["key"]}

    c = client.post("/api/projects", json={"name": "Custom", "key": " xy "}).json()
    assert c["key"] == "XY"
    assert client.post("/api/projects", json={"name": "Clash", "key": "xy"}).status_code == 409
    assert client.post("/api/projects", json={"name": "Bad", "key": "toolong"}).status_code == 422

    assert client.patch(f"/api/projects/{c['id']}", json={"key": "zz9"}).json()["key"] == "ZZ9"
    assert client.patch(f"/api/projects/{c['id']}", json={"key": "ZZ9"}).status_code == 200  # its own key
    assert client.patch(f"/api/projects/{c['id']}", json={"key": "dem"}).status_code == 409
    assert client.patch(f"/api/projects/{c['id']}", json={"key": "9"}).status_code == 422
    assert client.get(f"/api/projects/{c['id']}").json()["key"] == "ZZ9"


def test_tasks_and_docs_are_numbered_per_project(client):
    a = client.post("/api/projects", json={"name": "Alpha"}).json()
    b = client.post("/api/projects", json={"name": "Beta"}).json()

    def task(p, title):
        return client.post(f"/api/projects/{p['id']}/tasks", json={"title": title}).json()

    def doc(p, title):
        return client.post(f"/api/projects/{p['id']}/docs", json={"title": title}).json()

    assert [task(a, "a1")["number"], task(b, "b1")["number"], task(a, "a2")["number"]] == [1, 1, 2]
    last = task(a, "a3")
    assert client.delete(f"/api/tasks/{last['id']}").status_code == 204
    assert task(a, "a4")["number"] == 4  # numbers aren't handed out again

    assert [doc(a, "d1")["number"], doc(b, "d1")["number"], doc(a, "d2")["number"]] == [1, 1, 2]
    listed = client.get(f"/api/projects/{a['id']}/docs").json()
    assert sorted(d["number"] for d in listed) == [1, 2]


def test_agent_prompt_names_the_task_by_its_project_id(client):
    from mercury.models import Project, Task
    from mercury.services.agent_runner import build_system_append

    project = Project(id=None, name="Demo", key="DEM")
    task = Task(project_id=1, number=7, title="Fix it")
    text = build_system_append(project, task)
    assert "You are working on task DEM-7: Fix it" in text and "Task DEM-7 moves to Review" in text


def test_old_databases_get_keys_and_numbers(tmp_path, monkeypatch):
    from mercury import db

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE project (
            id INTEGER PRIMARY KEY, name VARCHAR NOT NULL,
            description VARCHAR NOT NULL DEFAULT '', repo_path VARCHAR, github_repo VARCHAR,
            status VARCHAR(8) NOT NULL DEFAULT 'active', color VARCHAR NOT NULL DEFAULT '#7c8cff',
            created_at DATETIME NOT NULL DEFAULT '2026-01-01 00:00:00',
            updated_at DATETIME NOT NULL DEFAULT '2026-01-01 00:00:00'
        );
        CREATE TABLE task (id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, title VARCHAR NOT NULL);
        CREATE TABLE doc (
            title VARCHAR NOT NULL, body VARCHAR NOT NULL DEFAULT '', pinned BOOLEAN NOT NULL DEFAULT 0,
            id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL,
            created_at DATETIME NOT NULL DEFAULT '2026-01-01 00:00:00',
            updated_at DATETIME NOT NULL DEFAULT '2026-01-01 00:00:00'
        );
        INSERT INTO project (id, name) VALUES (1, 'Mercury'), (2, 'Mercury two'), (3, 'Website');
        INSERT INTO task VALUES (5, 1, 'a'), (6, 3, 'b'), (9, 1, 'c'), (23, 1, 'd');
        INSERT INTO doc (id, project_id, title) VALUES (4, 3, 'x'), (8, 3, 'y'), (10, 1, 'z');
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
    try:
        keys = con.execute("SELECT id, key, task_seq, doc_seq FROM project ORDER BY id").fetchall()
        tasks = con.execute("SELECT id, project_id, number FROM task ORDER BY id").fetchall()
        docs = con.execute("SELECT id, project_id, number FROM doc ORDER BY id").fetchall()
        indexes = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    finally:
        con.close()
    assert keys == [(1, "MER", 3, 1), (2, "MT", 0, 0), (3, "WEB", 1, 2), (4, "SCR", 0, 0)]  # 4: Scratch, added
    assert tasks == [(5, 1, 1), (6, 3, 1), (9, 1, 2), (23, 1, 3)]  # creation order within each project
    assert docs == [(4, 3, 1), (8, 3, 2), (10, 1, 1)]
    assert {"ux_project_key", "ux_task_project_number", "ux_doc_project_number"} <= indexes
