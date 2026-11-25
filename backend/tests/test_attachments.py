from __future__ import annotations

import base64

from mercury.services import attachments
from tests.test_agent_runs import FakeClient, _setup, _wait_for

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


class ImageClient(FakeClient):
    """FakeClient that reads a streamed prompt the way the real client does."""

    async def query(self, prompt):
        self.prompt = prompt if isinstance(prompt, str) else [m async for m in prompt]


def test_run_sends_pasted_images_to_claude(make_client, tmp_path):
    client = make_client(lambda opts: ImageClient(opts))
    p, _ = _setup(client, tmp_path)

    r = client.post(f"/api/projects/{p['id']}/runs", json={"prompt": "what's wrong here?", "images": [b64(PNG), b64(JPEG)]})
    assert r.status_code == 201, r.text
    run = _wait_for(client, r.json()["id"], {"succeeded"})

    names = run["images"]
    assert [n.rsplit(".", 1)[1] for n in names] == ["png", "jpg"]

    [message] = FakeClient.instances[-1].prompt
    assert message["type"] == "user"
    content = message["message"]["content"]
    assert [b["type"] for b in content] == ["image", "image", "text"]
    assert content[0]["source"] == {"type": "base64", "media_type": "image/png", "data": b64(PNG)}
    assert content[1]["source"]["media_type"] == "image/jpeg"
    assert content[2] == {"type": "text", "text": "what's wrong here?"}

    # The UI loads them back for the transcript.
    img = client.get(f"/api/attachments/{names[0]}")
    assert img.status_code == 200
    assert img.headers["content-type"] == "image/png"
    assert img.content == PNG


def test_plain_prompt_is_still_a_string(make_client, tmp_path):
    client = make_client(lambda opts: ImageClient(opts))
    p, _ = _setup(client, tmp_path)
    run = client.post(f"/api/projects/{p['id']}/runs", json={"prompt": "hi"}).json()
    _wait_for(client, run["id"], {"succeeded"})
    assert run["images"] == []
    assert FakeClient.instances[-1].prompt == "hi"


def test_image_only_prompt(make_client, tmp_path):
    client = make_client(lambda opts: ImageClient(opts))
    p, _ = _setup(client, tmp_path)
    r = client.post(f"/api/projects/{p['id']}/runs", json={"prompt": "  ", "images": [b64(PNG)]})
    assert r.status_code == 201, r.text
    _wait_for(client, r.json()["id"], {"succeeded"})
    [message] = FakeClient.instances[-1].prompt
    assert [b["type"] for b in message["message"]["content"]] == ["image"]  # no empty text block


def test_follow_up_with_images(make_client, tmp_path):
    client = make_client(lambda opts: ImageClient(opts))
    p, _ = _setup(client, tmp_path)
    first = client.post(f"/api/projects/{p['id']}/runs", json={"prompt": "one"}).json()
    _wait_for(client, first["id"], {"succeeded"})

    r = client.post(f"/api/runs/{first['id']}/follow-up", json={"prompt": "", "images": [b64(PNG)]})
    assert r.status_code == 201, r.text
    second = _wait_for(client, r.json()["id"], {"succeeded"})
    assert len(second["images"]) == 1
    assert FakeClient.instances[-1].options.resume == "sess-1"
    [message] = FakeClient.instances[-1].prompt
    assert message["message"]["content"][0]["type"] == "image"

    empty = client.post(f"/api/runs/{first['id']}/follow-up", json={"prompt": " "})
    assert empty.status_code == 400


def test_rejects_bad_images_and_stores_nothing(make_client, tmp_path):
    client = make_client(lambda opts: ImageClient(opts))
    p, _ = _setup(client, tmp_path)
    url = f"/api/projects/{p['id']}/runs"

    cases = [
        [b64(PNG), b64(b"<svg onload=alert(1)>")],  # not a raster image Claude takes
        ["not base64!!"],
        [b64(PNG)] * (attachments.MAX_IMAGES + 1),
        [b64(PNG + b"\x00" * attachments.MAX_IMAGE_BYTES)],
    ]
    for images in cases:
        r = client.post(url, json={"prompt": "look", "images": images})
        assert r.status_code == 400, r.text
    assert list(attachments.attachments_dir().iterdir()) == []
    assert client.get(f"/api/projects/{p['id']}/runs").json() == []


def test_attachment_names_are_checked(client):
    for name in ["..%2Fmercury.db", "abc.png", "0" * 32 + ".exe", "0" * 32 + ".png"]:
        assert client.get(f"/api/attachments/{name}").status_code == 404


def test_missing_attachment_fails_the_run(make_client, tmp_path, monkeypatch):
    client = make_client(lambda opts: ImageClient(opts))
    p, _ = _setup(client, tmp_path)
    original = attachments.save_images

    def save_then_lose(images):  # the file vanishes between saving and the run starting
        names = original(images)
        attachments.delete(names)
        return names

    monkeypatch.setattr(attachments, "save_images", save_then_lose)
    r = client.post(f"/api/projects/{p['id']}/runs", json={"prompt": "look", "images": [b64(PNG)]})
    run = _wait_for(client, r.json()["id"], {"failed"})
    assert "missing" in run["error"]


def test_deleting_a_project_deletes_its_images(make_client, tmp_path):
    client = make_client(lambda opts: ImageClient(opts))
    p, _ = _setup(client, tmp_path)
    run = client.post(f"/api/projects/{p['id']}/runs", json={"prompt": "look", "images": [b64(PNG)]}).json()
    _wait_for(client, run["id"], {"succeeded"})
    assert attachments.path_for(run["images"][0]) is not None

    assert client.delete(f"/api/projects/{p['id']}").status_code in (200, 204)
    assert attachments.path_for(run["images"][0]) is None
