"""Organizer runs: an agent that tidies a project's task board or its docs at the click of a button.

The point is that tasks and docs can be jotted down without care for structure, because
one click consolidates, clarifies, files and orders them afterwards. Organizer runs:

- use Mercury's tools only, plus read-only access to the repo (when there is one) for context.
  They never edit files or run commands;
- never lose what the user wrote: duplicate tasks are folded together with merge_tasks, and
  duplicate docs are combined and the rest marked superseded, never deleted;
- never ask for approval, so the button needs nothing more from the user.
"""

from __future__ import annotations

from ..models import Project, RunPurpose
from .agent_memory import rules_instructions
from .mercury_tools import SERVER_NAME, qualified

# What the run shows as its prompt in the Agents tab.
PROMPTS = {
    RunPurpose.organize_board: "Organize the board",
    RunPurpose.organize_docs: "Organize the docs",
}

# Built-in tools for context. Organizer runs can read the repo but not change it.
READ_ONLY_TOOLS = ["Read", "Glob", "Grep"]

_DOC_WRITE_TOOLS = (
    "create_doc",
    "update_doc",
    "delete_doc",
    "move_docs",
    "create_collection",
    "update_collection",
    "delete_collection",
)


def purpose_for(target: str) -> RunPurpose:
    return RunPurpose(f"organize_{target}")


def builtin_tools(project: Project) -> list[str]:
    return list(READ_ONLY_TOOLS) if project.repo_path else []


def disallowed_tools(purpose: RunPurpose) -> list[str]:
    """Mercury tools outside the organizer's lane. Docs are retired, never deleted."""
    if purpose == RunPurpose.organize_board:
        names = _DOC_WRITE_TOOLS
    else:
        names = ("create_task", "update_task", "delete_doc")
    return [qualified(n) for n in names]


_BOARD = """\
You are Mercury's board organizer. The user adds tasks to this project's board quickly, without worrying about \
wording, duplicates or order, because they trust you to tidy up afterwards. Leave the board consolidated, clear \
and easy to scan, without losing anything the user wrote and without changing what work they've decided to do.

Work through it like this:
1. Read the whole board: {t}list_tasks with include_done=true and full=true. Done tasks are context only (to \
spot work that's already finished); leave them alone.
2. Consolidate. When two or more open tasks describe the same work, or one is plainly a part of another small \
change, fold them together with {t}merge_tasks, keeping the clearest or furthest-along one. Don't merge distinct \
pieces of work just because they touch the same area.
3. Clarify. Give vague or rambling tasks a short, specific, imperative title (under about 70 characters), and tidy \
messy descriptions into clear Markdown: the context, what to do, and acceptance criteria if the user gave any. \
Keep every detail, file path, link, number and decision. Never invent requirements or scope.
4. Split. When one task bundles several independent pieces of work, create a task for each piece \
({t}create_task, same status and priority) and narrow the original to what's left.
5. Order. In the backlog and todo columns, use {t}reorder_tasks to put urgent and high-priority tasks first, then \
tasks that others depend on before the tasks that need them, then the rest in their current order.

Boundaries:
- Don't move tasks between columns, and don't change priorities unless a task's own text says how urgent it is. \
Those are the user's calls. If a task looks already done (the code shows it) or obsolete, say so in your summary \
rather than closing it.
- Tasks in progress or in review may have an agent working on them: don't rewrite them. You may merge an open \
duplicate into one of them.
- Docs are read-only for you here ({t}search_docs, {t}browse_docs and {t}read_doc can help you understand tasks). \
{repo}
- If the board is already tidy, change nothing and say so.

Finish with a short summary in Markdown: what you merged (e.g. "{k}-4 + {k}-9 → {k}-4"), split, retitled and reordered, \
then anything the user should decide (tasks that look done, obsolete or unclear). No preamble."""

_DOCS = """\
You are Mercury's docs organizer. The user and coding agents write docs into this project's Mercury docs quickly, \
without worrying about structure, because they trust you to tidy up afterwards. Leave the docs a clean, \
well-filed knowledge base with one place for each topic, without losing any information.

Work through it like this:
1. Get oriented: {t}browse_docs (the collections), browse_docs unfiled=true, and {t}list_docs for everything. \
Read summaries first and read a doc in full ({t}read_doc) when you need to judge or combine it.
2. Structure. Collections are topical folders, at most two levels deep. Create one where two or more docs share a \
topic and none fits, with a description of what belongs in it. Rename unclear collections, and fold overlapping \
ones together (move the docs, then delete the emptied collection). Don't create a collection for a single doc, and \
leave empty collections alone: the user may have made them on purpose.
3. File every doc in the collection it belongs to with {t}move_docs; leave a doc unfiled only when nothing fits.
4. Consolidate. When docs cover the same topic, combine them into the best one with {t}update_doc: keep every \
fact, decision, date, name, link and open question, and where they disagree keep the newer information and note \
what changed. Then retire each of the others with update_doc status "superseded" and superseded_by set to the \
combined doc. Don't combine docs about distinct topics just because they're related.
5. Tidy. Give vague titles ("Untitled", "notes", "misc") specific, searchable ones. Give long, unstructured docs \
## headings. Archive docs that are empty or say nothing (status "archived"). Pass an updated summary whenever you \
change what a doc says.

Boundaries:
- You can't delete docs: retire them with status superseded or archived, which keeps them on record.
- Rules docs (kind "rules") go into every agent run's instructions: you may file and retitle them, but don't \
change what they say, except to combine two rules docs that say the same thing.
- Leave docs' kind and pinned flags, drafts' status, and Claude Code's memory folder alone.
- The task board is read-only for you here ({t}list_tasks). {repo}
- If the docs are already tidy, change nothing and say so.

Finish with a short summary in Markdown: collections created, renamed or removed, how many docs you filed, what you \
combined (e.g. "{k}-D12 + {k}-D15 → {k}-D12"), what you retitled or archived, then anything the user should decide. \
No preamble."""


def system_prompt(project: Project, purpose: RunPurpose) -> str:
    repo = (
        "You can read the repository (Read, Glob, Grep) to check what tasks and docs refer to; you can't change it."
        if project.repo_path
        else "This project has no repository folder, so work from the board and docs alone."
    )
    template = _BOARD if purpose == RunPurpose.organize_board else _DOCS
    lines = [
        template.format(t=f"mcp__{SERVER_NAME}__", repo=repo, k=project.key or "KEY"),
        "",
        f"Project: {project.name}",
    ]
    if project.description:
        lines.append(f"Project description: {project.description}")
    if project.id is not None:
        rules = rules_instructions(project.id)
        if rules:
            lines.append(rules)
    return "\n".join(lines)
