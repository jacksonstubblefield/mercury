"""Agent run lifecycle, using a fake Agent SDK client (no network, no cost)."""

from __future__ import annotations

import time

from claude_agent_sdk import (
    AssistantMessage,
    PermissionResultAllow,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolPermissionContext,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)


class FakeClient:
    """Mimics ClaudeSDKClient: replays a scripted conversation."""

    instances: list["FakeClient"] = []

    def __init__(self, options, ask_permission: bool = False):
        self.options = options
        self.ask_permission = ask_permission
        self.prompt = None
        FakeClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def query(self, prompt):
        self.prompt = prompt

    async def interrupt(self):
        pass

    async def receive_response(self):
        yield SystemMessage(subtype="init", data={"session_id": "sess-1", "model": "claude-opus-5-5"})
        tool = ToolUseBlock(id="tu1", name="Bash", input={"command": "ls"})
        yield AssistantMessage(content=[TextBlock(text="Looking around."), tool], model="claude-opus-5-5")
        if self.ask_permission:
            decision = await self.options.can_use_tool("Bash", {"command": "ls"}, ToolPermissionContext(tool_use_id="tu1"))
            content = "README.md" if isinstance(decision, PermissionResultAllow) else decision.message
            yield UserMessage(content=[ToolResultBlock(tool_use_id="tu1", content=content, is_error=False)])
        yield ResultMessage(
            subtype="success",
            duration_ms=10,
            duration_api_ms=8,
            is_error=False,
            num_turns=2,
            session_id="sess-1",
            total_cost_usd=0.0123,
            result="Done: listed files.",
        )


def _wait_for(client, run_id, statuses, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] in statuses:
            return run
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} never reached {statuses}; last={run}")


def _setup(client, tmp_path):
    p = client.post("/api/projects", json={"name": "Demo", "repo_path": str(tmp_path)}).json()
    t = client.post(f"/api/projects/{p['id']}/tasks", json={"title": "List files", "description": "use ls"}).json()
    return p, t


def test_run_succeeds_and_records_events(make_client, tmp_path):
    client = make_client(lambda opts: FakeClient(opts))
    p, t = _setup(client, tmp_path)

    r = client.post(f"/api/projects/{p['id']}/runs", json={"prompt": "list files", "task_id": t["id"]})
    assert r.status_code == 201, r.text
    run = _wait_for(client, r.json()["id"], {"succeeded"})

    assert run["session_id"] == "sess-1"
    assert run["cost_usd"] == 0.0123
    assert run["result"] == "Done: listed files."

    kinds = [e["kind"] for e in client.get(f"/api/runs/{run['id']}/events").json()]
    assert kinds == ["system", "text", "tool_use", "result"]

    # Task moved todo -> in_progress -> review
    assert client.get(f"/api/tasks/{t['id']}").json()["status"] == "review"

    opts = FakeClient.instances[-1].options
    assert opts.cwd == str(tmp_path)
    assert "List files" in opts.system_prompt["append"]


def test_approval_flow(make_client, tmp_path):
    client = make_client(lambda opts: FakeClient(opts, ask_permission=True))
    p, _ = _setup(client, tmp_path)
    run_id = client.post(f"/api/projects/{p['id']}/runs", json={"prompt": "go", "permission_mode": "default"}).json()["id"]

    _wait_for(client, run_id, {"waiting_approval"})
    request = next(e for e in client.get(f"/api/runs/{run_id}/events").json() if e["kind"] == "approval_request")
    assert request["payload"]["tool_name"] == "Bash"

    r = client.post(f"/api/runs/{run_id}/approvals/{request['payload']['approval_id']}", json={"allow": True})
    assert r.status_code == 200
    _wait_for(client, run_id, {"succeeded"})

    events = client.get(f"/api/runs/{run_id}/events").json()
    result = next(e for e in events if e["kind"] == "tool_result")
    assert result["payload"]["content"] == "README.md"


