from __future__ import annotations

from mercury import config


def test_defaults_when_nothing_saved(client):
    r = client.get("/api/preferences")
    assert r.status_code == 200
    assert r.json() == {"default_permission_mode": "acceptEdits"}


def test_save_and_reload(client):
    r = client.put("/api/preferences", json={"default_permission_mode": "bypassPermissions"})
    assert r.status_code == 200
    assert (config.data_dir() / "preferences.json").exists()
    assert client.get("/api/preferences").json() == {"default_permission_mode": "bypassPermissions"}


def test_rejects_unknown_mode(client):
    assert client.put("/api/preferences", json={"default_permission_mode": "yolo"}).status_code == 422


def test_unreadable_file_falls_back_to_defaults(client):
    config.data_dir().mkdir(parents=True, exist_ok=True)
    (config.data_dir() / "preferences.json").write_text("{not json", encoding="utf-8")
    assert client.get("/api/preferences").json() == {"default_permission_mode": "acceptEdits"}
