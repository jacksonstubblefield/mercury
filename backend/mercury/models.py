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


class AgentModel(str, Enum):
    """Claude models offered for agent sessions. Mirror the list in frontend/src/agentModels.ts."""

    opus_5_5 = "claude-opus-5-5"
    sonnet_5_5 = "claude-sonnet-5-5"
    haiku_5_5 = "claude-haiku-5-5"
    fable_5_2 = "claude-fable-5-2"


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
    # Preselected model for new agent sessions in this project. None: the app-wide default (Preferences).
    default_model: Optional[AgentModel] = None


class Project(ProjectBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    # Unique, 2-4 characters (services/refs.py): the prefix of the project's task and doc ids, e.g. MER-12.
    # Generated from the name when a project is created without one.
    key: Optional[str] = Field(default=None, max_length=4)
    # The last task / doc number handed out in this project; numbers aren't reused.
    task_seq: int = 0
    doc_seq: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ProjectCreate(ProjectBase):
    key: Optional[str] = None  # None: generated from the name


class ProjectUpdate(SQLModel):
    name: Optional[str] = None
    key: Optional[str] = None
    description: Optional[str] = None
    repo_path: Optional[str] = None
    github_repo: Optional[str] = None
    status: Optional[ProjectStatus] = None
    color: Optional[str] = None
    default_permission_mode: Optional[PermissionMode] = None  # send null to use the app default
    default_model: Optional[AgentModel] = None  # send null to use the app default


class ProjectRead(ProjectBase):
    id: int
    key: str
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
    # Per-project id, shown as "<project key>-<number>" (services/refs.py assigns it on insert).
    number: Optional[int] = None
    github_issue_number: Optional[int] = None
    github_url: Optional[str] = None
    # Pictures attached to the task (attachment names, see services/attachments.py). A run started on the
    # task gets its own copies, sent with its first message.
    images: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False, server_default="[]"))
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class TaskCreate(TaskBase):
    add_images: list[str] = []  # base64 PNG/JPEG/GIF/WebP; the type is read from the bytes


