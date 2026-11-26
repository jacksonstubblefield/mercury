"""The built-in Scratch project: sessions that don't belong to any of the user's projects."""

from __future__ import annotations

import shutil

from sqlalchemy import text

from mercury import config, db

from test_agent_runs import FakeClient, _wait_for


def _scratch(client) -> dict:
    found = [p for p in client.get("/api/projects").json() if p["scratch"]]
    assert len(found) == 1
    return found[0]


def test_scratch_exists_from_the_start(client):
    p = _scratch(client)
    folder = config.data_dir() / "scratch"
    assert (p["name"], p["key"], p["repo_path"]) == ("Scratch", "SCR", str(folder))
    assert folder.is_dir()


def test_scratch_is_created_once_and_added_to_older_databases(make_client):
    client = make_client()
    mine = client.post("/api/projects", json={"name": "Screenshots", "key": "SCR2"}).json()
    # A database from before Scratch: no column, no row, and the user already uses its usual key.
    with db.get_engine().begin() as conn:
        conn.execute(text("DELETE FROM project WHERE scratch"))
        conn.execute(text("ALTER TABLE project DROP COLUMN scratch"))
        conn.execute(text("UPDATE project SET key = 'SCR' WHERE id = :id"), {"id": mine["id"]})

    db.init_db()
    db.init_db()  # a later start adds no second one

    p = _scratch(client)
    assert p["key"] != "SCR"
    assert client.get(f"/api/projects/{mine['id']}").json()["scratch"] is False


def test_scratch_cannot_be_deleted_renamed_or_archived(client):
    p = _scratch(client)
    url = f"/api/projects/{p['id']}"
    assert client.delete(url).status_code == 400
    assert client.patch(url, json={"name": "Misc"}).status_code == 400
    assert client.patch(url, json={"status": "archived"}).status_code == 400
    assert _scratch(client)["name"] == "Scratch"

    # The settings form sends unchanged fields too.
    r = client.patch(url, json={"name": "Scratch", "status": "active", "color": "#4fd18b"})
    assert r.status_code == 200 and r.json()["color"] == "#4fd18b"


def test_api_cannot_set_the_scratch_flag(client):
    created = client.post("/api/projects", json={"name": "Mine", "scratch": True}).json()
    assert created["scratch"] is False
    assert client.patch(f"/api/projects/{created['id']}", json={"scratch": True}).json()["scratch"] is False
    assert client.delete(f"/api/projects/{created['id']}").status_code == 204


def test_scratch_folder_can_be_changed_and_reset(client, tmp_path):
    url = f"/api/projects/{_scratch(client)['id']}"
    default = str(config.data_dir() / "scratch")
    assert client.patch(url, json={"repo_path": str(tmp_path)}).json()["repo_path"] == str(tmp_path)
    for blank in (None, "  "):
        assert client.patch(url, json={"repo_path": blank}).json()["repo_path"] == default


def test_scratch_session_runs_without_a_project(make_client):
    client = make_client(lambda opts: FakeClient(opts))
    p = _scratch(client)
    client.post(
        f"/api/projects/{p['id']}/docs",
        json={"title": "Scratch rules", "body": "Answer in haiku.", "kind": "rules"},
    )
    folder = config.data_dir() / "scratch"
    shutil.rmtree(folder)  # cleared out by the user: the next run makes it again

    run = client.post(f"/api/projects/{p['id']}/runs", json={"prompt": "try something"}).json()
    _wait_for(client, run["id"], {"succeeded"})

    opts = FakeClient.instances[-1].options
    assert opts.cwd == str(folder) and folder.is_dir()
    prompt = opts.system_prompt["append"]
    assert "scratch session" in prompt
    assert "Answer in haiku." in prompt
    assert "Save as you go" not in prompt  # project runs are told to keep a memory; scratch ones aren't

    # It shows up in the sidebar's Sessions list, and can be followed up in the same folder.
    assert [a["project_id"] for a in client.get("/api/sessions/attention").json()] == [p["id"]]
    follow = client.post(f"/api/runs/{run['id']}/follow-up", json={"prompt": "more"}).json()
    _wait_for(client, follow["id"], {"succeeded"})
    assert FakeClient.instances[-1].options.cwd == str(folder)
    assert FakeClient.instances[-1].options.resume == "sess-1"
