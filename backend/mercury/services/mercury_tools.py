"""Mercury tools that agents can call during a run.

They are served from an in-process MCP server (one per run) and are scoped to
the run's project, so an agent can't touch other projects:

- Tasks: list, create and update (including marking done), but not delete.
  Board organizer runs also get merge_tasks (which folds duplicates into one task
  without losing what they say) and reorder_tasks.
- Docs (the project's long-term memory): search, list, read, create, update and
  delete. Deleting goes through Mercury's normal approval rules instead of being
  pre-approved (see PREAPPROVED_TOOL_NAMES). All writes go through doc_store so
  sections and the search index stay current.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from claude_agent_sdk import SdkMcpTool, ToolAnnotations, create_sdk_mcp_server, tool
from sqlmodel import Session, func, select

from ..db import get_engine
from ..models import (
    ACTIVE_RUN_STATUSES,
    AgentRun,
    Doc,
    DocKind,
    DocOrigin,
    DocStatus,
    Task,
    TaskPriority,
    TaskStatus,
    utcnow,
)
from . import doc_collections, doc_store

SERVER_NAME = "mercury"
TASK_TOOL_NAMES = ("list_tasks", "create_task", "update_task")
ORGANIZER_TASK_TOOL_NAMES = ("merge_tasks", "reorder_tasks")  # only in board organizer runs
DOC_TOOL_NAMES = (
    "browse_docs",
    "search_docs",
    "list_docs",
    "read_doc",
    "create_doc",
    "update_doc",
    "delete_doc",
    "move_docs",
    "create_collection",
    "update_collection",
    "delete_collection",
)
# Tools that go through Mercury's normal approval rules instead of always being allowed.
ASK_TOOL_NAMES = ("delete_doc",)


def qualified(name: str) -> str:
    """Full tool name as the agent (and Mercury's permission callback) sees it."""
    return f"mcp__{SERVER_NAME}__{name}"


QUALIFIED_TOOL_NAMES = frozenset(qualified(n) for n in TASK_TOOL_NAMES + ORGANIZER_TASK_TOOL_NAMES + DOC_TOOL_NAMES)
PREAPPROVED_TOOL_NAMES = QUALIFIED_TOOL_NAMES - {qualified(n) for n in ASK_TOOL_NAMES}
ORGANIZER_TASK_TOOLS = frozenset(qualified(n) for n in ORGANIZER_TASK_TOOL_NAMES)

MAX_LISTED_DESCRIPTION = 400
MAX_MERGED_TASKS = 20
SEARCH_LIMIT = 8
LIST_LIMIT = 50
LISTED_SUMMARY_CHARS = 160
BROWSE_SUMMARY_CHARS = 400
READ_LIMIT_CHARS = 20_000  # longer docs are read by line range
READ_OPENING_CHARS = 3_000

_STATUSES = [s.value for s in TaskStatus]
_PRIORITIES = [p.value for p in TaskPriority]
_STATUS_RANK = {s: i for i, s in enumerate(TaskStatus)}  # backlog < todo < ... < done
_PRIORITY_RANK = {p: i for i, p in enumerate(TaskPriority)}
_DOC_STATUSES = [s.value for s in DocStatus]
_DOC_KINDS = [k.value for k in DocKind]


def next_position(session: Session, project_id: int, status: TaskStatus) -> float:
    """Position that puts a task at the bottom of its board column."""
    last = session.exec(
        select(func.max(Task.position)).where(Task.project_id == project_id, Task.status == status)
    ).one()
    return (last or 0) + 1


def _task_dict(task: Task, full: bool = True) -> dict[str, Any]:
    description = task.description
    if not full and len(description) > MAX_LISTED_DESCRIPTION:
        description = description[:MAX_LISTED_DESCRIPTION] + "…"
    return {
        "id": task.id,
        "title": task.title,
        "status": task.status.value,
        "priority": task.priority.value,
        "description": description,
    }


def _ok(data: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(data, indent=2)}]}


def _error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "is_error": True}


