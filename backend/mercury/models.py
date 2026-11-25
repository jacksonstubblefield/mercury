"""Database tables and API schemas."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Optional

from pydantic import field_validator
from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- enums


class ProjectStatus(str, Enum):
    active = "active"
    paused = "paused"
    archived = "archived"


class TaskStatus(str, Enum):
    backlog = "backlog"
    todo = "todo"
    in_progress = "in_progress"
    review = "review"
    done = "done"


class TaskPriority(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    urgent = "urgent"


class RunStatus(str, Enum):
    queued = "queued"
    running = "running"
    waiting_approval = "waiting_approval"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


ACTIVE_RUN_STATUSES = {RunStatus.queued, RunStatus.running, RunStatus.waiting_approval}


class RunPurpose(str, Enum):
    """What a run is for. Organizer runs tidy the board or the docs and never touch the repo."""

    work = "work"  # a coding session (the default)
    organize_board = "organize_board"
    organize_docs = "organize_docs"


class PermissionMode(str, Enum):
    """Subset of Claude Agent SDK permission modes exposed in the UI."""

    plan = "plan"  # read-only: agent investigates and proposes a plan
    default = "default"  # every edit / command needs approval in Mercury
    acceptEdits = "acceptEdits"  # file edits auto-approved, commands need approval
    bypassPermissions = "bypassPermissions"  # no approvals at all


# --------------------------------------------------------------------------- projects


class ProjectBase(SQLModel):
    name: str
    description: str = ""
    repo_path: Optional[str] = None  # local checkout the agent works in
    github_repo: Optional[str] = None  # "owner/name"
    status: ProjectStatus = ProjectStatus.active
    color: str = "#7c8cff"
    # Preselected for new agent sessions in this project. None: the app-wide default (Preferences).
    default_permission_mode: Optional[PermissionMode] = None


class Project(ProjectBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ProjectCreate(ProjectBase):
    pass


class ProjectUpdate(SQLModel):
    name: Optional[str] = None
    description: Optional[str] = None
    repo_path: Optional[str] = None
    github_repo: Optional[str] = None
    status: Optional[ProjectStatus] = None
    color: Optional[str] = None
    default_permission_mode: Optional[PermissionMode] = None  # send null to use the app default


class ProjectRead(ProjectBase):
    id: int
    created_at: datetime
    updated_at: datetime
    open_tasks: int = 0
    active_runs: int = 0


# --------------------------------------------------------------------------- tasks


class TaskBase(SQLModel):
    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.todo
    priority: TaskPriority = TaskPriority.medium
    position: float = 0


class Task(TaskBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True, ondelete="CASCADE")
    github_issue_number: Optional[int] = None
    github_url: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class TaskCreate(TaskBase):
    pass


class TaskUpdate(SQLModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[TaskStatus] = None
    priority: Optional[TaskPriority] = None
    position: Optional[float] = None


# --------------------------------------------------------------------------- docs


class DocStatus(str, Enum):
    current = "current"
    draft = "draft"
    superseded = "superseded"  # replaced by superseded_by; ranked low in search
    archived = "archived"  # kept for the record; ranked low in search


class DocKind(str, Enum):
    doc = "doc"  # found through search
    rules = "rules"  # always in agents' instructions: how to work on this project


class DocOrigin(str, Enum):
    user = "user"
    agent = "agent"
    import_ = "import"


class DocBase(SQLModel):
    title: str = "Untitled"
    body: str = ""
    pinned: bool = False
    status: DocStatus = DocStatus.current
    kind: DocKind = DocKind.doc
    superseded_by: Optional[int] = None
    collection_id: Optional[int] = Field(default=None, index=True)  # None: unfiled


class Doc(DocBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True, ondelete="CASCADE")
    origin: DocOrigin = DocOrigin.user
    # content_hash() of title + body, kept current by services/doc_store.save_doc.
    content_hash: str = ""
    # A short description written for agents, and the content_hash it describes; when the
    # two differ the summary is stale and the summary worker rewrites it.
    summary: str = ""
    summary_hash: str = ""
    # Where an imported doc came from (e.g. "claude-memory:notes.md") and the content_hash() at import.
    source_key: Optional[str] = Field(default=None, index=True)
    source_hash: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class DocCollectionBase(SQLModel):
    name: str
    # What belongs here, written by a person or an agent; guides filing.
    description: str = ""
    # Collections nest at most two levels: a parent must be a top-level collection.
    parent_id: Optional[int] = None


class DocCollection(DocCollectionBase, table=True):
    """A folder of docs. Its summary, written by Mercury from the docs' summaries, orients agents."""

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True, ondelete="CASCADE")
    summary: str = ""
    # Hash of the contents the summary was written from (see services/doc_collections.py).
    summary_hash: str = ""
    # Set when an import created it (e.g. "claude-memory:roadmap.md"), so undoing the import can remove it.
    source_key: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class DocCollectionRead(DocCollectionBase):
    id: int
    project_id: int
    summary: str
    summary_stale: bool
    doc_count: int  # docs directly in it
    created_at: datetime
    updated_at: datetime


