"""Runs Claude coding agents (via the Claude Agent SDK) against a project's repo.

Each run is an asyncio task. Everything the agent does is stored as an
AgentEvent row and pushed to any WebSocket subscribers. Tool calls that need
permission pause the run and surface an approval request in the UI.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
import warnings
from dataclasses import dataclass
from typing import Any, Callable

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolPermissionContext,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from claude_agent_sdk.types import CanUseToolShadowedWarning
from sqlmodel import Session, func, select

from .. import config
from ..db import get_engine
from ..models import (
    ACTIVE_RUN_STATUSES,
    AgentEvent,
    AgentRun,
    PermissionMode,
    Project,
    RunPurpose,
    RunStatus,
    Task,
    TaskStatus,
    utcnow,
)
from . import attachments, organizer
from .agent_memory import docs_instructions, memory_hooks
from .mercury_tools import ORGANIZER_TASK_TOOLS
from .mercury_tools import PREAPPROVED_TOOL_NAMES as MERCURY_TOOLS
from .mercury_tools import SERVER_NAME as MERCURY_SERVER
from .mercury_tools import build_mercury_server

log = logging.getLogger(__name__)

MAX_TOOL_RESULT_CHARS = 20_000

# Tools that acceptEdits mode auto-approves; used when switching modes mid-run.
EDIT_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}

# Mercury's own task and doc tools (except delete_doc) are pre-approved in every mode
# via allowed_tools, which (intentionally) bypasses can_use_tool for them; don't warn about that.
warnings.filterwarnings("ignore", message=f"can_use_tool will not be invoked for: mcp__{MERCURY_SERVER}__", category=CanUseToolShadowedWarning)

MODE_LABELS = {
    PermissionMode.plan: "Plan only",
    PermissionMode.default: "Ask for everything",
    PermissionMode.acceptEdits: "Auto-edit",
    PermissionMode.bypassPermissions: "Full auto",
}


@dataclass
class _PendingApproval:
    run_id: int
    future: asyncio.Future
    tool_name: str = ""


def _truncate(text: str, limit: int = MAX_TOOL_RESULT_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n… [truncated {len(text) - limit} chars]"


def _tool_result_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            else:
                parts.append(json.dumps(item, default=str))
        return "\n".join(parts)
    return json.dumps(content, default=str)


def serialize_message(message: Any) -> list[tuple[str, dict]]:
    """Turn an Agent SDK message into (kind, payload) events for storage and the UI."""
    events: list[tuple[str, dict]] = []
    if isinstance(message, AssistantMessage):
        parent = message.parent_tool_use_id
        for block in message.content:
            if isinstance(block, TextBlock) and block.text.strip():
                events.append(("text", {"text": block.text, "parent_tool_use_id": parent}))
            elif isinstance(block, ThinkingBlock) and block.thinking.strip():
                events.append(("thinking", {"text": block.thinking, "parent_tool_use_id": parent}))
            elif isinstance(block, ToolUseBlock):
                events.append(
                    ("tool_use", {"id": block.id, "name": block.name, "input": block.input, "parent_tool_use_id": parent})
                )
        if message.error:
            events.append(("error", {"message": str(message.error)}))
    elif isinstance(message, UserMessage):
        if isinstance(message.content, list):
            for block in message.content:
                if isinstance(block, ToolResultBlock):
                    events.append(
                        (
                            "tool_result",
                            {
                                "tool_use_id": block.tool_use_id,
                                "content": _truncate(_tool_result_text(block.content)),
                                "is_error": bool(block.is_error),
                            },
                        )
                    )
    elif isinstance(message, SystemMessage):
        if message.subtype == "init":
            data = message.data or {}
            events.append(
                (
                    "system",
                    {
                        "subtype": "init",
                        "session_id": data.get("session_id"),
                        "model": data.get("model"),
                        "cwd": data.get("cwd"),
                    },
                )
            )
    elif isinstance(message, ResultMessage):
        events.append(
            (
                "result",
                {
                    "subtype": message.subtype,
                    "is_error": message.is_error,
                    "result": message.result,
                    "num_turns": message.num_turns,
                    "duration_ms": message.duration_ms,
                    "total_cost_usd": message.total_cost_usd,
                    "session_id": message.session_id,
                },
            )
        )
    return events


def prompt_input(prompt: str, images: list[str]) -> Any:
    """What to pass to ``client.query``: the plain prompt, or a user message with image blocks."""
    if not images:
        return prompt
    content = attachments.content_blocks(prompt, images)  # read now, so a missing file fails the run up front

    async def messages():
        yield {"type": "user", "message": {"role": "user", "content": content}, "parent_tool_use_id": None}

    return messages()


def build_system_append(project: Project, task: Task | None) -> str:
    lines = [
        "You are running inside Mercury, the user's project manager and coding workspace.",
        f"Project: {project.name}",
    ]
    if project.description:
        lines.append(f"Project description: {project.description}")
    if project.github_repo:
        lines.append(f"GitHub repository: {project.github_repo}")
    if task is not None:
        lines.append(f"You are working on task #{task.id}: {task.title}")
        if task.description:
            lines.append(f"Task details:\n{task.description}")
    lines.append(
        "You can read and manage this project's tasks on the Mercury board with the "
        f"mcp__{MERCURY_SERVER}__list_tasks, create_task and update_task tools. When the user asks you to plan, "
        "break down or record work, create the tasks directly rather than giving them a list to paste. "
        "When you finish a task's work and have verified it, mark it done with update_task."
    )
    if task is not None:
        lines.append(
            f"Task #{task.id} moves to Review automatically when this run succeeds; mark it done yourself "
            "only if the user asked you to, or the work needs no review."
        )
    lines.append(
        "When you finish, end with a short summary of what you changed, what you verified, "
        "and anything the user still needs to do."
    )
    if project.id is not None:
        lines.append("\n" + docs_instructions(project.id))
    return "\n".join(lines)


def _organizer_dir():
    path = config.data_dir() / "organizer"
    path.mkdir(parents=True, exist_ok=True)
    return path


class RunManager:
    def __init__(self, client_factory: Callable[[ClaudeAgentOptions], Any] = ClaudeSDKClient):
        self.client_factory = client_factory
        self._tasks: dict[int, asyncio.Task] = {}
        self._clients: dict[int, Any] = {}
        self._subscribers: dict[int, set[asyncio.Queue]] = {}
        self._approvals: dict[str, _PendingApproval] = {}
        self._seq: dict[int, int] = {}
        self._modes: dict[int, PermissionMode] = {}  # live permission mode of active runs

    # ------------------------------------------------------------------ lifecycle

    def recover_orphans(self) -> None:
        """Runs left active by a previous process can't continue; mark them failed."""
        with Session(get_engine()) as s:
            runs = s.exec(select(AgentRun).where(AgentRun.status.in_(list(ACTIVE_RUN_STATUSES)))).all()
            for run in runs:
                run.status = RunStatus.failed
                run.error = "Mercury was closed while this run was active."
                run.finished_at = utcnow()
                s.add(run)
            s.commit()

    def start(self, run_id: int) -> None:
        self._tasks[run_id] = asyncio.create_task(self._execute(run_id), name=f"agent-run-{run_id}")

    def is_running(self, run_id: int) -> bool:
        task = self._tasks.get(run_id)
        return task is not None and not task.done()

    async def cancel(self, run_id: int) -> bool:
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        client = self._clients.get(run_id)
        if client is not None:
            try:
                await asyncio.wait_for(client.interrupt(), timeout=5)
            except Exception:  # interrupt is best-effort; cancellation below is authoritative
                log.debug("interrupt failed for run %s", run_id, exc_info=True)
        task.cancel()
        return True

    async def shutdown(self) -> None:
        for run_id in list(self._tasks):
            await self.cancel(run_id)
        pending = [t for t in self._tasks.values() if not t.done()]
        if pending:
            await asyncio.wait(pending, timeout=10)

    # ------------------------------------------------------------------ approvals

    def resolve_approval(self, run_id: int, approval_id: str, allow: bool, message: str = "") -> bool:
        pending = self._approvals.get(approval_id)
        if pending is None or pending.run_id != run_id or pending.future.done():
            return False
        pending.future.set_result((allow, message))
        return True

    def pending_approvals(self, run_id: int) -> list[str]:
        return [aid for aid, p in self._approvals.items() if p.run_id == run_id and not p.future.done()]

    async def set_permission_mode(self, run_id: int, mode: PermissionMode) -> None:
        """Switch an active run's permission mode, and resolve approvals the new mode wouldn't ask for."""
        self._modes[run_id] = mode
        client = self._clients.get(run_id)
        if client is not None and hasattr(client, "set_permission_mode"):
            try:
                await client.set_permission_mode(mode.value)
            except Exception:  # Mercury still enforces the mode in can_use_tool below
                log.warning("set_permission_mode failed for run %s", run_id, exc_info=True)
        self._update_run(run_id, permission_mode=mode)
        self._emit(run_id, "status", {"message": f"Permissions switched to {MODE_LABELS[mode]}."})
        for pending in list(self._approvals.values()):
            if pending.run_id == run_id and not pending.future.done() and self._auto_allowed(mode, pending.tool_name):
                pending.future.set_result((True, ""))

    @staticmethod
    def _auto_allowed(mode: PermissionMode, tool_name: str) -> bool:
        if tool_name in MERCURY_TOOLS:
            return True
        return mode == PermissionMode.bypassPermissions or (mode == PermissionMode.acceptEdits and tool_name in EDIT_TOOLS)

    # ------------------------------------------------------------------ pub/sub

    def subscribe(self, run_id: int) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(run_id, set()).add(q)
        return q

    def unsubscribe(self, run_id: int, q: asyncio.Queue) -> None:
        subs = self._subscribers.get(run_id)
        if subs:
            subs.discard(q)
            if not subs:
                self._subscribers.pop(run_id, None)

    def _publish(self, run_id: int, message: dict) -> None:
        for q in list(self._subscribers.get(run_id, ())):
            q.put_nowait(message)

    def _emit(self, run_id: int, kind: str, payload: dict) -> None:
        if run_id not in self._seq:
            with Session(get_engine()) as s:
                current = s.exec(select(func.max(AgentEvent.seq)).where(AgentEvent.run_id == run_id)).one()
            self._seq[run_id] = current if current is not None else -1
        self._seq[run_id] += 1
        seq = self._seq[run_id]
        with Session(get_engine()) as s:
            event = AgentEvent(run_id=run_id, seq=seq, kind=kind, payload=json.dumps(payload, default=str))
            s.add(event)
            s.commit()
            created = event.created_at
        self._publish(
            run_id, {"type": "event", "seq": seq, "kind": kind, "payload": payload, "created_at": created.isoformat()}
        )

    def _update_run(self, run_id: int, **fields: Any) -> AgentRun:
        with Session(get_engine()) as s:
            run = s.get(AgentRun, run_id)
            assert run is not None
            for key, value in fields.items():
                setattr(run, key, value)
            s.add(run)
            s.commit()
            s.refresh(run)
            self._publish(run_id, {"type": "run", "run": json.loads(run.model_dump_json())})
            return run

    def _set_task_status(self, task_id: int | None, status: TaskStatus, only_from: set[TaskStatus]) -> None:
        if task_id is None:
            return
        with Session(get_engine()) as s:
            task = s.get(Task, task_id)
            if task is not None and task.status in only_from:
                task.status = status
                task.updated_at = utcnow()
                s.add(task)
                s.commit()

    # ------------------------------------------------------------------ execution

    def _build_options(self, run: AgentRun, project: Project, task: Task | None, resume: str | None) -> ClaudeAgentOptions:
        async def can_use_tool(tool_name: str, tool_input: dict, _ctx: ToolPermissionContext):
            # Honour a mode switched mid-run even if the CLI still asks.
            if self._auto_allowed(self._modes.get(run.id, run.permission_mode), tool_name):
                return PermissionResultAllow()
            approval_id = uuid.uuid4().hex
            future: asyncio.Future = asyncio.get_running_loop().create_future()
            self._approvals[approval_id] = _PendingApproval(run.id, future, tool_name)
            self._emit(run.id, "approval_request", {"approval_id": approval_id, "tool_name": tool_name, "input": tool_input})
            self._update_run(run.id, status=RunStatus.waiting_approval)
            try:
                allow, message = await future
            finally:
                self._approvals.pop(approval_id, None)
            self._emit(run.id, "approval_decision", {"approval_id": approval_id, "allow": allow, "message": message})
            self._update_run(run.id, status=RunStatus.running)
            if allow:
                return PermissionResultAllow()
            return PermissionResultDeny(message=message or "The user denied this action in Mercury.")

        options: dict[str, Any] = {}
        organize_board = run.purpose == RunPurpose.organize_board
        mercury_tools = MERCURY_TOOLS if organize_board else MERCURY_TOOLS - ORGANIZER_TASK_TOOLS
        if run.purpose == RunPurpose.work:
            cwd: str = project.repo_path  # type: ignore[assignment]  (checked in _execute)
            system_prompt: Any = {"type": "preset", "preset": "claude_code", "append": build_system_append(project, task)}
            allowed = sorted(mercury_tools)
        else:
            # Organizer runs only read the repo (if any); without one they need just an empty folder to run in.
            cwd = project.repo_path or str(_organizer_dir())
            system_prompt = organizer.system_prompt(project, run.purpose)
            read_only = organizer.builtin_tools(project)
            options["tools"] = read_only
            options["disallowed_tools"] = organizer.disallowed_tools(run.purpose)
            allowed = sorted(mercury_tools | set(read_only))  # nothing asks, so the run never waits on the user

        return ClaudeAgentOptions(
            cwd=cwd,
            model=run.model or config.DEFAULT_AGENT_MODEL,
            permission_mode=run.permission_mode.value,
            system_prompt=system_prompt,
            resume=resume,
            max_budget_usd=run.max_budget_usd,
            can_use_tool=can_use_tool,
            mcp_servers={
                MERCURY_SERVER: build_mercury_server(project.id, organize_board=organize_board)
            },
            allowed_tools=allowed,
            hooks=memory_hooks(),  # Claude Code memory is read-only in Mercury runs
            **options,
        )

    async def _execute(self, run_id: int) -> None:
        with Session(get_engine()) as s:
            run = s.get(AgentRun, run_id)
            assert run is not None
            project = s.get(Project, run.project_id)
            task = s.get(Task, run.task_id) if run.task_id else None
            parent = s.get(AgentRun, run.parent_run_id) if run.parent_run_id else None
            s.expunge_all()

        self._modes[run_id] = run.permission_mode
        resume = parent.session_id if parent else None
        self._update_run(run_id, status=RunStatus.running, started_at=utcnow())
        self._set_task_status(run.task_id, TaskStatus.in_progress, {TaskStatus.backlog, TaskStatus.todo})
        final: dict[str, Any] = {}

        try:
            if not project:
                raise RuntimeError("This project no longer exists.")
            if run.purpose == RunPurpose.work and not project.repo_path:
                raise RuntimeError("This project has no local repository folder. Set one in project settings.")
            options = self._build_options(run, project, task, resume)
            client = self.client_factory(options)
            self._clients[run_id] = client
            async with client:
                await client.query(prompt_input(run.prompt, run.images))
                async for message in client.receive_response():
                    for kind, payload in serialize_message(message):
                        self._emit(run_id, kind, payload)
                        if kind == "system" and payload.get("session_id"):
                            self._update_run(run_id, session_id=payload["session_id"])
                    if isinstance(message, ResultMessage):
                        final = {
                            "status": RunStatus.failed if message.is_error else RunStatus.succeeded,
                            "result": message.result,
                            "cost_usd": message.total_cost_usd,
                            "num_turns": message.num_turns,
                            "session_id": message.session_id,
                        }
                        if message.is_error:
                            final["error"] = message.result or message.subtype
            if not final:
                final = {"status": RunStatus.failed, "error": "The agent ended without a result."}
            self._update_run(run_id, finished_at=utcnow(), **final)
            if final["status"] == RunStatus.succeeded:
                self._set_task_status(run.task_id, TaskStatus.review, {TaskStatus.in_progress})
        except asyncio.CancelledError:
            self._emit(run_id, "status", {"message": "Run cancelled."})
            self._update_run(run_id, status=RunStatus.cancelled, finished_at=utcnow())
        except Exception as exc:  # surface any failure on the run instead of crashing the server
            log.exception("agent run %s failed", run_id)
            self._emit(run_id, "error", {"message": str(exc)})
            self._update_run(run_id, status=RunStatus.failed, error=str(exc), finished_at=utcnow())
        finally:
            self._clients.pop(run_id, None)
            self._seq.pop(run_id, None)
            self._modes.pop(run_id, None)
            for aid in self.pending_approvals(run_id):
                self._approvals.pop(aid, None)