def test_follow_up_resumes_session(make_client, tmp_path):
    client = make_client(lambda opts: FakeClient(opts))
    p, _ = _setup(client, tmp_path)
    first = client.post(f"/api/projects/{p['id']}/runs", json={"prompt": "one"}).json()
    _wait_for(client, first["id"], {"succeeded"})

    second = client.post(f"/api/runs/{first['id']}/follow-up", json={"prompt": "two"}).json()
    _wait_for(client, second["id"], {"succeeded"})
    assert second["parent_run_id"] == first["id"]
    assert FakeClient.instances[-1].options.resume == "sess-1"


def test_run_requires_repo_path(client):
    p = client.post("/api/projects", json={"name": "No repo"}).json()
    r = client.post(f"/api/projects/{p['id']}/runs", json={"prompt": "hi"})
    assert r.status_code == 400


def test_stream_websocket_replays_history(make_client, tmp_path):
    client = make_client(lambda opts: FakeClient(opts))
    p, _ = _setup(client, tmp_path)
    run_id = client.post(f"/api/projects/{p['id']}/runs", json={"prompt": "x"}).json()["id"]
    _wait_for(client, run_id, {"succeeded"})

    with client.websocket_connect(f"/api/runs/{run_id}/stream") as ws:
        first = ws.receive_json()
        assert first["type"] == "run"
        kinds = [ws.receive_json()["kind"] for _ in range(4)]
    assert kinds == ["system", "text", "tool_use", "result"]


def test_switching_to_full_auto_resolves_pending_approval(make_client, tmp_path):
    fakes: list[FakeClient] = []

    def factory(opts):
        fake = FakeClient(opts, ask_permission=True)
        fake.modes = []

        async def set_permission_mode(mode):
            fake.modes.append(mode)

        fake.set_permission_mode = set_permission_mode
        fakes.append(fake)
        return fake

    client = make_client(factory)
    p, _ = _setup(client, tmp_path)
    run_id = client.post(f"/api/projects/{p['id']}/runs", json={"prompt": "go", "permission_mode": "acceptEdits"}).json()["id"]
    _wait_for(client, run_id, {"waiting_approval"})

    r = client.post(f"/api/runs/{run_id}/permission-mode", json={"permission_mode": "bypassPermissions"})
    assert r.status_code == 200
    run = _wait_for(client, run_id, {"succeeded"})
    assert run["permission_mode"] == "bypassPermissions"
    assert fakes[0].modes == ["bypassPermissions"]

    events = client.get(f"/api/runs/{run_id}/events").json()
    assert next(e for e in events if e["kind"] == "tool_result")["payload"]["content"] == "README.md"
    assert any(e["kind"] == "status" and "Full auto" in e["payload"]["message"] for e in events)

    # Finished runs can't be switched; the follow-up carries the mode instead.
    assert client.post(f"/api/runs/{run_id}/permission-mode", json={"permission_mode": "plan"}).status_code == 409


def test_full_auto_never_asks(make_client, tmp_path):
    client = make_client(lambda opts: FakeClient(opts, ask_permission=True))
    p, _ = _setup(client, tmp_path)
    first = client.post(f"/api/projects/{p['id']}/runs", json={"prompt": "go", "permission_mode": "bypassPermissions"}).json()
    _wait_for(client, first["id"], {"succeeded"})

    # A follow-up can raise the mode even if the parent asked for approvals.
    follow = client.post(f"/api/runs/{first['id']}/follow-up", json={"prompt": "more", "permission_mode": "bypassPermissions"}).json()
    _wait_for(client, follow["id"], {"succeeded"})
    for run_id in (first["id"], follow["id"]):
        kinds = [e["kind"] for e in client.get(f"/api/runs/{run_id}/events").json()]
        assert "approval_request" not in kinds