class DocCollectionCreate(DocCollectionBase):
    pass


class DocCollectionUpdate(SQLModel):
    name: Optional[str] = None
    description: Optional[str] = None
    parent_id: Optional[int] = None
    summary: Optional[str] = None


class DocSection(SQLModel, table=True):
    """A doc split at its Markdown headings: what search indexes and read_doc can read alone.

    Derived from Doc.body by services/doc_index.py; rebuilt whenever the doc changes.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    doc_id: int = Field(foreign_key="doc.id", index=True, ondelete="CASCADE")
    position: int
    heading: str = ""  # heading path, e.g. "Design › Storage"; "" for text before the first heading
    start_line: int  # 1-based, inclusive
    end_line: int
    text: str
    # Hash of the text this section is embedded as; Embedding rows are looked up by it,
    # so unchanged sections keep their vectors when a doc is re-saved.
    embed_hash: str = Field(default="", index=True)


class Embedding(SQLModel, table=True):
    """A section's vector for meaning-based search, cached by the hash of its text."""

    id: Optional[int] = Field(default=None, primary_key=True)
    model: str = Field(index=True)
    text_hash: str = Field(index=True)
    vector: bytes  # float32, L2-normalized


class MercuryMeta(SQLModel, table=True):
    """Small key/value store for Mercury's own bookkeeping (e.g. the doc index version)."""

    key: str = Field(primary_key=True)
    value: str = ""


class DocRead(DocBase):
    id: int
    project_id: int
    origin: DocOrigin
    summary: str
    summary_stale: bool
    source_key: Optional[str]
    created_at: datetime
    updated_at: datetime


class DocListItem(SQLModel):
    id: int
    title: str
    pinned: bool
    status: DocStatus
    kind: DocKind
    origin: DocOrigin
    collection_id: Optional[int]
    updated_at: datetime


class DocCreate(DocBase):
    summary: str = ""


class DocUpdate(SQLModel):
    title: Optional[str] = None
    body: Optional[str] = None
    pinned: Optional[bool] = None
    status: Optional[DocStatus] = None
    kind: Optional[DocKind] = None
    superseded_by: Optional[int] = None
    collection_id: Optional[int] = None
    summary: Optional[str] = None


class DocMove(SQLModel):
    doc_ids: list[int]
    collection_id: Optional[int] = None  # None: unfiled


# --------------------------------------------------------------------------- agent runs


class AgentRunBase(SQLModel):
    prompt: str
    task_id: Optional[int] = None
    model: Optional[str] = None
    permission_mode: PermissionMode = PermissionMode.acceptEdits
    max_budget_usd: Optional[float] = None


class AgentRun(AgentRunBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True, ondelete="CASCADE")
    parent_run_id: Optional[int] = None  # follow-up of another run
    purpose: RunPurpose = RunPurpose.work  # follow-ups keep their parent's
    status: RunStatus = RunStatus.queued
    session_id: Optional[str] = None  # Claude session id, used to resume / follow up
    # Pasted images sent with the prompt: attachment names (see services/attachments.py).
    images: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False, server_default="[]"))
    result: Optional[str] = None
    error: Optional[str] = None
    cost_usd: Optional[float] = None
    num_turns: Optional[int] = None
    created_at: datetime = Field(default_factory=utcnow)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class AgentRunCreate(AgentRunBase):
    permission_mode: Optional[PermissionMode] = None  # None: the project's default, else the app's
    images: list[str] = []  # base64 PNG/JPEG/GIF/WebP; the type is read from the bytes


