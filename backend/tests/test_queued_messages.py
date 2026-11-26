"""Messages sent while a run is working, with a fake that behaves like the CLI (no network, no cost)."""

from __future__ import annotations

import asyncio
import base64
import time

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolPermissionContext,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from test_agent_runs import FakeClient, _setup, _wait_for

from mercury.services import agent_runner, attachments

WITHDRAW = {"subtype": "interrupt", "cancel_queued": True}


class QueueClient(FakeClient):
    """Holds its first turn at a tool approval, so a test can queue messages. `takes` is what happens next:

    mid_turn: reads them after the tool call (echoing each), within the same turn
    listed:   the same, but only the result names them
    next:     finishes the turn first, then starts another one for them
    never:    finishes the turn and never starts on them
    error:    the turn fails before reading them
    """

    def __init__(self, options, takes: str = "mid_turn"):
        super().__init__(options)
        self.takes = takes
        self.queued: list[dict] = []
        self.controls: list[dict] = []
        self.turn = 0
        self._query = self  # where the SDK client keeps its control channel

    async def query(self, prompt):
        if self.prompt is None:
            self.prompt = prompt
        else:
            self.queued += [message async for message in prompt]

    async def _send_control_request(self, request):
        self.controls.append(request)

    def _echoes(self):
        return [UserMessage(content=m["message"]["content"], uuid=m["uuid"]) for m in self.queued]

    def _result(self, total: float, text: str, read: list[str], is_error: bool = False):
        return ResultMessage(
            subtype="error_during_execution" if is_error else "success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=is_error,
            num_turns=2,
            session_id="sess-1",
            total_cost_usd=total,
            result=text,
            user_message_uuids=read or None,
        )

    async def receive_response(self):
        self.turn += 1
        if self.turn > 1 and self.takes == "never":
            await asyncio.sleep(60)
        yield SystemMessage(subtype="init", data={"session_id": "sess-1", "model": "claude-opus-5-5"})
        if self.turn == 1:
            tool = ToolUseBlock(id="tu1", name="Bash", input={"command": "ls"})
            yield AssistantMessage(content=[tool], model="claude-opus-5-5")
            await self.options.can_use_tool("Bash", {"command": "ls"}, ToolPermissionContext(tool_use_id="tu1"))
            yield UserMessage(content=[ToolResultBlock(tool_use_id="tu1", content="README.md")])
            if self.takes == "mid_turn":
                for echo in self._echoes():
                    yield echo
            read = [m["uuid"] for m in self.queued] if self.takes in ("mid_turn", "listed") else []
            yield self._result(0.01, "First answer.", read, is_error=self.takes == "error")
        else:
            for echo in self._echoes():
                yield echo
            yield AssistantMessage(content=[TextBlock(text="On it.")], model="claude-opus-5-5")
            yield self._result(0.025, "Second answer.", [m["uuid"] for m in self.queued])


def _start(make_client, tmp_path, takes="mid_turn"):
    """A run that's waiting for approval, ready to be sent messages."""
    client = make_client(lambda opts: QueueClient(opts, takes))
    p, t = _setup(client, tmp_path)
    body = {"prompt": "go", "permission_mode": "default", "task_id": t["id"]}
    run_id = client.post(f"/api/projects/{p['id']}/runs", json=body).json()["id"]
    _wait_for(client, run_id, {"waiting_approval"})
    return client, p, run_id


def _send(client, run_id, prompt, images=()):
    r = client.post(f"/api/runs/{run_id}/messages", json={"prompt": prompt, "images": list(images)})
    assert r.status_code == 202, r.text
    return r.json()["message_id"]


def _events(client, run_id):
    return client.get(f"/api/runs/{run_id}/events").json()


def _approve(client, run_id):
    request = next(e for e in _events(client, run_id) if e["kind"] == "approval_request")
    r = client.post(f"/api/runs/{run_id}/approvals/{request['payload']['approval_id']}", json={"allow": True})
    assert r.status_code == 200


def _ids(events, kind):
    return [e["payload"]["message_id"] for e in events if e["kind"] == kind]


def test_queued_messages_are_read_during_the_turn(make_client, tmp_path):
    client, _, run_id = _start(make_client, tmp_path)
    first, second = _send(client, run_id, "also this"), _send(client, run_id, "and that")

    # Handed to the CLI straight away, in order, as the user's own messages.
    fake = FakeClient.instances[-1]
    assert [(m["uuid"], m["message"]["content"]) for m in fake.queued] == [(first, "also this"), (second, "and that")]
    assert all(m["origin"] == {"kind": "human"} for m in fake.queued)
    assert "replay-user-messages" in fake.options.extra_args
    assert fake.options.max_buffer_size == agent_runner.MAX_CLI_LINE_BYTES
    assert _ids(_events(client, run_id), "user_message") == []  # not read yet

    _approve(client, run_id)
    run = _wait_for(client, run_id, {"succeeded"})
    events = _events(client, run_id)
    kinds = [e["kind"] for e in events]
    assert kinds[kinds.index("tool_result") :] == ["tool_result", "user_message", "user_message", "result"]
    assert _ids(events, "queued_message") == _ids(events, "user_message") == [first, second]
    assert run["num_turns"] == 2 and run["cost_usd"] == 0.01
    assert fake.turn == 1 and fake.controls == []


def test_a_message_only_named_by_the_result_counts_as_read(make_client, tmp_path):
    client, _, run_id = _start(make_client, tmp_path, takes="listed")
    message = _send(client, run_id, "also this")
    _approve(client, run_id)
    _wait_for(client, run_id, {"succeeded"})
    assert _ids(_events(client, run_id), "user_message") == [message]