def test_task_runs_include_whole_sessions(make_client, tmp_path):
    """The task modal lists a task's sessions from ?task_id=, so follow-ups must carry the task too."""
    client = make_client(lambda opts: FakeClient(opts))
    p, t = _setup(client, tmp_path)
    first = client.post(f"/api/projects/{p['id']}/runs", json={"prompt": "go", "task_id": t["id"]}).json()
    _wait_for(client, first["id"], {"succeeded"})
    follow = client.post(f"/api/runs/{first['id']}/follow-up", json={"prompt": "more"}).json()
    _wait_for(client, follow["id"], {"succeeded"})
    unrelated = client.post(f"/api/projects/{p['id']}/runs", json={"prompt": "other"}).json()
    _wait_for(client, unrelated["id"], {"succeeded"})

    runs = client.get(f"/api/projects/{p['id']}/runs", params={"task_id": t["id"]}).json()
    assert [r["id"] for r in runs] == [follow["id"], first["id"]]
    assert runs[0]["parent_run_id"] == first["id"]
    assert all(r["result"] == "Done: listed files." for r in runs)  # the summary the modal shows


# --------------------------------------------------------------------------- task tools


def _call(tools, name, **args):
    import asyncio
    import json

    handler = next(t for t in tools if t.name == name).handler
    result = asyncio.run(handler(args))
    text = result["content"][0]["text"]
    return (text, True) if result.get("is_error") else (json.loads(text), False)


def test_runs_get_mercury_task_tools(make_client, tmp_path):
    client = make_client(lambda opts: FakeClient(opts))
    p, t = _setup(client, tmp_path)
    run = client.post(f"/api/projects/{p['id']}/runs", json={"prompt": "go", "task_id": t["id"], "permission_mode": "plan"}).json()
    _wait_for(client, run["id"], {"succeeded"})

    opts = FakeClient.instances[-1].options
    assert "mercury" in opts.mcp_servers
    assert set(opts.allowed_tools) == {
        "mcp__mercury__list_tasks",
        "mcp__mercury__create_task",
        "mcp__mercury__update_task",
        "mcp__mercury__search_docs",
        "mcp__mercury__list_docs",
        "mcp__mercury__read_doc",
        "mcp__mercury__create_doc",
        "mcp__mercury__update_doc",
        "mcp__mercury__browse_docs",
        "mcp__mercury__move_docs",
        "mcp__mercury__create_collection",
        "mcp__mercury__update_collection",
        "mcp__mercury__delete_collection",
        "mcp__mercury__get_settings",
        "mcp__mercury__get_appearance",
    }  # delete_doc, update_settings and the appearance changes are left to the approval rules
    assert "mcp__mercury__get_settings" in opts.system_prompt["append"]
    assert "get_appearance" in opts.system_prompt["append"]
    assert "mcp__mercury__list_tasks" in opts.system_prompt["append"]
    assert "mcp__mercury__browse_docs" in opts.system_prompt["append"]
    assert "PreToolUse" in opts.hooks  # Claude Code memory stays read-only


def test_delete_doc_needs_approval_unless_full_auto():
    from mercury.models import PermissionMode
    from mercury.services.agent_runner import RunManager

    allowed = RunManager._auto_allowed
    assert allowed(PermissionMode.default, "mcp__mercury__update_doc")
    for mode in (PermissionMode.default, PermissionMode.acceptEdits, PermissionMode.plan):
        assert not allowed(mode, "mcp__mercury__delete_doc")
    assert allowed(PermissionMode.bypassPermissions, "mcp__mercury__delete_doc")


def test_update_settings_needs_approval_unless_full_auto():
    from mercury.models import PermissionMode
    from mercury.services.agent_runner import RunManager

    allowed = RunManager._auto_allowed
    assert allowed(PermissionMode.plan, "mcp__mercury__get_settings")
    for mode in (PermissionMode.default, PermissionMode.acceptEdits, PermissionMode.plan):
        assert not allowed(mode, "mcp__mercury__update_settings")
    assert allowed(PermissionMode.bypassPermissions, "mcp__mercury__update_settings")


def test_appearance_changes_need_approval_unless_full_auto():
    from mercury.models import PermissionMode
    from mercury.services.agent_runner import RunManager

    allowed = RunManager._auto_allowed
    assert allowed(PermissionMode.plan, "mcp__mercury__get_appearance")
    for name in ("create_theme", "update_theme", "delete_theme", "set_appearance"):
        for mode in (PermissionMode.default, PermissionMode.acceptEdits, PermissionMode.plan):
            assert not allowed(mode, f"mcp__mercury__{name}")
        assert allowed(PermissionMode.bypassPermissions, f"mcp__mercury__{name}")