class OrganizeRequest(SQLModel):
    target: Literal["board", "docs"]


class AgentFollowUp(SQLModel):
    prompt: str
    permission_mode: Optional[PermissionMode] = None
    images: list[str] = []  # base64, as in AgentRunCreate


class PermissionModeUpdate(SQLModel):
    permission_mode: PermissionMode


class AgentEvent(SQLModel, table=True):
    """One streamed item from a run (text, tool call, tool result, ...)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="agentrun.id", index=True, ondelete="CASCADE")
    seq: int
    kind: str
    payload: str  # JSON
    created_at: datetime = Field(default_factory=utcnow)


class ApprovalDecision(SQLModel):
    allow: bool
    message: str = ""


# --------------------------------------------------------------------------- source control


class ScmPaths(SQLModel):
    """Files to stage, unstage or discard. `None` means every file in that group."""

    paths: Optional[list[str]] = None


class ScmCommit(SQLModel):
    message: str
    # Stage every change first when nothing is staged (like VS Code's "smart commit").
    stage_all: bool = False
    push: bool = False


# --------------------------------------------------------------------------- Claude Code usage


class UsageWindow(SQLModel):
    """One plan limit, e.g. the 5-hour session window."""

    key: str  # five_hour, seven_day, seven_day_opus, seven_day_sonnet
    label: str
    utilization: float  # percent of the limit used, 0-100
    resets_at: Optional[datetime] = None
    window_seconds: int


class ClaudeUsage(SQLModel):
    # signed_out: no Claude subscription sign-in found; expired: the sign-in needs renewing in Claude Code.
    status: Literal["ok", "signed_out", "expired", "error"]
    message: Optional[str] = None
    plan: Optional[str] = None  # pro, max, team, ...
    windows: list[UsageWindow] = []
    fetched_at: Optional[datetime] = None


# --------------------------------------------------------------------------- appearance

_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}")
_COLOR_TOKEN = re.compile(r"--[a-z0-9-]{1,40}")
_PRESET_ID = re.compile(r"[a-z0-9-]{1,40}")


class Appearance(SQLModel):
    """The UI theme: a built-in preset plus the user's changes. Kept in appearance.json, not the database.

    The frontend owns the token names and presets (`frontend/src/theme.ts`); the backend only checks shapes.
    """

    preset: str = "mercury-dark"
    colors: dict[str, str] = Field(default_factory=dict)  # CSS custom property -> #rrggbb
    font_ui: Optional[str] = Field(default=None, max_length=200)
    font_mono: Optional[str] = Field(default=None, max_length=200)
    radius: Optional[int] = Field(default=None, ge=0, le=16)

    @field_validator("preset")
    @classmethod
    def _check_preset(cls, value: str) -> str:
        if not _PRESET_ID.fullmatch(value):
            raise ValueError("preset ids are lower-case letters, digits and dashes")
        return value

    @field_validator("colors")
    @classmethod
    def _check_colors(cls, colors: dict[str, str]) -> dict[str, str]:
        if len(colors) > 64:
            raise ValueError("too many colors")
        for key, value in colors.items():
            if not _COLOR_TOKEN.fullmatch(key):
                raise ValueError(f"not a color token: {key!r}")
            if not _HEX_COLOR.fullmatch(value):
                raise ValueError(f"{key} must be a #rrggbb color, got {value!r}")
        return {k: v.lower() for k, v in colors.items()}

    @field_validator("font_ui", "font_mono")
    @classmethod
    def _check_font(cls, value: Optional[str]) -> Optional[str]:
        # The value lands in a CSS font-family; keep it to names, commas and quotes.
        if value is None or not value.strip():
            return None
        if re.search(r"[;{}<>\\]", value):
            raise ValueError("font names can't contain ; { } < > or \\")
        return value.strip()


# --------------------------------------------------------------------------- preferences


class Preferences(SQLModel):
    """App-wide user preferences. Kept in preferences.json next to the database, like `Appearance`."""

    # For new agent sessions in projects that don't set their own `default_permission_mode`.
    default_permission_mode: PermissionMode = PermissionMode.acceptEdits