def build_task_tools(project_id: int) -> list[SdkMcpTool[Any]]:
    @tool(
        "list_tasks",
        "List this project's tasks in Mercury (the user's project board). Done tasks are left out "
        "unless you ask for them or filter by status.",
        {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": _STATUSES, "description": "Only tasks with this status."},
                "include_done": {"type": "boolean", "description": "Include done tasks. Default false."},
                "full": {
                    "type": "boolean",
                    "description": f"Return whole descriptions (by default they're cut at {MAX_LISTED_DESCRIPTION} characters).",
                },
            },
        },
        annotations=ToolAnnotations(readOnlyHint=True),
    )
    async def list_tasks(args: dict[str, Any]) -> dict[str, Any]:
        query = select(Task).where(Task.project_id == project_id)
        if args.get("status"):
            query = query.where(Task.status == TaskStatus(args["status"]))
        elif not args.get("include_done"):
            query = query.where(Task.status != TaskStatus.done)
        with Session(get_engine()) as s:
            tasks = s.exec(query.order_by(Task.status, Task.position, Task.id)).all()
            return _ok([_task_dict(t, full=bool(args.get("full"))) for t in tasks])

    @tool(
        "create_task",
        "Create a task on this project's Mercury board. Use it when the user asks you to plan, "
        "break down, or record follow-up work, instead of giving them a list to paste.",
        {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short, imperative title."},
                "description": {"type": "string", "description": "Details, acceptance criteria, context (Markdown)."},
                "status": {"type": "string", "enum": _STATUSES, "description": "Default: todo."},
                "priority": {"type": "string", "enum": _PRIORITIES, "description": "Default: medium."},
            },
            "required": ["title"],
        },
    )
    async def create_task(args: dict[str, Any]) -> dict[str, Any]:
        title = (args.get("title") or "").strip()
        if not title:
            return _error("A task needs a non-empty title.")
        status = TaskStatus(args.get("status") or TaskStatus.todo)
        with Session(get_engine()) as s:
            task = Task(
                project_id=project_id,
                title=title,
                description=args.get("description") or "",
                status=status,
                priority=TaskPriority(args.get("priority") or TaskPriority.medium),
                position=next_position(s, project_id, status),
            )
            s.add(task)
            s.commit()
            s.refresh(task)
            return _ok(_task_dict(task))

    @tool(
        "update_task",
        "Update a task on this project's Mercury board. Set status to \"done\" to complete it once the "
        "work is finished and verified. Only the fields you pass are changed.",
        {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer"},
                "title": {"type": "string"},
                "description": {"type": "string", "description": "Replaces the whole description."},
                "append_note": {
                    "type": "string",
                    "description": "Appended to the description, e.g. what you did or what's left.",
                },
                "status": {"type": "string", "enum": _STATUSES},
                "priority": {"type": "string", "enum": _PRIORITIES},
            },
            "required": ["task_id"],
        },
    )
    async def update_task(args: dict[str, Any]) -> dict[str, Any]:
        with Session(get_engine()) as s:
            task = s.get(Task, args.get("task_id"))
            if task is None or task.project_id != project_id:
                return _error(f"Task {args.get('task_id')} isn't in this project. Use list_tasks to find task ids.")
            if "title" in args:
                title = (args["title"] or "").strip()
                if not title:
                    return _error("A task needs a non-empty title.")
                task.title = title
            if "description" in args:
                task.description = args["description"] or ""
            if args.get("append_note"):
                task.description = f"{task.description.rstrip()}\n\n{args['append_note']}".lstrip()
            if args.get("priority"):
                task.priority = TaskPriority(args["priority"])
            if args.get("status") and TaskStatus(args["status"]) != task.status:
                task.status = TaskStatus(args["status"])
                task.position = next_position(s, project_id, task.status)
            task.updated_at = utcnow()
            s.add(task)
            s.commit()
            s.refresh(task)
            return _ok(_task_dict(task))

    return [list_tasks, create_task, update_task]