def test_appearance_precheck_refuses_system_themes_before_asking(client):
    from mercury.services.mercury_tools import precheck

    problem = precheck("mcp__mercury__delete_theme", {"theme_id": "mercury-dark"})
    assert problem and "system theme" in problem
    problem = precheck("mcp__mercury__update_theme", {"theme_id": "paper", "name": "Mine"})
    assert problem and "system theme" in problem
    assert precheck("mcp__mercury__set_appearance", {"theme": "nope"})
    assert precheck("mcp__mercury__set_appearance", {"theme": "paper"}) is None
    client.patch("/api/preferences", json={"agent_settings_access": "read"})
    problem = precheck("mcp__mercury__create_theme", {"name": "Mine"})
    assert problem and "Agent access" in problem


class SettingsClient(FakeClient):
    """Asks to change Mercury's settings instead of running Bash."""

    changes: dict = {}

    async def receive_response(self):
        yield SystemMessage(subtype="init", data={"session_id": "sess-1", "model": "claude-opus-5-5"})
        name, args = "mcp__mercury__update_settings", {"changes": self.changes}
        yield AssistantMessage(content=[ToolUseBlock(id="tu1", name=name, input=args)], model="claude-opus-5-5")
        decision = await self.options.can_use_tool(name, args, ToolPermissionContext(tool_use_id="tu1"))
        content = "allowed" if isinstance(decision, PermissionResultAllow) else decision.message
        yield UserMessage(content=[ToolResultBlock(tool_use_id="tu1", content=content, is_error=False)])
        yield ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1, session_id="sess-1", result="ok"
        )


def test_settings_change_asks_the_user(make_client, tmp_path):
    SettingsClient.changes = {"default_permission_mode": "plan"}
    client = make_client(lambda opts: SettingsClient(opts))
    p, _ = _setup(client, tmp_path)
    run_id = client.post(f"/api/projects/{p['id']}/runs", json={"prompt": "go", "permission_mode": "acceptEdits"}).json()["id"]
    _wait_for(client, run_id, {"waiting_approval"})
    request = next(e for e in client.get(f"/api/runs/{run_id}/events").json() if e["kind"] == "approval_request")
    assert request["payload"]["tool_name"] == "mcp__mercury__update_settings"
    client.post(f"/api/runs/{run_id}/approvals/{request['payload']['approval_id']}", json={"allow": True})
    _wait_for(client, run_id, {"succeeded"})


def test_disallowed_settings_change_is_denied_without_asking(make_client, tmp_path):
    SettingsClient.changes = {"agent_settings_access": "change"}  # only the user may change this
    client = make_client(lambda opts: SettingsClient(opts))
    p, _ = _setup(client, tmp_path)
    run_id = client.post(f"/api/projects/{p['id']}/runs", json={"prompt": "go", "permission_mode": "default"}).json()["id"]
    _wait_for(client, run_id, {"succeeded"})
    events = client.get(f"/api/runs/{run_id}/events").json()
    assert not any(e["kind"] == "approval_request" for e in events)
    assert "Only the user" in next(e for e in events if e["kind"] == "tool_result")["payload"]["content"]