class TaskUpdate(SQLModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[TaskStatus] = None
    priority: Optional[TaskPriority] = None
    position: Optional[float] = None
    # The task's pictures to keep, in order (names from Task.images); ones left out are deleted.
    images: Optional[list[str]] = None
    add_images: list[str] = []  # new pictures, base64 as in TaskCreate, appended after the kept ones


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
    # Per-project id, shown as "<project key>-D<number>" (services/refs.py assigns it on insert).
    number: Optional[int] = None
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
    number: int
    origin: DocOrigin
    summary: str
    summary_stale: bool
    source_key: Optional[str]
    created_at: datetime
    updated_at: datetime


class DocListItem(SQLModel):
    id: int
    number: int
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
    # The Claude model id. A str rather than AgentModel so runs made with models since dropped from the
    # list still load; None on runs from before models were chosen (they ran on config.DEFAULT_AGENT_MODEL).
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
    # This run's own cost. The SDK reports a resumed session's running total (total_cost_usd), so a
    # follow-up's cost is that total minus the nearest earlier run's (session_cost_usd); see agent_runner.
    cost_usd: Optional[float] = None
    session_cost_usd: Optional[float] = None  # the session's running total as the SDK reported it
    num_turns: Optional[int] = None
    created_at: datetime = Field(default_factory=utcnow)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    # Whether the user has looked at this run since it finished. Unseen finished work runs stay in the
    # sidebar's Sessions list until opened. Rows from before this column existed count as seen (db.py).
    seen: bool = False


class AgentRunCreate(AgentRunBase):
    permission_mode: Optional[PermissionMode] = None  # None: the project's default, else the app's
    model: Optional[AgentModel] = None  # None: the project's default, else the app's
    images: list[str] = []  # base64 PNG/JPEG/GIF/WebP; the type is read from the bytes


class OrganizeRequest(SQLModel):
    target: Literal["board", "docs"]


class AgentFollowUp(SQLModel):
    prompt: str
    permission_mode: Optional[PermissionMode] = None
    model: Optional[AgentModel] = None  # None: keep the session's model
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


class SessionAttention(SQLModel):
    """A session shown in the sidebar: working, waiting on the user, or finished and not yet looked at."""

    project_id: int
    root_run_id: int  # the session's first run; its prompt names the session
    run_id: int  # the session's newest run, whose status this is
    status: RunStatus
    title: str
    image_count: int = 0  # images pasted with the first prompt, for sessions started with only an image
    pending_tool: Optional[str] = None  # the tool waiting for approval, when status is waiting_approval
    result: Optional[str] = None  # the start of the agent's wrap-up, when finished
    error: Optional[str] = None
    finished_at: Optional[datetime] = None


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


def _check_color_map(colors: dict[str, str]) -> dict[str, str]:
    if len(colors) > 64:
        raise ValueError("too many colors")
    for key, value in colors.items():
        if not _COLOR_TOKEN.fullmatch(key):
            raise ValueError(f"not a color token: {key!r}")
        if not _HEX_COLOR.fullmatch(value):
            raise ValueError(f"{key} must be a #rrggbb color, got {value!r}")
    return {k: v.lower() for k, v in colors.items()}


class Appearance(SQLModel):
    """The UI theme: the active theme (`preset`, a system or custom theme id) plus the user's changes on top.

    Kept in appearance.json, not the database. The themes themselves live in services/themes.py.
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
        return _check_color_map(colors)

    @field_validator("font_ui", "font_mono")
    @classmethod
    def _check_font(cls, value: Optional[str]) -> Optional[str]:
        # The value lands in a CSS font-family; keep it to names, commas and quotes.
        if value is None or not value.strip():
            return None
        if re.search(r"[;{}<>\\]", value):
            raise ValueError("font names can't contain ; { } < > or \\")
        return value.strip()


class Theme(SQLModel):
    """A named palette. System themes ship with Mercury and can't be changed; custom ones are the user's."""

    id: str
    name: str
    system: bool = False
    colors: dict[str, str]  # every base token (services/themes.BASE_TOKENS), plus any derived ones it pins


class ThemeCreate(SQLModel):
    name: str = Field(min_length=1, max_length=60)
    colors: dict[str, str] = Field(default_factory=dict)  # merged over `based_on`'s colors
    based_on: Optional[str] = None  # a theme id; default: Mercury Dark

    @field_validator("colors")
    @classmethod
    def _check_colors(cls, colors: dict[str, str]) -> dict[str, str]:
        return _check_color_map(colors)


class ThemeUpdate(SQLModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=60)
    # Merged into the theme's colors. null removes a derived token's pinned color (base tokens can't be removed).
    colors: Optional[dict[str, Optional[str]]] = None


# --------------------------------------------------------------------------- preferences


class AgentSettingsAccess(str, Enum):
    """How much of `Preferences` and the appearance (themes) Mercury's agents may see and change.

    Applies to the get/update_settings tools and the appearance tools (services/mercury_tools.py).
    """

    off = "off"  # agents can't read or change settings
    read = "read"  # agents can read settings
    change = "change"  # agents can also change them; each change follows the run's permission mode


class SessionOpenScroll(str, Enum):
    """Where an agent session's conversation is scrolled to when the user opens it."""

    top = "top"  # the start of the conversation
    last_message = "last_message"  # the user's most recent message, with the reply below it
    bottom = "bottom"  # the end of the conversation (the latest output)


class Preferences(SQLModel):
    """App-wide settings (the Settings page). Kept in preferences.json next to the database, like `Appearance`.

    Each field's description is what agents see in get_settings, so write it for them.
    Fields listed in `services/preferences.USER_ONLY` can only be changed by the user.
    """

    default_permission_mode: PermissionMode = Field(
        default=PermissionMode.acceptEdits,
        description="Permission mode preselected for new agent sessions in projects that don't set their own.",
    )
    default_model: AgentModel = Field(
        default=AgentModel.opus_5_5,
        description="Claude model preselected for new agent sessions in projects that don't set their own. "
        "The user can still pick another model for each session.",
    )
    agent_settings_access: AgentSettingsAccess = Field(
        default=AgentSettingsAccess.change,
        description="Whether agents may read (read) or also change (change) these settings and the appearance "
        "(themes, fonts), or neither (off).",
    )
    agent_notifications: bool = Field(
        default=True,
        description="Show a desktop notification when an agent session finishes or needs the user's answer "
        "while the Mercury window isn't in focus.",
    )
    session_open_scroll: SessionOpenScroll = Field(
        default=SessionOpenScroll.last_message,
        description="Where an agent conversation opens when the user selects it: at the top, at the user's most "
        "recent message (last_message), or at the bottom. Links to a specific message still go to that message.",
    )
