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


# --------------------------------------------------------------------------- task pictures

GIF = b"GIF89a" + b"\x00" * 32


def _files():
    return sorted(p.name for p in attachments.attachments_dir().iterdir())


def test_task_pictures_add_reorder_remove(client):
    p = client.post("/api/projects", json={"name": "Demo"}).json()
    r = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "Fix header", "add_images": [b64(PNG)]})
    assert r.status_code == 201, r.text
    task = r.json()
    [first] = task["images"]
    assert "add_images" not in task

    # Plain edits leave the pictures alone.
    task = client.patch(f"/api/tasks/{task['id']}", json={"title": "Fix the header"}).json()
    assert task["images"] == [first]

    task = client.patch(f"/api/tasks/{task['id']}", json={"add_images": [b64(JPEG), b64(GIF)]}).json()
    assert len(task["images"]) == 3 and task["images"][0] == first
    _, second, third = task["images"]

    # Keep two, reordered; the dropped one's file goes.
    task = client.patch(f"/api/tasks/{task['id']}", json={"images": [third, first]}).json()
    assert task["images"] == [third, first]
    assert attachments.path_for(second) is None
    assert _files() == sorted([first, third])

    # Names that aren't this task's are refused.
    bad = client.patch(f"/api/tasks/{task['id']}", json={"images": [third, "0" * 32 + ".png"]})
    assert bad.status_code == 400
    assert client.get(f"/api/tasks/{task['id']}").json()["images"] == [third, first]

    assert client.delete(f"/api/tasks/{task['id']}").status_code == 204
    assert _files() == []


def test_task_picture_limits(client):
    p = client.post("/api/projects", json={"name": "Demo"}).json()
    url = f"/api/projects/{p['id']}/tasks"
    assert client.post(url, json={"title": "x", "add_images": [b64(b"<svg/>")]}).status_code == 400
    assert client.post(url, json={"title": "x", "add_images": [b64(PNG)] * (attachments.MAX_IMAGES + 1)}).status_code == 400
    task = client.post(url, json={"title": "x", "add_images": [b64(PNG)] * attachments.MAX_IMAGES}).json()
    r = client.patch(f"/api/tasks/{task['id']}", json={"add_images": [b64(PNG)]})
    assert r.status_code == 400 and "one task" in r.json()["detail"]
    # Making room first works in the same request.
    r = client.patch(f"/api/tasks/{task['id']}", json={"images": task["images"][1:], "add_images": [b64(JPEG)]})
    assert r.status_code == 200 and len(r.json()["images"]) == attachments.MAX_IMAGES
    assert len(_files()) == attachments.MAX_IMAGES
    assert client.get(f"/api/projects/{p['id']}/tasks").json()[0]["images"] == r.json()["images"]


def test_run_on_a_task_gets_copies_of_its_pictures(make_client, tmp_path):
    client = make_client(lambda opts: ImageClient(opts))
    p, t = _setup(client, tmp_path)
    t = client.patch(f"/api/tasks/{t['id']}", json={"add_images": [b64(PNG)]}).json()

    r = client.post(
        f"/api/projects/{p['id']}/runs", json={"prompt": "Complete it", "task_id": t["id"], "images": [b64(JPEG)]}
    )
    assert r.status_code == 201, r.text
    run = _wait_for(client, r.json()["id"], {"succeeded"})
    assert len(run["images"]) == 2 and not set(run["images"]) & set(t["images"])  # its own copies

    [message] = FakeClient.instances[-1].prompt
    content = message["message"]["content"]
    assert [b["type"] for b in content] == ["image", "image", "text"]
    assert content[0]["source"]["data"] == b64(PNG)  # the task's picture first, then the pasted one
    assert content[1]["source"]["data"] == b64(JPEG)
    assert "1 picture attached" in FakeClient.instances[-1].options.system_prompt["append"]

    # Removing the picture from the task leaves the session's copy.
    client.patch(f"/api/tasks/{t['id']}", json={"images": []})
    assert attachments.path_for(run["images"][0]) is not None

    # Follow-ups don't resend the task's pictures.
    client.patch(f"/api/tasks/{t['id']}", json={"add_images": [b64(GIF)]})
    f = client.post(f"/api/runs/{run['id']}/follow-up", json={"prompt": "more"}).json()
    f = _wait_for(client, f["id"], {"succeeded"})
    assert f["images"] == [] and FakeClient.instances[-1].prompt == "more"


def test_agents_can_view_task_pictures(make_client, tmp_path):
    import asyncio

    from mercury.services.mercury_tools import build_task_tools
    from tests.test_agent_runs import _call

    client = make_client(lambda opts: ImageClient(opts))
    p, t = _setup(client, tmp_path)
    client.patch(f"/api/tasks/{t['id']}", json={"add_images": [b64(PNG), b64(JPEG)]})
    tools = build_task_tools(p["id"])
    ref = f"{p['key']}-{t['number']}"

    [listed], _ = _call(tools, "list_tasks")
    assert listed["pictures"] == 2

    handler = next(x for x in tools if x.name == "view_task_images").handler
    result = asyncio.run(handler({"task_id": ref}))
    assert not result.get("is_error")
    assert [b["type"] for b in result["content"]] == ["text", "image", "image"]
    assert result["content"][1] == {"type": "image", "data": b64(PNG), "mimeType": "image/png"}

    other = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "No pictures"}).json()
    empty, err = _call(tools, "view_task_images", task_id=other["number"])
    assert not err and empty["pictures"] == 0
    assert "pictures" not in _call(tools, "list_tasks")[0][1]


def test_merging_tasks_keeps_their_pictures(make_client):
    from mercury.services.mercury_tools import build_organizer_task_tools
    from tests.test_agent_runs import _call

    client = make_client(lambda opts: FakeClient(opts))
    p = client.post("/api/projects", json={"name": "Demo"}).json()
    url = f"/api/projects/{p['id']}/tasks"
    keep = client.post(url, json={"title": "a", "add_images": [b64(PNG)]}).json()
    dup = client.post(url, json={"title": "b", "add_images": [b64(JPEG)]}).json()
    _, err = _call(build_organizer_task_tools(p["id"]), "merge_tasks", keep_id=keep["number"], merge_ids=[dup["number"]])
    assert not err
    assert client.get(f"/api/tasks/{keep['id']}").json()["images"] == keep["images"] + dup["images"]


def test_deleting_a_project_deletes_its_task_pictures(client):
    p = client.post("/api/projects", json={"name": "Demo"}).json()
    client.post(f"/api/projects/{p['id']}/tasks", json={"title": "a", "add_images": [b64(PNG)]})
    assert len(_files()) == 1
    client.delete(f"/api/projects/{p['id']}")
    assert _files() == []
