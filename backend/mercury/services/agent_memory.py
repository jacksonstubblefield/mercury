"""What agent runs know about Mercury docs, and the guard that keeps Claude Code memory read-only.

Mercury docs are a project's long-term memory inside Mercury. Claude Code's own memory
folder (~/.claude/projects/<repo>/memory) belongs to the user's sessions outside
Mercury: runs may read it but must not write to it. The run instructions say so, and
a PreToolUse hook enforces it in every permission mode (hooks run even in
bypassPermissions, where can_use_tool isn't consulted).
"""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePath
from typing import Any

from claude_agent_sdk import HookMatcher
from sqlmodel import Session, func, select

from ..db import get_engine
from ..models import Doc, DocKind, DocStatus
from . import doc_collections
from .memory_import import claude_projects_dir
from .mercury_tools import SERVER_NAME

MAX_RULES_CHARS = 12_000
MAX_PINNED = 12
PINNED_SUMMARY_CHARS = 300
MAP_SUMMARY_CHARS = 250
MAX_MAP_CHARS = 8_000

FILE_TOOLS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}
SHELL_TOOLS = {"Bash", "PowerShell"}
# Shell commands that can change files. A command that mentions the memory folder and
# one of these is blocked; reading (cat, grep, ls, Get-Content...) is fine.
_SHELL_WRITES = re.compile(
    r"(>|\btee\b|\bmv\b|\bcp\b|\brm\b|\brmdir\b|\bmkdir\b|\btouch\b|\btruncate\b|\bdd\b|\bsed\b[^|;&]*\s-i|\bperl\b[^|;&]*\s-i|"
    r"\bpython[\d.]*\b|\bnode\b|\bln\b|\binstall\b|\brsync\b|"
    r"set-content|add-content|out-file|new-item|remove-item|move-item|copy-item|rename-item|clear-content)",
    re.I,
)
DENY_REASON = (
    "Claude Code's memory folder is read-only inside Mercury: it belongs to the user's Claude Code sessions "
    f"outside Mercury. Save what you wanted to remember as a Mercury doc instead (mcp__{SERVER_NAME}__search_docs "
    "to find an existing one, then update_doc, or create_doc)."
)


# --------------------------------------------------------------------------- instructions


