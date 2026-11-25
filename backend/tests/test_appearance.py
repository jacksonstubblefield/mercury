from __future__ import annotations

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
