from __future__ import annotations

import asyncio
import json

from mercury import config

DEFAULTS = {"default_permission_mode": "acceptEdits", "default_model": "claude-opus-5-5", "agent_settings_access": "change", "agent_notifications": True, "session_open_scroll": "last_message"}


def test_defaults_when_nothing_saved(client):
    r = client.get("/api/preferences")
    assert r.status_code == 200
    assert r.json() == DEFAULTS


def test_save_and_reload(client):
    r = client.put("/api/preferences", json={"default_permission_mode": "bypassPermissions"})
    assert r.status_code == 200
    assert (config.data_dir() / "preferences.json").exists()
    assert client.get("/api/preferences").json() == {**DEFAULTS, "default_permission_mode": "bypassPermissions"}


def test_rejects_unknown_mode(client):
    assert client.put("/api/preferences", json={"default_permission_mode": "yolo"}).status_code == 422


def test_unreadable_file_falls_back_to_defaults(client):
    config.data_dir().mkdir(parents=True, exist_ok=True)
    (config.data_dir() / "preferences.json").write_text("{not json", encoding="utf-8")
    assert client.get("/api/preferences").json() == DEFAULTS


def test_older_file_gets_new_defaults(client):
    config.data_dir().mkdir(parents=True, exist_ok=True)
    (config.data_dir() / "preferences.json").write_text('{"default_permission_mode": "plan"}', encoding="utf-8")
    assert client.get("/api/preferences").json() == {**DEFAULTS, "default_permission_mode": "plan"}


def test_patch_changes_only_what_is_given(client):
    client.put("/api/preferences", json={**DEFAULTS, "agent_settings_access": "read"})
    r = client.patch("/api/preferences", json={"default_permission_mode": "plan"})
    assert r.status_code == 200, r.text
    assert r.json() == {**DEFAULTS, "default_permission_mode": "plan", "agent_settings_access": "read"}


def test_patch_rejects_unknown_keys_and_bad_values(client):
    r = client.patch("/api/preferences", json={"theme": "dark"})
    assert r.status_code == 422 and "Unknown setting" in r.json()["detail"]
    r = client.patch("/api/preferences", json={"agent_settings_access": "admin"})
    assert r.status_code == 422 and "agent_settings_access" in r.json()["detail"]
    assert client.get("/api/preferences").json() == DEFAULTS  # nothing was saved


def test_session_open_scroll(client):
    for value in ("top", "bottom", "last_message"):
        r = client.patch("/api/preferences", json={"session_open_scroll": value})
        assert r.status_code == 200, r.text
        assert r.json()["session_open_scroll"] == value
    assert client.patch("/api/preferences", json={"session_open_scroll": "middle"}).status_code == 422


# --------------------------------------------------------------------------- agent tools


def _call(name, **args):
    from mercury.services.mercury_tools import build_settings_tools

    handler = next(t for t in build_settings_tools() if t.name == name).handler
    result = asyncio.run(handler(args))
    text = result["content"][0]["text"]
    return (text, True) if result.get("is_error") else (json.loads(text), False)


def _settings(data):
    return {s["key"]: s for s in data["settings"]}


def test_agent_reads_settings(client):
    data, err = _call("get_settings")
    assert not err
    settings = _settings(data)
    mode = settings["default_permission_mode"]
    assert mode["value"] == mode["default"] == "acceptEdits"
    assert "plan" in mode["allowed_values"] and mode["description"]
    assert mode["agent_can_change"] is True
    assert settings["agent_settings_access"]["agent_can_change"] is False


def test_agent_changes_a_setting(client):
    data, err = _call("update_settings", changes={"default_permission_mode": "bypassPermissions"})
    assert not err
    assert _settings(data)["default_permission_mode"]["value"] == "bypassPermissions"
    assert client.get("/api/preferences").json()["default_permission_mode"] == "bypassPermissions"


def test_agent_cannot_change_settings_on_read_access(client):
    client.patch("/api/preferences", json={"agent_settings_access": "read"})
    for changes in ({"agent_settings_access": "change"}, {"default_permission_mode": "plan"}):
        text, err = _call("update_settings", changes=changes)
        assert err and "Nothing was changed" in text
    assert client.get("/api/preferences").json() == {**DEFAULTS, "agent_settings_access": "read"}
    data, err = _call("get_settings")
    assert not err  # reading is still fine


def test_agent_cannot_widen_its_own_access(client):
    text, err = _call("update_settings", changes={"agent_settings_access": "off", "default_permission_mode": "plan"})
    assert err and "Only the user" in text
    assert client.get("/api/preferences").json() == DEFAULTS  # nothing applied, not even the allowed key


def test_agent_access_off_hides_settings(client):
    client.patch("/api/preferences", json={"agent_settings_access": "off"})
    text, err = _call("get_settings")
    assert err and "turned off" in text


def test_agent_gets_clear_errors_for_bad_changes(client):
    text, err = _call("update_settings", changes={"nope": 1})
    assert err and "Unknown setting" in text
    text, err = _call("update_settings", changes={"default_permission_mode": "yolo"})
    assert err and "Invalid value" in text
    text, err = _call("update_settings")
    assert err