def build_organizer_task_tools(project_id: int) -> list[SdkMcpTool[Any]]:
    """Tools for tidying the board: folding duplicate tasks together and ordering columns."""

    @tool(
        "merge_tasks",
        "Fold duplicate or overlapping tasks into one. The tasks in merge_ids are deleted; keep_id stays. "
        "Without a description, each merged task's description is appended to the kept one, so nothing is lost; "
        "if you pass a description it replaces the kept one and must carry every detail from all of them. "
        "Mercury notes which tasks were merged, moves their agent sessions to the kept task, and keeps the "
        "highest priority and the furthest-along status. Done tasks, tasks with a running agent, and tasks "
        "linked to GitHub issues can't be merged away (a GitHub-linked task can be the one you keep).",
        {
            "type": "object",
            "properties": {
                "keep_id": {"type": "integer", "description": "The task that remains."},
                "merge_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 1,
                    "maxItems": MAX_MERGED_TASKS,
                    "description": "Tasks folded into keep_id and then deleted.",
                },
                "title": {"type": "string", "description": "New title for the kept task."},
                "description": {"type": "string", "description": "Consolidated description (Markdown)."},
                "priority": {"type": "string", "enum": _PRIORITIES, "description": "Default: the highest of them."},
            },
            "required": ["keep_id", "merge_ids"],
        },
    )
    async def merge_tasks(args: dict[str, Any]) -> dict[str, Any]:
        keep_id = args.get("keep_id")
        merge_ids = list(dict.fromkeys(i for i in args.get("merge_ids") or [] if isinstance(i, int)))
        if not isinstance(keep_id, int) or not merge_ids:
            return _error("Pass keep_id and the merge_ids to fold into it.")
        if keep_id in merge_ids:
            return _error("keep_id can't also be in merge_ids.")
        if len(merge_ids) > MAX_MERGED_TASKS:
            return _error(f"Merge at most {MAX_MERGED_TASKS} tasks at a time.")
        with Session(get_engine()) as s:
            tasks = {t.id: t for t in s.exec(select(Task).where(Task.id.in_([keep_id, *merge_ids]))).all()}
            missing = [i for i in [keep_id, *merge_ids] if i not in tasks or tasks[i].project_id != project_id]
            if missing:
                return _error(f"Not tasks in this project: {missing}. Use list_tasks to find task ids.")
            keep = tasks[keep_id]
            merged = [tasks[i] for i in merge_ids]
            if keep.status == TaskStatus.done:
                return _error(f"T-{keep.id} is done; keep an open task instead.")
            active = set(
                s.exec(
                    select(AgentRun.task_id).where(
                        AgentRun.task_id.in_(merge_ids), AgentRun.status.in_(list(ACTIVE_RUN_STATUSES))
                    )
                ).all()
            )
            problems = []
            for t in merged:
                if t.status == TaskStatus.done:
                    problems.append(f"T-{t.id} is done")
                elif t.id in active:
                    problems.append(f"T-{t.id} has an agent running on it")
                elif t.github_issue_number is not None:
                    problems.append(f"T-{t.id} is linked to GitHub issue #{t.github_issue_number} (keep it instead)")
            if problems:
                return _error("Nothing was merged: " + "; ".join(problems) + ".")

            if "title" in args:
                title = (args["title"] or "").strip()
                if not title:
                    return _error("A task needs a non-empty title.")
                keep.title = title
            if (args.get("description") or "").strip():
                body = args["description"].strip()
            else:
                parts = [keep.description.strip()]
                for t in merged:
                    if t.description.strip():
                        parts.append(f"### From T-{t.id}: {t.title}\n\n{t.description.strip()}")
                body = "\n\n".join(p for p in parts if p)
            note = "_Merged in: " + ", ".join(f"T-{t.id} ({t.title})" for t in merged) + "._"
            keep.description = f"{body}\n\n{note}".strip()
            if args.get("priority"):
                keep.priority = TaskPriority(args["priority"])
            else:
                keep.priority = max([keep, *merged], key=lambda t: _PRIORITY_RANK[t.priority]).priority
            furthest = max([keep, *merged], key=lambda t: _STATUS_RANK[t.status]).status
            if furthest != keep.status:
                keep.status = furthest
                keep.position = next_position(s, project_id, furthest)
            keep.updated_at = utcnow()
            s.add(keep)
            for run in s.exec(select(AgentRun).where(AgentRun.task_id.in_(merge_ids))).all():
                run.task_id = keep.id
                s.add(run)
            for t in merged:
                s.delete(t)
            s.commit()
            s.refresh(keep)
            return _ok({"kept": _task_dict(keep), "deleted": merge_ids})

    @tool(
        "reorder_tasks",
        "Set the order of tasks in one board column, top first. Tasks in the column that you leave out keep "
        "their relative order below the ones you list.",
        {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": _STATUSES, "description": "The column."},
                "task_ids": {"type": "array", "items": {"type": "integer"}, "minItems": 1},
            },
            "required": ["status", "task_ids"],
        },
    )
    async def reorder_tasks(args: dict[str, Any]) -> dict[str, Any]:
        status = TaskStatus(args["status"])
        order = list(dict.fromkeys(i for i in args.get("task_ids") or [] if isinstance(i, int)))
        if not order:
            return _error("Pass the task_ids in the order you want.")
        with Session(get_engine()) as s:
            column = s.exec(
                select(Task).where(Task.project_id == project_id, Task.status == status).order_by(Task.position, Task.id)
            ).all()
            by_id = {t.id: t for t in column}
            stray = [i for i in order if i not in by_id]
            if stray:
                return _error(f"Not in the {status.value} column: {stray}. Nothing was reordered.")
            listed = set(order)
            ordered = [by_id[i] for i in order] + [t for t in column if t.id not in listed]
            for pos, t in enumerate(ordered, start=1):
                if t.position != pos:
                    t.position = pos
                    s.add(t)
            s.commit()
            return _ok({"status": status.value, "order": [t.id for t in ordered]})

    return [merge_tasks, reorder_tasks]


