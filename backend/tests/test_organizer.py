"""Organizer runs (the Organize buttons on Board and Docs) and the board organizer's tools."""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session

from mercury.db import get_engine
from mercury.models import AgentRun, RunStatus, Task

from test_agent_runs import FakeClient, _call, _wait_for


def test_organize_board_runs_without_repo_and_only_with_mercury_tools(make_client):
    client = make_client(lambda opts: FakeClient(opts))
    p = client.post("/api/projects", json={"name": "No repo"}).json()

    r = client.post(f"/api/projects/{p['id']}/organize", json={"target": "board"})
    assert r.status_code == 201, r.text
    run = _wait_for(client, r.json()["id"], {"succeeded"})
    assert run["purpose"] == "organize_board"
    assert run["prompt"] == "Organize the board"
    assert run["permission_mode"] == "default"

    opts = FakeClient.instances[-1].options
    assert opts.tools == []  # no repo: no built-in tools at all
    assert Path(opts.cwd).name == "organizer"
    assert isinstance(opts.system_prompt, str) and "board organizer" in opts.system_prompt
    assert "mcp__mercury__create_doc" in opts.disallowed_tools
    assert "mcp__mercury__merge_tasks" in opts.allowed_tools
    assert "mcp__mercury__update_task" not in opts.disallowed_tools


def test_organize_docs_reads_repo_but_cannot_change_it_or_delete_docs(make_client, tmp_path):
    client = make_client(lambda opts: FakeClient(opts))
    p = client.post("/api/projects", json={"name": "Demo", "repo_path": str(tmp_path)}).json()
    client.post(f"/api/projects/{p['id']}/docs", json={"title": "House style", "body": "Short titles.", "kind": "rules"})

    run = client.post(f"/api/projects/{p['id']}/organize", json={"target": "docs"}).json()
    _wait_for(client, run["id"], {"succeeded"})

    opts = FakeClient.instances[-1].options
    assert opts.cwd == str(tmp_path)
    assert opts.tools == ["Read", "Glob", "Grep"]
    assert {"Read", "Glob", "Grep"} <= set(opts.allowed_tools)
    assert "mcp__mercury__delete_doc" in opts.disallowed_tools
    assert "mcp__mercury__update_task" in opts.disallowed_tools
    assert "mcp__mercury__move_docs" not in opts.disallowed_tools
    assert "mcp__mercury__merge_tasks" not in opts.allowed_tools  # board organizer only
    assert "mcp__mercury__get_settings" not in opts.allowed_tools  # settings tools are for work runs
    assert "mcp__mercury__get_appearance" not in opts.allowed_tools
    assert "docs organizer" in opts.system_prompt
    assert "Short titles." in opts.system_prompt  # rules docs still apply

    # A follow-up stays an organizer session.
    follow = client.post(f"/api/runs/{run['id']}/follow-up", json={"prompt": "also tidy the drafts"}).json()
    _wait_for(client, follow["id"], {"succeeded"})
    assert follow["purpose"] == "organize_docs"
    assert "docs organizer" in FakeClient.instances[-1].options.system_prompt


def test_organize_returns_the_run_already_in_progress(make_client):
    client = make_client(lambda opts: FakeClient(opts, ask_permission=True))  # parks the run waiting for approval
    p = client.post("/api/projects", json={"name": "Demo"}).json()
    first = client.post(f"/api/projects/{p['id']}/organize", json={"target": "board"}).json()
    _wait_for(client, first["id"], {"waiting_approval"})

    again = client.post(f"/api/projects/{p['id']}/organize", json={"target": "board"})
    assert again.status_code == 200 and again.json()["id"] == first["id"]
    docs = client.post(f"/api/projects/{p['id']}/organize", json={"target": "docs"})
    assert docs.status_code == 201 and docs.json()["id"] != first["id"]

    assert client.post(f"/api/projects/{p['id']}/organize", json={"target": "code"}).status_code == 422
    for run_id in (first["id"], docs.json()["id"]):
        client.post(f"/api/runs/{run_id}/cancel")


def test_work_runs_have_no_organizer_tools(make_client, tmp_path):
    client = make_client(lambda opts: FakeClient(opts))
    p = client.post("/api/projects", json={"name": "Demo", "repo_path": str(tmp_path)}).json()
    run = client.post(f"/api/projects/{p['id']}/runs", json={"prompt": "go"}).json()
    _wait_for(client, run["id"], {"succeeded"})
    assert run["purpose"] == "work"
    opts = FakeClient.instances[-1].options
    assert opts.tools is None and opts.disallowed_tools == []
    assert opts.system_prompt["preset"] == "claude_code"


def _task(client, project_id, **fields):
    return client.post(f"/api/projects/{project_id}/tasks", json=fields).json()


def _ref(project, task):
    return f"{project['key']}-{task['number']}"