def test_task_tools_create_list_update(make_client, tmp_path):
    from mercury.services.mercury_tools import build_task_tools

    client = make_client(lambda opts: FakeClient(opts))
    p, t = _setup(client, tmp_path)
    other = client.post("/api/projects", json={"name": "Other"}).json()
    foreign = client.post(f"/api/projects/{other['id']}/tasks", json={"title": "Not yours"}).json()
    tools = build_task_tools(p["id"])

    created, err = _call(tools, "create_task", title="  Write docs ", priority="high")
    assert not err and created["title"] == "Write docs" and created["status"] == "todo" and created["priority"] == "high"

    listed, _ = _call(tools, "list_tasks")
    assert {x["id"] for x in listed} == {t["id"], created["id"]}  # never other projects' tasks

    done, err = _call(tools, "update_task", task_id=t["id"], status="done", append_note="Listed files with ls.")
    assert not err and done["status"] == "done"
    stored = client.get(f"/api/tasks/{t['id']}").json()
    assert stored["status"] == "done" and stored["description"] == "use ls\n\nListed files with ls."

    assert [x["id"] for x in _call(tools, "list_tasks")[0]] == [created["id"]]  # done hidden by default
    assert [x["id"] for x in _call(tools, "list_tasks", status="done")[0]] == [t["id"]]

    msg, err = _call(tools, "update_task", task_id=foreign["id"], status="done")
    assert err and "isn't in this project" in msg
    assert client.get(f"/api/tasks/{foreign['id']}").json()["status"] == "todo"

    _, err = _call(tools, "update_task", task_id=created["id"], title="  ")
    assert err


def test_agent_completing_its_own_task_is_not_moved_to_review(make_client, tmp_path):
    from mercury.services.mercury_tools import build_task_tools

    class CompletingClient(FakeClient):
        project_id = task_id = None

        async def receive_response(self):
            async for message in super().receive_response():
                if isinstance(message, ResultMessage):
                    handler = next(t for t in build_task_tools(self.project_id) if t.name == "update_task").handler
                    await handler({"task_id": self.task_id, "status": "done"})
                yield message

    client = make_client(lambda opts: CompletingClient(opts))
    p, t = _setup(client, tmp_path)
    CompletingClient.project_id, CompletingClient.task_id = p["id"], t["id"]
    run = client.post(f"/api/projects/{p['id']}/runs", json={"prompt": "go", "task_id": t["id"]}).json()
    _wait_for(client, run["id"], {"succeeded"})
    assert client.get(f"/api/tasks/{t['id']}").json()["status"] == "done"


def test_run_without_mode_uses_saved_default(make_client, tmp_path):
    client = make_client(lambda opts: FakeClient(opts))
    p, _ = _setup(client, tmp_path)

    first = client.post(f"/api/projects/{p['id']}/runs", json={"prompt": "go"}).json()
    assert first["permission_mode"] == "acceptEdits"
    _wait_for(client, first["id"], {"succeeded"})

    client.put("/api/preferences", json={"default_permission_mode": "bypassPermissions"})
    second = client.post(f"/api/projects/{p['id']}/runs", json={"prompt": "go"}).json()
    assert second["permission_mode"] == "bypassPermissions"
    _wait_for(client, second["id"], {"succeeded"})
    assert FakeClient.instances[-1].options.permission_mode == "bypassPermissions"


def test_project_default_overrides_app_default(make_client, tmp_path):
    client = make_client(lambda opts: FakeClient(opts))
    p, _ = _setup(client, tmp_path)
    client.put("/api/preferences", json={"default_permission_mode": "bypassPermissions"})
    r = client.patch(f"/api/projects/{p['id']}", json={"default_permission_mode": "plan"})
    assert r.json()["default_permission_mode"] == "plan"

    run = client.post(f"/api/projects/{p['id']}/runs", json={"prompt": "go"}).json()
    assert run["permission_mode"] == "plan"
    _wait_for(client, run["id"], {"succeeded"})

    # An explicit mode still wins, and clearing the project's default falls back to the app's.
    run = client.post(f"/api/projects/{p['id']}/runs", json={"prompt": "go", "permission_mode": "default"}).json()
    assert run["permission_mode"] == "default"
    _wait_for(client, run["id"], {"succeeded", "waiting_approval", "failed"})
    assert client.patch(f"/api/projects/{p['id']}", json={"default_permission_mode": None}).json()["default_permission_mode"] is None
    run = client.post(f"/api/projects/{p['id']}/runs", json={"prompt": "go"}).json()
    assert run["permission_mode"] == "bypassPermissions"
    _wait_for(client, run["id"], {"succeeded"})
