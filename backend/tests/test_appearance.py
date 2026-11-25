from __future__ import annotations

import asyncio
import json

from mercury import config


def test_default_when_nothing_saved(client):
    r = client.get("/api/appearance")
    assert r.status_code == 200
    assert r.json() == {"preset": "mercury-dark", "colors": {}, "font_ui": None, "font_mono": None, "radius": None}


def test_save_and_reload(client):
    body = {"preset": "hot-pink", "colors": {"--accent": "#FF3EA5"}, "font_ui": "Inter, sans-serif", "radius": 12}
    r = client.put("/api/appearance", json=body)
    assert r.status_code == 200
    assert r.json()["colors"] == {"--accent": "#ff3ea5"}  # stored lower-case
    assert (config.data_dir() / "appearance.json").exists()

    got = client.get("/api/appearance").json()
    assert got == {
        "preset": "hot-pink",
        "colors": {"--accent": "#ff3ea5"},
        "font_ui": "Inter, sans-serif",
        "font_mono": None,
        "radius": 12,
    }


def test_blank_font_means_default(client):
    assert client.put("/api/appearance", json={"font_ui": "   "}).json()["font_ui"] is None


def test_rejects_bad_values(client):
    bad = [
        {"colors": {"--accent": "hotpink"}},
        {"colors": {"--accent": "#ff3ea5; background: red"}},
        {"colors": {"accent": "#ff3ea5"}},
        {"font_ui": "Inter; color: red"},
        {"radius": 99},
        {"preset": "../etc"},
    ]
    for body in bad:
        assert client.put("/api/appearance", json=body).status_code == 422, body
    assert not (config.data_dir() / "appearance.json").exists()


def test_corrupt_file_falls_back_to_default(client):
    (config.data_dir() / "appearance.json").write_text("{not json", encoding="utf-8")
    assert client.get("/api/appearance").json()["preset"] == "mercury-dark"
    (config.data_dir() / "appearance.json").write_text('{"colors": {"--bg": "nope"}}', encoding="utf-8")
    assert client.get("/api/appearance").json()["colors"] == {}


# --------------------------------------------------------------------------- themes (API)

SYSTEM_IDS = ["mercury-dark", "mercury-light", "hot-pink", "midnight", "forest", "ember", "paper"]


def test_lists_system_themes(client):
    themes = client.get("/api/appearance/themes").json()
    assert [t["id"] for t in themes] == SYSTEM_IDS
    assert all(t["system"] for t in themes)
    assert themes[0]["colors"]["--accent"] == "#7c8cff"
    assert all(len(t["colors"]) == 12 for t in themes)


def test_create_update_delete_custom_theme(client):
    body = {"name": "Neon  Grape", "based_on": "midnight", "colors": {"--accent": "#B000FF"}}
    r = client.post("/api/appearance/themes", json=body)
    assert r.status_code == 201, r.text
    theme = r.json()
    assert theme["id"] == "neon-grape" and theme["name"] == "Neon Grape" and theme["system"] is False
    assert theme["colors"]["--accent"] == "#b000ff"
    assert theme["colors"]["--bg"] == "#0a0e17"  # from Midnight
    assert (config.data_dir() / "themes.json").exists()

    r = client.patch("/api/appearance/themes/neon-grape", json={"name": "Grape", "colors": {"--on-accent": "#ffffff"}})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Grape" and r.json()["colors"]["--on-accent"] == "#ffffff"
    r = client.patch("/api/appearance/themes/neon-grape", json={"colors": {"--on-accent": None}})
    assert "--on-accent" not in r.json()["colors"]
    assert client.patch("/api/appearance/themes/neon-grape", json={"colors": {"--bg": None}}).status_code == 422

    client.put("/api/appearance", json={"preset": "neon-grape", "colors": {"--text": "#ffffff"}, "radius": 4})
    assert client.delete("/api/appearance/themes/neon-grape").status_code == 204
    assert [t["id"] for t in client.get("/api/appearance/themes").json()] == SYSTEM_IDS
    # It was in use: back to Mercury Dark, keeping fonts and corners.
    assert client.get("/api/appearance").json() == {
        "preset": "mercury-dark", "colors": {}, "font_ui": None, "font_mono": None, "radius": 4,
    }


def test_system_themes_cannot_be_changed_or_deleted(client):
    r = client.patch("/api/appearance/themes/mercury-dark", json={"name": "Mine"})
    assert r.status_code == 403 and "system theme" in r.json()["detail"]
    assert client.delete("/api/appearance/themes/paper").status_code == 403
    assert client.delete("/api/appearance/themes/nope").status_code == 404
    assert not (config.data_dir() / "themes.json").exists()


def test_theme_rules(client):
    assert client.post("/api/appearance/themes", json={"name": "Mercury dark"}).status_code == 409  # name taken
    assert client.post("/api/appearance/themes", json={"name": "X", "colors": {"--nope": "#000000"}}).status_code == 422
    assert client.post("/api/appearance/themes", json={"name": "X", "based_on": "nope"}).status_code == 404
    a = client.post("/api/appearance/themes", json={"name": "Repap"}).json()
    b = client.post("/api/appearance/themes", json={"name": "Repap!"}).json()
    assert (a["id"], b["id"]) == ("repap", "repap-2")  # different names, same slug