def docs_instructions(project_id: int) -> str:
    with Session(get_engine()) as s:
        active = (Doc.project_id == project_id) & (Doc.status.not_in([DocStatus.archived, DocStatus.superseded]))
        total = s.exec(select(func.count()).select_from(Doc).where(Doc.project_id == project_id)).one()
        rules = s.exec(select(Doc).where(active, Doc.kind == DocKind.rules).order_by(Doc.created_at)).all()
        pinned = s.exec(
            select(Doc).where(active, Doc.kind == DocKind.doc, Doc.pinned).order_by(Doc.updated_at.desc()).limit(MAX_PINNED)
        ).all()
        tree = doc_collections.tree(s, project_id)
        unfiled = doc_collections.doc_counts(s, project_id).get(None, 0)

    t = f"mcp__{SERVER_NAME}__"
    parts = [
        "## Mercury docs: this project's long-term memory",
        f"The project has {total} doc(s) in Mercury: decisions, findings, specs and notes kept by the user and by "
        f"earlier agent runs, filed in collections (folders, at most two levels). Use the {t}browse_docs, "
        "search_docs, read_doc, create_doc and update_doc tools (and list_docs, delete_doc, move_docs, "
        "create_collection, update_collection, delete_collection).",
        "- Before you start, search the docs for anything relevant to the request, and read what matters; "
        "browse_docs shows what's in a collection when you don't yet know what to search for. Docs can be out of "
        "date: check them against the code before relying on them, and fix them when they're wrong.",
        "- Save as you go, without being asked, what a future run would need and couldn't easily get from the code, "
        "git history or the task board: decisions and why they were made, findings from investigation and "
        "experiments (including what didn't work), how things actually behave, and the user's preferences about "
        "the project. Don't save what the code or the diff already shows, or a running log of this session.",
        "- Prefer updating an existing doc over creating a new one, and search before creating. Keep docs "
        "focused on one topic, with ## headings. When a doc is wrong or replaced, fix it or set its status "
        "(superseded with superseded_by, or archived) rather than leaving contradictions.",
        "- Give each doc a summary written for other agents whenever you create it or change what it says.",
        "- File every doc in the collection it belongs to. When several docs share a topic and no collection "
        "fits, create one (with a description of what belongs in it) and move them in; don't make a collection "
        "for a single doc. Keep the structure shallow and topical.",
        "- When the user corrects how you work or states how they want work done on this project, record it in a "
        'doc with kind "rules": rules docs are given to every run.',
        "- Write absolute dates (YYYY-MM-DD), not relative ones like \"yesterday\".",
        "- Claude Code's own memory folder (~/.claude/projects/<repo>/memory) belongs to the user's sessions "
        "outside Mercury. You may read it, but never write to it; Mercury docs are your memory here.",
    ]
    if rules:
        text, used = [], 0
        for d in rules:
            block = f"### {d.title} (doc {d.id})\n{d.body.strip()}"
            if used + len(block) > MAX_RULES_CHARS:
                text.append(f"[More rules docs, cut for length: read them with {t}list_docs kind=rules.]")
                break
            text.append(block)
            used += len(block)
        parts.append("\n## Working rules for this project (from rules docs; follow them)\n" + "\n\n".join(text))
    if pinned:
        lines = []
        for d in pinned:
            summary = d.summary if len(d.summary) <= PINNED_SUMMARY_CHARS else d.summary[: PINNED_SUMMARY_CHARS - 1] + "…"
            lines.append(f"- Doc {d.id}: {d.title}" + (f": {summary}" if summary else ""))
        parts.append("\n## Pinned docs\n" + "\n".join(lines))
    if tree:
        lines, used = [], 0
        for node in tree:
            c = node["collection"]
            summary = c.summary or c.description
            if len(summary) > MAP_SUMMARY_CHARS:
                summary = summary[: MAP_SUMMARY_CHARS - 1] + "…"
            line = f"- {c.name} (collection {c.id}, {node['doc_count']} docs)" + (f": {summary}" if summary else "")
            if node["children"]:
                line += "\n  Subcollections: " + ", ".join(f"{k.name} ({k.id})" for k in node["children"])
            if used + len(line) > MAX_MAP_CHARS:
                lines.append(f"- … {len(tree) - len(lines)} more: see {t}browse_docs.")
                break
            lines.append(line)
            used += len(line)
        if unfiled:
            lines.append(f"- Unfiled: {unfiled} docs")
        parts.append("\n## Doc collections\n" + "\n".join(lines))
    return "\n".join(parts)


# --------------------------------------------------------------------------- memory guard


def _norm(p: str) -> str:
    return p.replace("\\", "/").lower().rstrip("/")


def is_memory_path(path: str, cwd: str | None = None) -> bool:
    """True for files in any Claude Code project memory folder: <projects>/<project>/memory/..."""
    if not path:
        return False
    p = PurePath(os.path.expanduser(path))
    if not p.is_absolute() and cwd:
        p = PurePath(cwd) / p
    full = _norm(os.path.normpath(str(p)))
    root = _norm(os.path.normpath(str(claude_projects_dir())))
    if not full.startswith(root + "/"):
        return False
    rest = full[len(root) + 1 :].split("/")
    return len(rest) >= 2 and rest[1] == "memory"


def _mentions_memory(command: str) -> bool:
    c = _norm(command)
    root = _norm(str(claude_projects_dir()))
    home = _norm(str(Path.home()))
    variants = {root, root.replace(home, "~", 1), root.replace(home, "$home", 1), "/.claude/projects/"}
    return "memory" in c and any(v in c for v in variants)


def memory_write_blocked(tool_name: str, tool_input: dict[str, Any], cwd: str | None = None) -> bool:
    if tool_name in FILE_TOOLS:
        return is_memory_path(str(tool_input.get(FILE_TOOLS[tool_name]) or ""), cwd)
    if tool_name in SHELL_TOOLS:
        command = str(tool_input.get("command") or "")
        return _mentions_memory(command) and bool(_SHELL_WRITES.search(command))
    return False


async def guard_memory_writes(input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
    if memory_write_blocked(input_data.get("tool_name", ""), input_data.get("tool_input") or {}, input_data.get("cwd")):
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": DENY_REASON,
            }
        }
    return {}


def memory_hooks() -> dict[str, list[HookMatcher]]:
    matcher = "|".join([*FILE_TOOLS, *sorted(SHELL_TOOLS)])
    return {"PreToolUse": [HookMatcher(matcher=matcher, hooks=[guard_memory_writes])]}