def test_a_message_read_after_the_turn_gets_another_turn_in_the_same_run(make_client, tmp_path):
    client, p, run_id = _start(make_client, tmp_path, takes="next")
    message = _send(client, run_id, "one more thing")
    _approve(client, run_id)
    run = _wait_for(client, run_id, {"succeeded"})

    events = _events(client, run_id)
    kinds = [e["kind"] for e in events]
    assert kinds[kinds.index("result") :] == ["result", "user_message", "text", "result"]
    assert kinds.count("system") == 1  # the second turn doesn't announce the session again
    assert _ids(events, "user_message") == [message]
    costs = [e["payload"]["cost_usd"] for e in events if e["kind"] == "result"]
    assert [round(c, 6) for c in costs] == [0.01, 0.015]
    assert round(run["cost_usd"], 6) == 0.025 and run["session_cost_usd"] == 0.025
    assert run["num_turns"] == 4 and run["result"] == "Second answer."
    assert len(client.get(f"/api/projects/{p['id']}/runs").json()) == 1


def test_queued_message_the_cli_never_starts_on_is_left_unread(make_client, tmp_path, monkeypatch):
    monkeypatch.setattr(agent_runner, "NEXT_TURN_TIMEOUT", 0.1)
    client, _, run_id = _start(make_client, tmp_path, takes="never")
    _send(client, run_id, "hello?")
    _approve(client, run_id)
    run = _wait_for(client, run_id, {"succeeded"})
    assert run["result"] == "First answer."
    assert _ids(_events(client, run_id), "user_message") == []
    assert FakeClient.instances[-1].controls == [WITHDRAW]


def test_failed_turn_withdraws_queued_messages(make_client, tmp_path):
    client, _, run_id = _start(make_client, tmp_path, takes="error")
    _send(client, run_id, "too late")
    _approve(client, run_id)
    _wait_for(client, run_id, {"failed"})
    assert _ids(_events(client, run_id), "user_message") == []
    assert FakeClient.instances[-1].controls == [WITHDRAW]


def test_stop_withdraws_queued_messages(make_client, tmp_path):
    client, _, run_id = _start(make_client, tmp_path)
    _send(client, run_id, "never mind")
    assert client.post(f"/api/runs/{run_id}/cancel").status_code == 200
    _wait_for(client, run_id, {"cancelled"})
    assert _ids(_events(client, run_id), "user_message") == []
    assert FakeClient.instances[-1].controls == [WITHDRAW]


def test_message_sent_while_the_run_is_starting_follows_its_prompt(make_client, tmp_path):
    ready = []

    class SlowStart(QueueClient):
        async def __aenter__(self):
            while not ready:
                await asyncio.sleep(0.01)
            return self

    client = make_client(lambda opts: SlowStart(opts))
    p, _ = _setup(client, tmp_path)
    body = {"prompt": "go", "permission_mode": "default"}
    run_id = client.post(f"/api/projects/{p['id']}/runs", json=body).json()["id"]
    message = _send(client, run_id, "early")
    assert all(not fake.queued for fake in FakeClient.instances if isinstance(fake, SlowStart))
    ready.append(True)

    _wait_for(client, run_id, {"waiting_approval"})
    fake = FakeClient.instances[-1]
    deadline = time.time() + 5
    while not fake.queued and time.time() < deadline:
        time.sleep(0.01)
    assert fake.prompt == "go" and [m["uuid"] for m in fake.queued] == [message]
    _approve(client, run_id)
    _wait_for(client, run_id, {"succeeded"})
    assert _ids(_events(client, run_id), "user_message") == [message]


def test_queued_pictures_are_sent_and_deleted_with_the_project(make_client, tmp_path):
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * 20).decode()
    client, p, run_id = _start(make_client, tmp_path)
    _send(client, run_id, "", [png])
    content = FakeClient.instances[-1].queued[0]["message"]["content"]
    assert [block["type"] for block in content] == ["image"]
    name = next(e for e in _events(client, run_id) if e["kind"] == "queued_message")["payload"]["images"][0]
    assert attachments.path_for(name)

    _approve(client, run_id)
    _wait_for(client, run_id, {"succeeded"})
    assert client.delete(f"/api/projects/{p['id']}").status_code == 204
    assert attachments.path_for(name) is None


def test_messages_are_only_taken_by_working_runs(make_client, tmp_path):
    client, p, run_id = _start(make_client, tmp_path)
    url = f"/api/runs/{run_id}/messages"
    assert client.post(url, json={"prompt": "  "}).status_code == 400
    assert client.post(url, json={"prompt": "x", "images": ["bm90IGFuIGltYWdl"]}).status_code == 400
    assert client.post("/api/runs/999/messages", json={"prompt": "x"}).status_code == 404
    _approve(client, run_id)
    _wait_for(client, run_id, {"succeeded"})
    assert client.post(url, json={"prompt": "x"}).status_code == 409

    organizer = client.post(f"/api/projects/{p['id']}/organize", json={"target": "board"}).json()
    _wait_for(client, organizer["id"], {"waiting_approval", "succeeded"})
    assert client.post(f"/api/runs/{organizer['id']}/messages", json={"prompt": "x"}).status_code == 409
    assert not FakeClient.instances[-1].options.extra_args
    client.post(f"/api/runs/{organizer['id']}/cancel")