def test_custom_theme_file_cannot_claim_a_system_id(client):
    config.data_dir().mkdir(parents=True, exist_ok=True)
    fake = {"id": "paper", "name": "Evil", "system": False, "colors": {"--bg": "#000000"}}
    mine = {"id": "mine", "name": "Mine", "system": True, "colors": {"--bg": "#000000"}}
    (config.data_dir() / "themes.json").write_text(json.dumps([fake, mine]), encoding="utf-8")
    themes = {t["id"]: t for t in client.get("/api/appearance/themes").json()}
    assert themes["paper"]["name"] == "Paper"
    assert themes["mine"]["system"] is False
    assert client.delete("/api/appearance/themes/mine").status_code == 204


# --------------------------------------------------------------------------- themes (agent tools)


def _call(tool_name, /, **args):
    from mercury.services.mercury_tools import build_appearance_tools

    handler = next(t for t in build_appearance_tools() if t.name == tool_name).handler
    result = asyncio.run(handler(args))
    text = result["content"][0]["text"]
    return (text, True) if result.get("is_error") else (json.loads(text), False)


def test_agent_reads_appearance(client):
    data, err = _call("get_appearance")
    assert not err
    assert data["current"]["theme"] == "mercury-dark" and data["current"]["theme_is_system"] is True
    assert [t["id"] for t in data["themes"]] == SYSTEM_IDS
    assert "--accent" in data["tokens"] and "--on-accent" in data["derived_tokens"]


def test_agent_creates_and_switches_to_a_theme(client):
    client.put("/api/appearance", json={"preset": "forest", "colors": {"--text": "#ffffff"}})
    data, err = _call("create_theme", name="Mossy", colors={"--accent": "#88cc44"})
    assert not err, data
    assert data["current"]["theme"] == "mossy" and data["current"]["color_overrides"] == {}
    mossy = next(t for t in data["themes"] if t["id"] == "mossy")
    # Starts from what the user sees (Forest plus their tweak), then the agent's colors.
    assert mossy["colors"]["--bg"] == "#0d1310" and mossy["colors"]["--text"] == "#ffffff"
    assert mossy["colors"]["--accent"] == "#88cc44"
    assert client.get("/api/appearance").json()["preset"] == "mossy"

    data, err = _call("create_theme", name="Later", based_on="paper", apply=False)
    assert not err and data["current"]["theme"] == "mossy"


def test_agent_updates_and_deletes_only_custom_themes(client):
    _call("create_theme", name="Mine", apply=False)
    data, err = _call("update_theme", theme_id="mine", colors={"--accent": "#123456"})
    assert not err and next(t for t in data["themes"] if t["id"] == "mine")["colors"]["--accent"] == "#123456"

    for name, args in (("update_theme", {"theme_id": "hot-pink", "name": "x"}), ("delete_theme", {"theme_id": "mercury-dark"})):
        text, err = _call(name, **args)
        assert err and "system theme" in text and "Nothing was changed" in text
    assert client.get("/api/appearance/themes").json()[2]["name"] == "Hot Pink"

    data, err = _call("delete_theme", theme_id="mine")
    assert not err and "mine" not in [t["id"] for t in data["themes"]]


def test_agent_sets_appearance(client):
    data, err = _call("set_appearance", theme="paper", font_ui="Inter, sans-serif", radius=12)
    assert not err
    data, err = _call("set_appearance", colors={"--accent": "#ff0000"})
    assert not err
    assert client.get("/api/appearance").json() == {
        "preset": "paper", "colors": {"--accent": "#ff0000"}, "font_ui": "Inter, sans-serif", "font_mono": None, "radius": 12,
    }
    assert data["current"]["colors"]["--accent"] == "#ff0000"
    data, err = _call("set_appearance", colors={"--accent": None}, radius=None)
    assert not err and data["current"]["color_overrides"] == {} and data["current"]["radius"] is None

    for args in ({"theme": "nope"}, {"radius": 99}, {"colors": {"--accent": "red"}}, {"font_ui": "x; y"}, {"bogus": 1}, {}):
        text, err = _call("set_appearance", **args)
        assert err, args
    assert client.get("/api/appearance").json()["preset"] == "paper"


def test_agent_access_applies_to_appearance(client):
    client.patch("/api/preferences", json={"agent_settings_access": "read"})
    assert not _call("get_appearance")[1]
    for name, args in (("create_theme", {"name": "x"}), ("set_appearance", {"theme": "paper"})):
        text, err = _call(name, **args)
        assert err and "Agent access" in text
    assert not (config.data_dir() / "themes.json").exists()
    assert client.get("/api/appearance").json()["preset"] == "mercury-dark"

    client.patch("/api/preferences", json={"agent_settings_access": "off"})
    text, err = _call("get_appearance")
    assert err and "turned off" in text