def test_merge_tasks_folds_duplicates_without_losing_anything(make_client):
    from mercury.services.mercury_tools import build_organizer_task_tools

    client = make_client(lambda opts: FakeClient(opts))
    p = client.post("/api/projects", json={"name": "Demo"}).json()
    keep = _task(client, p["id"], title="Dark mode", description="Use CSS variables.", status="backlog")
    dup = _task(client, p["id"], title="add dark theme!!", description="Remember the sidebar.", priority="high", status="todo")
    blank = _task(client, p["id"], title="dark mode pls", status="backlog")
    with Session(get_engine()) as s:
        s.add(AgentRun(project_id=p["id"], task_id=dup["id"], prompt="earlier", status=RunStatus.succeeded))
        s.commit()
    tools = build_organizer_task_tools(p["id"])

    result, err = _call(tools, "merge_tasks", keep_id=_ref(p, keep), merge_ids=[_ref(p, dup), blank["number"]], title="Add dark mode")
    assert not err, result
    assert result["deleted"] == [_ref(p, dup), _ref(p, blank)]

    kept = client.get(f"/api/tasks/{keep['id']}").json()
    assert kept["title"] == "Add dark mode"
    assert "Use CSS variables." in kept["description"]
    assert f"### From {_ref(p, dup)}: add dark theme!!\n\nRemember the sidebar." in kept["description"]
    assert f"{_ref(p, blank)} (dark mode pls)" in kept["description"]  # the merge is noted
    assert kept["priority"] == "high" and kept["status"] == "todo"  # highest priority, furthest status
    assert client.get(f"/api/tasks/{dup['id']}").status_code == 404
    runs = client.get(f"/api/projects/{p['id']}/runs?task_id={keep['id']}").json()
    assert [r["prompt"] for r in runs] == ["earlier"]  # sessions follow the kept task


def test_merge_tasks_refuses_what_it_shouldnt_fold_away(make_client):
    from mercury.services.mercury_tools import build_organizer_task_tools

    client = make_client(lambda opts: FakeClient(opts))
    p = client.post("/api/projects", json={"name": "Demo"}).json()
    other = client.post("/api/projects", json={"name": "Other"}).json()
    keep = _task(client, p["id"], title="Keep")
    done = _task(client, p["id"], title="Finished", status="done")
    issue = _task(client, p["id"], title="From GitHub")
    busy = _task(client, p["id"], title="Busy")
    foreign = _task(client, other["id"], title="Not yours")
    with Session(get_engine()) as s:
        linked = s.get(Task, issue["id"])
        linked.github_issue_number = 7
        s.add(linked)
        s.add(AgentRun(project_id=p["id"], task_id=busy["id"], prompt="working", status=RunStatus.running))
        s.commit()
    tools = build_organizer_task_tools(p["id"])

    for merge_ids, expected in [
        ([_ref(p, done)], "is done"),
        ([_ref(p, issue)], "GitHub issue #7"),
        ([_ref(p, busy)], "agent running"),
        ([_ref(other, foreign)], "Not tasks in this project"),
        ([_ref(p, keep)], "can't also be"),
    ]:
        msg, err = _call(tools, "merge_tasks", keep_id=_ref(p, keep), merge_ids=merge_ids)
        assert err and expected in msg, msg
    msg, err = _call(tools, "merge_tasks", keep_id=_ref(p, done), merge_ids=[_ref(p, keep)])
    assert err and "is done" in msg
    assert len(client.get(f"/api/projects/{p['id']}/tasks").json()) == 4  # nothing was deleted

    # A GitHub-linked task can be the one that stays.
    _, err = _call(tools, "merge_tasks", keep_id=_ref(p, issue), merge_ids=[_ref(p, keep)])
    assert not err


def test_reorder_tasks_orders_one_column(make_client):
    from mercury.services.mercury_tools import build_organizer_task_tools

    client = make_client(lambda opts: FakeClient(opts))
    p = client.post("/api/projects", json={"name": "Demo"}).json()
    a, b, c, d = (_task(client, p["id"], title=t) for t in "abcd")
    elsewhere = _task(client, p["id"], title="e", status="backlog")
    tools = build_organizer_task_tools(p["id"])

    result, err = _call(tools, "reorder_tasks", status="todo", task_ids=[_ref(p, c), a["number"]])
    assert not err and result["order"] == [_ref(p, c), _ref(p, a), _ref(p, b), _ref(p, d)]
    todo = [t for t in client.get(f"/api/projects/{p['id']}/tasks").json() if t["status"] == "todo"]
    assert [t["id"] for t in sorted(todo, key=lambda t: t["position"])] == [c["id"], a["id"], b["id"], d["id"]]

    msg, err = _call(tools, "reorder_tasks", status="todo", task_ids=[_ref(p, elsewhere)])
    assert err and "Not in the todo column" in msg


def test_list_tasks_full_descriptions():
    from mercury.services import mercury_tools

    long = "x" * (mercury_tools.MAX_LISTED_DESCRIPTION + 50)
    task = Task(id=1, project_id=1, number=4, title="t", description=long)
    assert mercury_tools._task_dict(task, "DEM", full=False)["description"].endswith("…")
    assert mercury_tools._task_dict(task, "DEM", full=True)["description"] == long
    assert mercury_tools._task_dict(task, "DEM")["id"] == "DEM-4"
