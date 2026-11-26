"""Runs Claude coding agents (via the Claude Agent SDK) against a project's repo.

Each run is an asyncio task. Everything the agent does is stored as an
AgentEvent row and pushed to any WebSocket subscribers. Tool calls that need
permission pause the run and surface an approval request in the UI. So does
a question for the user (the AskUserQuestion tool), in every permission mode.

The user can send more messages while a work run is going (``send_message``).
They go straight to the Claude CLI, which takes each one in after the current
tool call, or starts another turn for it if the current one ends first. So one
run can span several turns, each with its own result.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
import warnings
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

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
from . import attachments, claude_usage, organizer, refs, scratch
from .agent_memory import docs_instructions, memory_hooks, scratch_docs_instructions
from .mercury_tools import ORGANIZER_TASK_TOOLS, SETTINGS_TOOLS
from .mercury_tools import PREAPPROVED_TOOL_NAMES as MERCURY_TOOLS
from .mercury_tools import SERVER_NAME as MERCURY_SERVER
from .mercury_tools import build_mercury_server, precheck

log = logging.getLogger(__name__)

MAX_TOOL_RESULT_CHARS = 20_000
# The most the SDK will read as one line from the CLI. Its 1 MB default is too small for the CLI's echo
# of a message with several images (each is some hundreds of KB even after the CLI shrinks it).
MAX_CLI_LINE_BYTES = 64 * 1024 * 1024
# After a turn ends with queued messages still waiting, how long to wait for the CLI to start on them.
NEXT_TURN_TIMEOUT = 30.0

# Tools that acceptEdits mode auto-approves; used when switching modes mid-run.
EDIT_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}

# The CLI's tool for asking the user multiple-choice questions. It reaches can_use_tool even in Full auto,
# and the answers go back inside the tool's input: allowing it unchanged tells the agent nobody answered.
QUESTION_TOOL = "AskUserQuestion"

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


@dataclass
class _QueuedMessage:
    prompt: str
    images: list[str]
    written: bool = False  # handed to the Claude CLI


@dataclass
class _Inbox:
    """Messages the user sends to a run while it works, until the agent takes them in."""

    queued: dict[str, _QueuedMessage] = field(default_factory=dict)  # message uuid -> message, oldest first
    client: Any = None  # set once the run's own prompt has gone out
    closed: bool = False  # the run is ending, so it takes no more messages
    writing: asyncio.Lock = field(default_factory=asyncio.Lock)  # keeps messages in order

    def payload(self, message_id: str) -> dict:
        message = self.queued[message_id]
        return {"message_id": message_id, "prompt": message.prompt, "images": message.images}


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
            elif isinstance(item, dict) and item.get("type") == "image":
                parts.append("[image]")  # keep base64 out of the transcript
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


def prompt_input(prompt: str, images: list[str], message_id: str | None = None) -> Any:
    """What to pass to ``client.query``: the plain prompt, or a user message dict.

    Images need the dict form. So does a message sent mid-run (``message_id``): the CLI reports that
    uuid back when the agent takes the message in.
    """
    if not images and message_id is None:
        return prompt
    # read now, so a missing file fails the run up front
    content = attachments.content_blocks(prompt, images) if images else prompt
    message = {"type": "user", "message": {"role": "user", "content": content}, "parent_tool_use_id": None}
    if message_id is not None:
        message |= {"uuid": message_id, "origin": {"kind": "human"}}  # from the user, not an automated source

    async def messages():
        yield message

    return messages()


async def _turn(client: Any, start_within: float | None) -> AsyncIterator[Any]:
    """One turn's messages, up to its result. With ``start_within``, none if the turn doesn't start in time."""
    messages = client.receive_response().__aiter__()
    if start_within is not None:
        try:
            yield await asyncio.wait_for(messages.__anext__(), start_within)
        except (asyncio.TimeoutError, StopAsyncIteration):
            return
    async for message in messages:
        yield message


def build_system_append(project: Project, task: Task | None) -> str:
    lines = [
        "You are running inside Mercury, the user's project manager and coding workspace.",
        f"Project: {project.name}",
    ]
    if project.scratch:
        lines.append(
            "This is a scratch session: it doesn't belong to any of the user's projects, and may be about anything. "
            f"Your working folder ({project.repo_path}) is a scratch space rather than a project's codebase. All "
            "scratch sessions share it, so don't change or delete files this session didn't create unless the user "
            "asks. The board and docs described below are Scratch's own."
        )
    if project.description:
        lines.append(f"Project description: {project.description}")
    if project.github_repo:
        lines.append(f"GitHub repository: {project.github_repo}")
    if task is not None:
        lines.append(f"You are working on task {refs.task_ref(project.key, task.number)}: {task.title}")
        if task.description:
            lines.append(f"Task details:\n{task.description}")
        if task.images:
            n = len(task.images)
            lines.append(
                f"The task has {n} picture{'s' if n != 1 else ''} attached (screenshots and the like). A session "
                "started on the task gets them with its first message, before the text; to look at them again, call "
                f"mcp__{MERCURY_SERVER}__view_task_images."
            )
    lines.append(
        f"Tasks and docs have per-project ids: {project.key}-12 is task 12 and {project.key}-D3 is doc 3 of this project. "
        "You can read and manage this project's tasks on the Mercury board with the "
        f"mcp__{MERCURY_SERVER}__list_tasks, create_task and update_task tools (view_task_images shows a task's pictures). When the user asks you to plan, "
        "break down or record work, create the tasks directly rather than giving them a list to paste. "
        "When you finish a task's work and have verified it, mark it done with update_task."
    )
    if task is not None:
        lines.append(
            f"Task {refs.task_ref(project.key, task.number)} moves to Review automatically when this run succeeds; mark it done yourself "
            "only if the user asked you to, or the work needs no review."
        )
    lines.append(
        "When you finish, end with a short summary of what you changed, what you verified, "
        "and anything the user still needs to do."
    )
    lines.append(
        f"Mercury's app-wide settings (the user's Settings page) can be read with mcp__{MERCURY_SERVER}__get_settings "
        "and changed with update_settings, as far as the user allows. Only change them when the user asks you to. "
        "Mercury's look (the Appearance page) works the same way: read it with get_appearance; at the user's "
        "request, make and switch to new themes with create_theme, change or delete the user's own themes with "
        "update_theme and delete_theme (system themes can't be changed), and switch theme, colors, fonts or "
        "corners with set_appearance."
    )
    if project.id is not None:
        docs = scratch_docs_instructions if project.scratch else docs_instructions
        lines.append("\n" + docs(project.id))
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
        self._inboxes: dict[int, _Inbox] = {}
        self._background: set[asyncio.Task] = set()  # work that outlives the run that started it

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
        self._inboxes[run_id] = _Inbox()  # now, so messages can be queued while the run is still starting
        self._tasks[run_id] = asyncio.create_task(self._execute(run_id), name=f"agent-run-{run_id}")

    def pending_tool(self, run_id: int) -> str | None:
        """The tool a run is waiting for the user to approve, if any."""
        return next((p.tool_name for p in self._approvals.values() if p.run_id == run_id), None)

    def is_running(self, run_id: int) -> bool:
        task = self._tasks.get(run_id)
        return task is not None and not task.done()

    async def cancel(self, run_id: int) -> bool:
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        inbox = self._inboxes.get(run_id)
        queued = bool(inbox and inbox.queued)
        if inbox is not None:
            inbox.closed = True
        client = self._clients.get(run_id)
        if client is not None:
            await self._interrupt(run_id, client, withdraw_queued=queued)
        task.cancel()
        return True

    async def wait(self, run_id: int, timeout: float = 10) -> None:
        """Wait (a while) for a run that's ending to be recorded as finished."""
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            await asyncio.wait([task], timeout=timeout)

    @staticmethod
    async def _interrupt(run_id: int, client: Any, withdraw_queued: bool) -> None:
        """Stop the current turn. ``withdraw_queued`` also drops messages the CLI still holds, which it
        would otherwise start on next."""
        # The SDK has no public call that withdraws queued messages (checked in 0.2.188).
        control = getattr(client, "_query", None) if withdraw_queued else None
        try:
            if control is not None:
                request = control._send_control_request({"subtype": "interrupt", "cancel_queued": True})
            else:
                request = client.interrupt()
            await asyncio.wait_for(request, timeout=5)
        except Exception:  # best-effort; ending the run is what counts
            log.debug("interrupt failed for run %s", run_id, exc_info=True)

    async def shutdown(self) -> None:
        for run_id in list(self._tasks):
            await self.cancel(run_id)
        pending = [t for t in self._tasks.values() if not t.done()]
        if pending:
            await asyncio.wait(pending, timeout=10)

    # ------------------------------------------------------------------ messages sent mid-run

    async def send_message(self, run_id: int, prompt: str, images: list[str]) -> str | None:
        """Queue a message for a working run; returns its id, or None if the run takes no more messages.

        A "queued_message" event records it now, and a "user_message" event with the same id marks where
        the agent took it in. One without the other, on a finished run, was never read.
        """
        inbox = self._inboxes.get(run_id)
        if inbox is None or inbox.closed:
            return None
        message_id = str(uuid.uuid4())
        inbox.queued[message_id] = _QueuedMessage(prompt, images)
        self._emit(run_id, "queued_message", inbox.payload(message_id))
        await self._write_queued(run_id, inbox)
        return message_id

    async def _write_queued(self, run_id: int, inbox: _Inbox) -> None:
        """Hand the CLI the queued messages it doesn't have yet, oldest first."""
        async with inbox.writing:
            if inbox.client is None:
                return  # the run sends them once it's under way
            for message_id, message in list(inbox.queued.items()):
                if inbox.closed:
                    return
                if message.written:
                    continue
                try:
                    await inbox.client.query(prompt_input(message.prompt, message.images, message_id))
                except Exception:  # the connection is going down; the message stays unread
                    log.warning("couldn't send a queued message to run %s", run_id, exc_info=True)
                    return
                message.written = True

    def _take_in(self, run_id: int, inbox: _Inbox, message_ids: list[str | None]) -> None:
        """Record that the agent has read these messages (ids that aren't queued messages are ignored)."""
        for message_id in message_ids:
            if message_id in inbox.queued:
                self._emit(run_id, "user_message", inbox.payload(message_id))
                del inbox.queued[message_id]

    # ------------------------------------------------------------------ approvals

    def resolve_approval(
        self, run_id: int, approval_id: str, allow: bool, message: str = "", answers: dict[str, str] | None = None
    ) -> bool:
        """Settle a pending approval. ``answers`` (question text -> answer) is how a question is allowed."""
        pending = self._approvals.get(approval_id)
        if pending is None or pending.run_id != run_id or pending.future.done():
            return False
        pending.future.set_result((allow, message, answers))
        return True

    def awaits_answers(self, approval_id: str) -> bool:
        pending = self._approvals.get(approval_id)
        return pending is not None and pending.tool_name == QUESTION_TOOL

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
                pending.future.set_result((True, "", None))

    @staticmethod
    def _auto_allowed(mode: PermissionMode, tool_name: str) -> bool:
        if tool_name == QUESTION_TOOL:
            return False  # only the user can answer, whatever the mode
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

    @staticmethod
    def _session_cost_before(run: AgentRun, session_id: str | None) -> float:
        """The session's running total as of the nearest earlier run in this chain that recorded one.

        Runs that were cancelled or crashed record no total; their spend lands on the next run."""
        parent_id = run.parent_run_id
        with Session(get_engine()) as s:
            while parent_id is not None:
                parent = s.get(AgentRun, parent_id)
                if parent is None:
                    break
                if parent.session_cost_usd is not None and parent.session_id == session_id:
                    return parent.session_cost_usd
                parent_id = parent.parent_run_id
        return 0.0

    def _refresh_usage(self) -> None:
        """A session just used some of the plan: get a new reading, without holding anything up."""

        async def refresh() -> None:
            try:
                await asyncio.to_thread(claude_usage.get_usage, refresh=True)
            except Exception:  # only the usage gauge depends on it
                log.warning("couldn't refresh Claude usage", exc_info=True)

        task = asyncio.create_task(refresh())
        self._background.add(task)  # the event loop alone doesn't keep a task alive
        task.add_done_callback(self._background.discard)

    # ------------------------------------------------------------------ execution

    def _build_options(self, run: AgentRun, project: Project, task: Task | None, resume: str | None) -> ClaudeAgentOptions:
        async def can_use_tool(tool_name: str, tool_input: dict, _ctx: ToolPermissionContext):
            # Don't ask the user to approve a call that would fail anyway (e.g. settings agents may not change).
            problem = precheck(tool_name, tool_input)
            if problem:
                return PermissionResultDeny(message=problem)
            # Honour a mode switched mid-run even if the CLI still asks.
            if self._auto_allowed(self._modes.get(run.id, run.permission_mode), tool_name):
                return PermissionResultAllow()
            approval_id = uuid.uuid4().hex
            future: asyncio.Future = asyncio.get_running_loop().create_future()
            self._approvals[approval_id] = _PendingApproval(run.id, future, tool_name)
            self._emit(run.id, "approval_request", {"approval_id": approval_id, "tool_name": tool_name, "input": tool_input})
            self._update_run(run.id, status=RunStatus.waiting_approval)
            try:
                allow, message, answers = await future
            finally:
                self._approvals.pop(approval_id, None)
            decision = {"approval_id": approval_id, "tool_name": tool_name, "allow": allow, "message": message}
            if answers:
                decision["answers"] = answers
            self._emit(run.id, "approval_decision", decision)
            self._update_run(run.id, status=RunStatus.running)
            if allow:
                return PermissionResultAllow(updated_input={**tool_input, "answers": answers} if answers else None)
            denied = "The user denied this action in Mercury."
            if tool_name == QUESTION_TOOL:
                denied = "The user dismissed the question in Mercury without answering it."
            return PermissionResultDeny(message=message or denied)

        options: dict[str, Any] = {}
        if run.purpose == RunPurpose.work:
            # The CLI echoes each message as the agent takes it in, which places queued ones in the transcript.
            options["extra_args"] = {"replay-user-messages": None}
            options["max_buffer_size"] = MAX_CLI_LINE_BYTES
        organize_board = run.purpose == RunPurpose.organize_board
        mercury_tools = MERCURY_TOOLS if organize_board else MERCURY_TOOLS - ORGANIZER_TASK_TOOLS
        if run.purpose != RunPurpose.work:
            mercury_tools -= SETTINGS_TOOLS  # organizers tidy the board or docs, nothing else
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
                MERCURY_SERVER: build_mercury_server(
                    project.id, organize_board=organize_board, settings=run.purpose == RunPurpose.work
                )
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
        inbox = self._inboxes.setdefault(run_id, _Inbox())

        try:
            if not project:
                raise RuntimeError("This project no longer exists.")
            if run.purpose == RunPurpose.work and not project.repo_path:
                raise RuntimeError("This project has no local repository folder. Set one in project settings.")
            scratch.prepare_folder(project)
            options = self._build_options(run, project, task, resume)
            client = self.client_factory(options)
            self._clients[run_id] = client
            async with client:
                await client.query(prompt_input(run.prompt, run.images))
                inbox.client = client
                await self._write_queued(run_id, inbox)
                final = await self._receive(run, client, inbox)
            self._update_run(run_id, finished_at=utcnow(), **final)
            if final["status"] == RunStatus.succeeded:
                self._set_task_status(run.task_id, TaskStatus.review, {TaskStatus.in_progress})
                self._refresh_usage()
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
            inbox.closed = True
            self._inboxes.pop(run_id, None)
            for aid in self.pending_approvals(run_id):
                self._approvals.pop(aid, None)

    async def _receive(self, run: AgentRun, client: Any, inbox: _Inbox) -> dict[str, Any]:
        """Record the agent's turns until it has answered everything it was sent.

        Returns the fields that finish the run. Closes the inbox.
        """
        final: dict[str, Any] = {}
        cost: float | None = None  # this run's share, over all its turns
        session_total: float | None = None  # the session's running total, as of the latest turn
        num_turns = 0
        initialised = False
        start_within: float | None = None  # the first turn may take as long as it likes to start

        while True:
            result: ResultMessage | None = None
            heard = False
            async for message in _turn(client, start_within):
                heard = True
                turn_cost = None
                if isinstance(message, UserMessage):
                    self._take_in(run.id, inbox, [message.uuid])
                elif isinstance(message, ResultMessage):
                    result = message
                    self._take_in(run.id, inbox, list(message.user_message_uuids or []))
                    if message.total_cost_usd is not None:
                        # total_cost_usd is the resumed session's running total, not this turn's.
                        before = session_total
                        if before is None:
                            before = self._session_cost_before(run, message.session_id)
                        turn_cost = max(0.0, message.total_cost_usd - before)
                        cost = (cost or 0.0) + turn_cost
                        session_total = message.total_cost_usd
                for kind, payload in serialize_message(message):
                    if kind == "system":
                        if initialised:
                            continue  # the CLI re-announces the session at the start of every turn
                        initialised = True
                    elif kind == "result":
                        payload["cost_usd"] = turn_cost
                    self._emit(run.id, kind, payload)
                    if kind == "system" and payload.get("session_id"):
                        self._update_run(run.id, session_id=payload["session_id"])

            if result is None:
                if heard or not final:
                    final |= {"status": RunStatus.failed, "error": "The agent ended without a result."}
                break  # else the CLI never started on what was still queued; the last turn's outcome stands
            num_turns += result.num_turns
            progress = {
                "result": result.result,
                "cost_usd": cost,
                "session_cost_usd": session_total,
                "num_turns": num_turns,
                "session_id": result.session_id,
            }
            final = {"status": RunStatus.failed if result.is_error else RunStatus.succeeded, **progress}
            if result.is_error:
                final["error"] = result.result or result.subtype
            if result.is_error or not inbox.queued:
                break
            self._update_run(run.id, **progress)  # kept even if the user stops the next turn
            start_within = NEXT_TURN_TIMEOUT

        withdraw = bool(inbox.queued) and not inbox.closed  # a Stop has already withdrawn them
        inbox.closed = True
        if withdraw:
            await self._interrupt(run.id, client, withdraw_queued=True)
        return final