def _missing_doc(doc_id: Any) -> dict[str, Any]:
    return _error(f"Doc {doc_id} isn't in this project. Use search_docs or list_docs to find doc ids.")


def _doc_header(doc: Doc, paths: dict[int, str] | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": doc.id,
        "title": doc.title,
        "collection_id": doc.collection_id,
        "status": doc.status.value,
        "kind": doc.kind.value,
        "pinned": doc.pinned,
        "origin": doc.origin.value,
        "updated_at": doc.updated_at.isoformat(),
        "summary": doc.summary or None,
        "summary_stale": doc_store.summary_stale(doc),
    }
    if doc.superseded_by:
        data["superseded_by"] = doc.superseded_by
    if paths and doc.collection_id in paths:
        data["collection"] = paths[doc.collection_id]
    return data


def _collection_row(c: Any, counts: dict[Any, int], paths: dict[int, str], *, summary_chars: int) -> dict[str, Any]:
    row: dict[str, Any] = {"id": c.id, "name": paths.get(c.id, c.name), "docs": counts.get(c.id, 0)}
    if c.description:
        row["description"] = c.description
    row["summary"] = _short(c.summary, summary_chars) if c.summary else None
    return row


def _short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def build_doc_tools(project_id: int) -> list[SdkMcpTool[Any]]:
    def get_doc(s: Session, doc_id: Any) -> Doc | None:
        doc = s.get(Doc, doc_id) if isinstance(doc_id, int) else None
        return doc if doc is not None and doc.project_id == project_id else None

    def collection_problem(s: Session, collection_id: Any) -> str | None:
        if collection_id is None or doc_collections.get(s, project_id, collection_id) is not None:
            return None
        return f"Collection {collection_id} isn't in this project. Use browse_docs to see the collections."

    def filing_hint(s: Session, doc: Doc) -> str | None:
        """For an unfiled new doc: the collections that similar docs are in."""
        if not doc_collections.all_for(s, project_id):
            return None
        hits = doc_store.search(s, project_id, f"{doc.title}\n{doc.summary}", limit=8)
        seen: dict[int, str] = {}
        for h in hits:
            if h.doc.id != doc.id and h.doc.collection_id and h.collection:
                seen.setdefault(h.doc.collection_id, h.collection)
        if not seen:
            return "Not filed in a collection. See browse_docs, and file it with move_docs if one fits."
        options = ", ".join(f"{name} (id {cid})" for cid, name in list(seen.items())[:3])
        return f"Not filed in a collection. Similar docs are in: {options}. File it with move_docs if one fits."

    @tool(
        "browse_docs",
        "Get oriented in this project's Mercury docs. Without collection_id: the collections (folders, at most "
        "two levels) with their summaries and doc counts, plus how many docs are unfiled. With collection_id: "
        "that collection's summary, its subcollections, and its docs (a page at a time). unfiled=true lists "
        "docs in no collection. Use it to see what knowledge exists before you know what to search for; use "
        "search_docs to find specific things.",
        {
            "type": "object",
            "properties": {
                "collection_id": {"type": "integer"},
                "unfiled": {"type": "boolean", "description": "List the docs that aren't in any collection."},
                "limit": {"type": "integer", "description": f"Docs per page. Default {LIST_LIMIT}, at most 200."},
                "offset": {"type": "integer"},
            },
        },
        annotations=ToolAnnotations(readOnlyHint=True),
    )
    async def browse_docs(args: dict[str, Any]) -> dict[str, Any]:
        limit = max(1, min(int(args.get("limit") or LIST_LIMIT), 200))
        offset = max(0, int(args.get("offset") or 0))
        with Session(get_engine()) as s:
            counts = doc_collections.doc_counts(s, project_id)
            paths = doc_collections.paths(s, project_id)
            cid = args.get("collection_id")
            if cid is None and not args.get("unfiled"):
                top = []
                for node in doc_collections.tree(s, project_id):
                    row = _collection_row(node["collection"], counts, paths, summary_chars=BROWSE_SUMMARY_CHARS)
                    row["docs"] = node["doc_count"]
                    if node["children"]:
                        row["subcollections"] = [
                            {"id": k.id, "name": k.name, "docs": counts.get(k.id, 0)} for k in node["children"]
                        ]
                    top.append(row)
                return _ok({"total_docs": sum(counts.values()), "unfiled_docs": counts.get(None, 0), "collections": top})
            if cid is not None:
                c = doc_collections.get(s, project_id, cid)
                if c is None:
                    return _error(collection_problem(s, cid) or "No such collection.")
                query = select(Doc).where(Doc.collection_id == c.id)
                data: dict[str, Any] = {
                    "id": c.id,
                    "name": paths[c.id],
                    "parent_id": c.parent_id,
                    "description": c.description or None,
                    "summary": c.summary or None,
                    "summary_stale": doc_collections.summary_stale(s, c),
                }
                children = [k for k in doc_collections.all_for(s, project_id) if k.parent_id == c.id]
                if children:
                    data["subcollections"] = [
                        _collection_row(k, counts, paths, summary_chars=BROWSE_SUMMARY_CHARS) for k in children
                    ]
            else:
                query = select(Doc).where(Doc.project_id == project_id, Doc.collection_id.is_(None))
                data = {"name": "(unfiled)"}
            total = s.exec(select(func.count()).select_from(query.subquery())).one()
            docs = s.exec(query.order_by(Doc.pinned.desc(), Doc.updated_at.desc()).offset(offset).limit(limit)).all()
            data["total_docs"] = total
            data["offset"] = offset
            data["docs"] = [
                {"id": d.id, "title": d.title, "status": d.status.value,
                 "summary": _short(d.summary, LISTED_SUMMARY_CHARS) or None}
                for d in docs
            ]
            return _ok(data)

    @tool(
        "create_collection",
        "Create a collection (folder) for this project's docs. Make one when several docs share a topic and no "
        "existing collection fits. Collections nest at most two levels, so parent_id must be a top-level "
        "collection. Mercury writes the collection's summary from its docs.",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Short and topical, e.g. \"Save file format\"."},
                "description": {"type": "string", "description": "What belongs here, to guide filing."},
                "parent_id": {"type": "integer"},
            },
            "required": ["name"],
        },
    )
    async def create_collection(args: dict[str, Any]) -> dict[str, Any]:
        with Session(get_engine()) as s:
            try:
                c = doc_collections.create(
                    s, project_id, args.get("name") or "", args.get("description") or "", args.get("parent_id")
                )
            except doc_collections.CollectionError as e:
                return _error(str(e))
            return _ok({"id": c.id, "name": doc_collections.paths(s, project_id)[c.id], "description": c.description or None})

    @tool(
        "update_collection",
        "Rename a collection, change its description, or move it (parent_id: a top-level collection, or null "
        "for the top level). Only the fields you pass change.",
        {
            "type": "object",
            "properties": {
                "collection_id": {"type": "integer"},
                "name": {"type": "string"},
                "description": {"type": "string"},
                "parent_id": {"type": ["integer", "null"]},
            },
            "required": ["collection_id"],
        },
    )
    async def update_collection(args: dict[str, Any]) -> dict[str, Any]:
        with Session(get_engine()) as s:
            c = doc_collections.get(s, project_id, args.get("collection_id"))
            if c is None:
                return _error(collection_problem(s, args.get("collection_id")) or "No such collection.")
            changes = {k: args[k] for k in ("name", "description", "parent_id") if k in args}
            try:
                c = doc_collections.update(s, c, changes)
            except doc_collections.CollectionError as e:
                s.rollback()
                return _error(str(e))
            return _ok({"id": c.id, "name": doc_collections.paths(s, project_id)[c.id], "description": c.description or None})

    @tool(
        "delete_collection",
        "Delete a collection. Its docs and subcollections move up a level (docs in a top-level collection "
        "become unfiled); no doc is deleted.",
        {"type": "object", "properties": {"collection_id": {"type": "integer"}}, "required": ["collection_id"]},
    )
    async def delete_collection(args: dict[str, Any]) -> dict[str, Any]:
        with Session(get_engine()) as s:
            c = doc_collections.get(s, project_id, args.get("collection_id"))
            if c is None:
                return _error(collection_problem(s, args.get("collection_id")) or "No such collection.")
            result = {"deleted": c.id, "name": c.name, "docs_moved_to": c.parent_id}
            doc_collections.delete(s, c)
            return _ok(result)

    @tool(
        "move_docs",
        "File docs into a collection, or unfile them (collection_id null). Moving doesn't change the docs.",
        {
            "type": "object",
            "properties": {
                "doc_ids": {"type": "array", "items": {"type": "integer"}, "minItems": 1, "maxItems": 500},
                "collection_id": {"type": ["integer", "null"]},
            },
            "required": ["doc_ids", "collection_id"],
        },
    )
    async def move_docs(args: dict[str, Any]) -> dict[str, Any]:
        doc_ids = [i for i in args.get("doc_ids") or [] if isinstance(i, int)]
        if not doc_ids:
            return _error("Pass the doc_ids to move.")
        target = args.get("collection_id")
        with Session(get_engine()) as s:
            try:
                moved = doc_collections.move_docs(s, project_id, doc_ids, target)
            except doc_collections.CollectionError as e:
                return _error(str(e))
            name = doc_collections.paths(s, project_id).get(target) if target is not None else "(unfiled)"
            return _ok({"moved": moved, "to": name})

    def check_target(s: Session, doc_id: int | None, target: Any) -> str | None:
        if target is None:
            return None
        other = get_doc(s, target)
        if other is None or other.id == doc_id:
            return "superseded_by must be the id of another doc in this project."
        return None

    @tool(
        "search_docs",
        "Search this project's Mercury docs (its long-term memory: decisions, findings, specs, notes). "
        "Matches both words and meaning, so a plain-language question works, and exact names and "
        "identifiers help. Returns the best-matching docs, each with its summary, the best-matching "
        "section, that section's line range, a snippet, and whether it matched on words, meaning or both. "
        "Read more with read_doc(doc_id, start_line, end_line). Archived docs are left out unless you ask.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "description": f"Default {SEARCH_LIMIT}, at most 25."},
                "include_archived": {"type": "boolean"},
                "collection_id": {"type": "integer", "description": "Only search this collection and its subcollections."},
            },
            "required": ["query"],
        },
        annotations=ToolAnnotations(readOnlyHint=True),
    )
    async def search_docs(args: dict[str, Any]) -> dict[str, Any]:
        limit = max(1, min(int(args.get("limit") or SEARCH_LIMIT), 25))
        cid = args.get("collection_id")
        with Session(get_engine()) as s:
            problem = collection_problem(s, cid)
            if problem:
                return _error(problem)
            scope = doc_collections.with_children(s, project_id, cid) if cid is not None else None

        def run() -> list[dict[str, Any]]:  # embedding the query is CPU work: keep it off the event loop
            with Session(get_engine()) as s:
                hits = doc_store.search(
                    s,
                    project_id,
                    args.get("query") or "",
                    limit=limit,
                    include_archived=bool(args.get("include_archived")),
                    collection_ids=scope,
                )
                return [h.as_dict() for h in hits]

        results = await asyncio.to_thread(run)
        if not results:
            return _ok({"results": [], "hint": "No matches. Try other words, or list_docs to browse."})
        return _ok({"results": results})

    @tool(
        "list_docs",
        "List this project's Mercury docs across all collections, most recently updated first (pinned first), "
        "a page at a time; filter by status or kind. To explore by topic use browse_docs; to find docs about "
        "something use search_docs.",
        {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": _DOC_STATUSES},
                "kind": {"type": "string", "enum": _DOC_KINDS},
                "limit": {"type": "integer", "description": f"Default {LIST_LIMIT}, at most 200."},
                "offset": {"type": "integer"},
            },
        },
        annotations=ToolAnnotations(readOnlyHint=True),
    )
    async def list_docs(args: dict[str, Any]) -> dict[str, Any]:
        limit = max(1, min(int(args.get("limit") or LIST_LIMIT), 200))
        offset = max(0, int(args.get("offset") or 0))
        query = select(Doc).where(Doc.project_id == project_id)
        if args.get("status"):
            query = query.where(Doc.status == DocStatus(args["status"]))
        if args.get("kind"):
            query = query.where(Doc.kind == DocKind(args["kind"]))
        with Session(get_engine()) as s:
            total = s.exec(select(func.count()).select_from(query.subquery())).one()
            docs = s.exec(query.order_by(Doc.pinned.desc(), Doc.updated_at.desc()).offset(offset).limit(limit)).all()
            rows = [
                {"id": d.id, "title": d.title, "status": d.status.value, "collection_id": d.collection_id,
                 "updated_at": d.updated_at.date().isoformat(), "summary": _short(d.summary, LISTED_SUMMARY_CHARS) or None}
                for d in docs
            ]
            return _ok({"total": total, "offset": offset, "docs": rows})

    @tool(
        "read_doc",
        "Read one of this project's Mercury docs. Short docs come back whole. For a long doc you get its "
        "outline (sections with line ranges) and the opening; then read the parts you need with "
        "start_line/end_line. outline=true returns just the outline.",
        {
            "type": "object",
            "properties": {
                "doc_id": {"type": "integer"},
                "start_line": {"type": "integer", "description": "1-based, inclusive."},
                "end_line": {"type": "integer", "description": "1-based, inclusive. Default: end of doc."},
                "outline": {"type": "boolean"},
            },
            "required": ["doc_id"],
        },
        annotations=ToolAnnotations(readOnlyHint=True),
    )
    async def read_doc(args: dict[str, Any]) -> dict[str, Any]:
        with Session(get_engine()) as s:
            doc = get_doc(s, args.get("doc_id"))
            if doc is None:
                return _missing_doc(args.get("doc_id"))
            data = _doc_header(doc)
            total_lines = doc.body.count("\n") + 1
            data["total_lines"] = total_lines
            data["chars"] = len(doc.body)
            if args.get("outline"):
                data["outline"] = doc_store.outline(s, doc)
                return _ok(data)
            if args.get("start_line") is not None or args.get("end_line") is not None:
                text, a, b, _ = doc_store.line_range(doc.body, int(args.get("start_line") or 1), args.get("end_line"))
                if len(text) > READ_LIMIT_CHARS:
                    text = text[:READ_LIMIT_CHARS]
                    data["note"] = f"Cut at {READ_LIMIT_CHARS} characters; read a smaller range for the rest."
                data["lines"] = [a, b]
                data["body"] = text
                return _ok(data)
            if len(doc.body) <= READ_LIMIT_CHARS:
                data["body"] = doc.body
                return _ok(data)
            data["outline"] = doc_store.outline(s, doc)
            data["opening"] = doc.body[:READ_OPENING_CHARS]
            data["note"] = "This doc is long: read the sections you need with start_line/end_line."
            return _ok(data)

    @tool(
        "create_doc",
        "Create a Markdown doc in this project's Mercury docs. Search first and update an existing doc "
        "when one covers the topic: if a doc with a similar title exists you get it back instead, and must "
        "pass confirm_new=true to create a separate doc anyway.",
        {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Specific and searchable."},
                "body": {"type": "string", "description": "Markdown. Use ## headings for distinct topics."},
                "summary": {
                    "type": "string",
                    "description": "Required when the body isn't empty. 2-4 sentences for other agents deciding "
                    "whether to read it: what it covers, key decisions/facts with names and values, what's open.",
                },
                "kind": {"type": "string", "enum": _DOC_KINDS, "description": "\"rules\" docs go into every run's instructions. Default doc."},
                "status": {"type": "string", "enum": _DOC_STATUSES, "description": "Default current."},
                "pinned": {"type": "boolean"},
                "collection_id": {"type": "integer", "description": "The collection to file it in (see browse_docs)."},
                "confirm_new": {"type": "boolean", "description": "Create it even though similar docs exist."},
            },
            "required": ["title"],
        },
    )
    async def create_doc(args: dict[str, Any]) -> dict[str, Any]:
        title = (args.get("title") or "").strip()
        body = args.get("body") or ""
        summary = (args.get("summary") or "").strip()
        if not title:
            return _error("A doc needs a non-empty title.")
        if body.strip() and not summary:
            return _error("Pass a summary for agents along with the body.")
        with Session(get_engine()) as s:
            problem = collection_problem(s, args.get("collection_id"))
            if problem:
                return _error(problem)
            if not args.get("confirm_new"):
                similar = doc_store.similar_titles(s, project_id, title)
                if similar:
                    return _error(
                        "Docs with similar titles already exist. Update one of them with update_doc, or call "
                        "create_doc again with confirm_new=true if this really is a separate topic:\n"
                        + json.dumps([{"id": d.id, "title": d.title, "summary": _short(d.summary, LISTED_SUMMARY_CHARS)} for d in similar], indent=2)
                    )
            doc = Doc(
                project_id=project_id,
                title=title,
                body=body,
                kind=DocKind(args.get("kind") or DocKind.doc),
                status=DocStatus(args.get("status") or DocStatus.current),
                pinned=bool(args.get("pinned")),
                collection_id=args.get("collection_id"),
                origin=DocOrigin.agent,
            )
            doc_store.save_doc(s, doc, summary=summary or None)
            data = _doc_header(doc, doc_collections.paths(s, project_id))
            if doc.collection_id is None and doc.kind == DocKind.doc:
                hint = filing_hint(s, doc)
                if hint:
                    data["note"] = hint
            return _ok(data)

    @tool(
        "update_doc",
        "Update one of this project's Mercury docs; only the fields you pass change. To edit part of a doc, "
        "pass old_text and new_text (old_text must occur exactly once) instead of rewriting the body. Pass "
        "an updated summary whenever the meaning changes. To retire a doc, set status \"superseded\" with "
        "superseded_by, or \"archived\".",
        {
            "type": "object",
            "properties": {
                "doc_id": {"type": "integer"},
                "title": {"type": "string"},
                "body": {"type": "string", "description": "Replaces the whole body (Markdown)."},
                "old_text": {"type": "string", "description": "Exact text to replace; must occur once."},
                "new_text": {"type": "string", "description": "Replacement for old_text."},
                "append": {"type": "string", "description": "Appended to the end of the body."},
                "summary": {"type": "string", "description": "Replaces the agent summary."},
                "status": {"type": "string", "enum": _DOC_STATUSES},
                "superseded_by": {"type": "integer", "description": "The doc that replaces this one."},
                "kind": {"type": "string", "enum": _DOC_KINDS},
                "pinned": {"type": "boolean"},
                "collection_id": {"type": ["integer", "null"], "description": "Move it to this collection (null: unfiled)."},
            },
            "required": ["doc_id"],
        },
    )
    async def update_doc(args: dict[str, Any]) -> dict[str, Any]:
        if "body" in args and "old_text" in args:
            return _error("Pass either body or old_text/new_text, not both.")
        if ("old_text" in args) != ("new_text" in args):
            return _error("old_text and new_text go together.")
        with Session(get_engine()) as s:
            doc = get_doc(s, args.get("doc_id"))
            if doc is None:
                return _missing_doc(args.get("doc_id"))
            if "superseded_by" in args:
                problem = check_target(s, doc.id, args["superseded_by"])
                if problem:
                    return _error(problem)
                doc.superseded_by = args["superseded_by"]
                if args["superseded_by"] is not None and not args.get("status"):
                    doc.status = DocStatus.superseded
            if "title" in args:
                title = (args["title"] or "").strip()
                if not title:
                    return _error("A doc needs a non-empty title.")
                doc.title = title
            if "body" in args:
                doc.body = args["body"] or ""
            if "old_text" in args:
                old = args["old_text"] or ""
                count = doc.body.count(old) if old else 0
                if count != 1:
                    found = "isn't in the doc" if count == 0 else f"occurs {count} times"
                    return _error(f"old_text {found}; it must occur exactly once. Nothing was changed.")
                doc.body = doc.body.replace(old, args["new_text"] or "", 1)
            if args.get("append"):
                doc.body = f"{doc.body.rstrip()}\n\n{args['append']}".lstrip()
            if args.get("status"):
                doc.status = DocStatus(args["status"])
            if args.get("kind"):
                doc.kind = DocKind(args["kind"])
            if "pinned" in args:
                doc.pinned = bool(args["pinned"])
            if "collection_id" in args:
                problem = collection_problem(s, args["collection_id"])
                if problem:
                    return _error(problem)
                doc.collection_id = args["collection_id"]
            summary = args.get("summary")
            doc_store.save_doc(s, doc, summary=summary.strip() if isinstance(summary, str) and summary.strip() else None)
            data = _doc_header(doc, doc_collections.paths(s, project_id))
            if data["summary_stale"]:
                data["note"] = "The summary no longer matches the doc; pass summary to update it (otherwise Mercury rewrites it shortly)."
            return _ok(data)

    @tool(
        "delete_doc",
        "Delete one of this project's Mercury docs. Unless the run is in full-auto mode, the user is "
        "asked to approve it. Only delete a doc when the user asked you to; to retire one, set its status.",
        {"type": "object", "properties": {"doc_id": {"type": "integer"}}, "required": ["doc_id"]},
        annotations=ToolAnnotations(destructiveHint=True),
    )
    async def delete_doc(args: dict[str, Any]) -> dict[str, Any]:
        with Session(get_engine()) as s:
            doc = get_doc(s, args.get("doc_id"))
            if doc is None:
                return _missing_doc(args.get("doc_id"))
            result = {"deleted": doc.id, "title": doc.title}
            doc_store.delete_doc(s, doc)
            return _ok(result)

    return [
        browse_docs,
        search_docs,
        list_docs,
        read_doc,
        create_doc,
        update_doc,
        delete_doc,
        move_docs,
        create_collection,
        update_collection,
        delete_collection,
    ]


def build_mercury_server(project_id: int, organize_board: bool = False):
    tools = build_task_tools(project_id) + build_doc_tools(project_id)
    if organize_board:
        tools += build_organizer_task_tools(project_id)
    return create_sdk_mcp_server(name=SERVER_NAME, tools=tools)
