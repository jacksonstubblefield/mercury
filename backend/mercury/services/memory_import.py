"""Copy a project's Claude Code memory into Mercury docs.

The memory folder (~/.claude/projects/<sanitized repo path>/memory) is only read, never
changed: Claude Code sessions outside Mercury keep using it. Mercury's docs are
Mercury's own memory from then on, so there's no ongoing sync. Running the import
again creates docs for new memories and updates docs whose memory changed, but
leaves alone any doc that has been edited in Mercury since it was imported.

Mapping:
- "project" and "reference" memories become docs; their description becomes the summary.
- "feedback" and "user" memories (how to work) are combined into one rules doc, which
  goes into every agent run's instructions.
- Memories over SPLIT_CHARS with several "## " sections are split: the text before the
  first section becomes a pinned overview doc, and each section its own doc, all filed in
  a collection named after the memory.
- MEMORY.md (the index) only supplies titles.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml
from sqlmodel import Session, select

from ..models import Doc, DocKind, DocOrigin, DocStatus, Project
from . import doc_collections, doc_store

SOURCE = "claude-memory"
SPLIT_CHARS = 40_000
MIN_SPLIT_SECTIONS = 3
MAX_TITLE_CHARS = 120
RULES_KEY = f"{SOURCE}:rules"
RULES_TITLE = "Agent working rules"
RULE_TYPES = {"feedback", "user"}
_REVIEW_WORDS = re.compile(
    r"\b(obsolete|outdated|disproven|superseded|redirected|dropped|abandoned|rejected|no longer|dead end)\b", re.I
)
_INDEX_LINK = re.compile(r"^\s*[-*]\s*\[([^\]]+)\]\(([^)]+)\)")
_FENCE = re.compile(r"^\s*(```|~~~)")


def claude_projects_dir() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(base) if base else Path.home() / ".claude") / "projects"


def _sanitize(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def find_memory_dir(repo_path: Optional[str]) -> Optional[Path]:
    """Claude Code's memory folder for a repo, matched case-insensitively (drive letters vary)."""
    root = claude_projects_dir()
    if not repo_path or not root.is_dir():
        return None
    wanted = {_sanitize(str(Path(repo_path))).lower(), _sanitize(repo_path.rstrip("\\/")).lower()}
    for d in sorted(root.iterdir()):
        if d.name.lower() in wanted and (d / "memory").is_dir():
            return d / "memory"
    return None


def resolve_memory_dir(project: Project, override: Optional[str]) -> Path:
    """The folder to import from; an override must be inside Claude Code's projects folder."""
    if override:
        path = Path(override).expanduser().resolve()
        root = claude_projects_dir().resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"The memory folder must be inside {root}.")
        if not path.is_dir():
            raise ValueError(f"{path} isn't a folder.")
        return path
    found = find_memory_dir(project.repo_path)
    if found is None:
        raise LookupError("No Claude Code memory folder was found for this project's repo path.")
    return found


# --------------------------------------------------------------------------- parsing


def parse_memory(text: str) -> tuple[dict[str, Any], str]:
    """Split a memory file into its YAML frontmatter and body."""
    text = text.lstrip("﻿")
    m = re.match(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", text, re.S)
    if not m:
        return {}, text.strip()
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        meta = {}
    return (meta if isinstance(meta, dict) else {}), text[m.end() :].strip()


def _memory_type(meta: dict[str, Any]) -> str:
    nested = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
    return str(meta.get("type") or nested.get("type") or "project").lower()


def _index_titles(memory_dir: Path) -> dict[str, str]:
    index = memory_dir / "MEMORY.md"
    if not index.is_file():
        return {}
    titles = {}
    for line in index.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _INDEX_LINK.match(line)
        if m:
            titles[Path(m.group(2)).name.lower()] = m.group(1).strip()
    return titles


def _title_from(name: str) -> str:
    return re.sub(r"[_-]+", " ", Path(name).stem).strip().capitalize() or name


def _h2_sections(body: str) -> list[tuple[str, int, int]]:
    """Top-level ("## ") sections outside code fences: (heading, start line index, end index)."""
    lines = body.split("\n")
    starts: list[tuple[str, int]] = []
    fence = None
    for i, line in enumerate(lines):
        m = _FENCE.match(line)
        if m:
            fence = None if fence == m.group(1) else (fence or m.group(1))
            continue
        if not fence and line.startswith("## "):
            starts.append((line[3:].strip(), i))
    return [(h, a, starts[n + 1][1] if n + 1 < len(starts) else len(lines)) for n, (h, a) in enumerate(starts)]


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "section"


# --------------------------------------------------------------------------- plan


@dataclass
class ImportItem:
    key: str
    file: str
    title: str
    kind: DocKind
    body: str
    summary: str = ""
    pinned: bool = False
    flags: list[str] = field(default_factory=list)
    modified: Optional[datetime] = None
    collection: Optional[str] = None  # name of the top-level collection to file a new doc in
    collection_description: str = ""
    # Filled in against the database by plan():
    action: str = "create"  # create | update | unchanged | edited
    doc_id: Optional[int] = None

    @property
    def source_hash(self) -> str:
        return doc_store.content_hash(self.title, self.body)

    def preview(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("body")
        data.pop("modified")
        data["kind"] = self.kind.value
        data["chars"] = len(self.body)
        data["include"] = self.action in ("create", "update")
        return data


def read_items(memory_dir: Path) -> list[ImportItem]:
    titles = _index_titles(memory_dir)
    items: list[ImportItem] = []
    rules: list[tuple[str, str, Optional[datetime]]] = []
    for path in sorted(memory_dir.glob("*.md"), key=lambda p: p.name.lower()):
        if path.name.lower() == "memory.md":
            continue
        meta, body = parse_memory(path.read_text(encoding="utf-8", errors="replace"))
        title = titles.get(path.name.lower()) or _title_from(path.name)
        description = " ".join(str(meta.get("description") or "").split())
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if _memory_type(meta) in RULE_TYPES:
            rules.append((title, body, modified))
            continue
        key = f"{SOURCE}:{path.name}"
        flags = ["Mentions being outdated or dropped: check its status."] if _REVIEW_WORDS.search(description) else []
        sections = _h2_sections(body)
        if len(body) > SPLIT_CHARS and len(sections) >= MIN_SPLIT_SECTIONS:
            lines = body.split("\n")
            intro = "\n".join(lines[: sections[0][1]]).strip()
            items.append(
                ImportItem(key, path.name, title, DocKind.doc, intro, summary=description, pinned=True, flags=flags,
                           modified=modified, collection=title, collection_description=description)
            )
            seen: dict[str, int] = {}
            for heading, a, b in sections:
                slug = _slug(heading)
                seen[slug] = seen.get(slug, 0) + 1
                if seen[slug] > 1:
                    slug = f"{slug}-{seen[slug]}"
                sec_title = f"{title}: {heading}"
                if len(sec_title) > MAX_TITLE_CHARS:
                    sec_title = sec_title[: MAX_TITLE_CHARS - 1].rstrip() + "…"
                items.append(
                    ImportItem(
                        f"{key}#{slug}",
                        path.name,
                        sec_title,
                        DocKind.doc,
                        "\n".join(lines[a + 1 : b]).strip(),
                        flags=["Heading suggests it was overturned: check its status."] if _REVIEW_WORDS.search(heading) else [],
                        modified=modified,
                        collection=title,
                        collection_description=description,
                    )
                )
        else:
            items.append(ImportItem(key, path.name, title, DocKind.doc, body, summary=description, flags=flags, modified=modified))
    if rules:
        body = "\n\n".join(f"## {t}\n\n{b}" for t, b, _ in rules)
        items.insert(
            0,
            ImportItem(
                RULES_KEY,
                ", ".join(sorted({t for t, _, _ in rules})),
                RULES_TITLE,
                DocKind.rules,
                body,
                summary="How to work on this project: " + "; ".join(t for t, _, _ in rules) + ".",
                modified=max(m for _, _, m in rules if m) if rules else None,
            ),
        )
    return items


def plan(session: Session, project: Project, memory_dir: Path) -> list[ImportItem]:
    items = read_items(memory_dir)
    existing = {
        d.source_key: d
        for d in session.exec(
            select(Doc).where(Doc.project_id == project.id, Doc.source_key.startswith(f"{SOURCE}:"))
        ).all()
    }
    for item in items:
        doc = existing.get(item.key)
        if doc is None:
            continue
        item.doc_id = doc.id
        edited = doc_store.content_hash(doc.title, doc.body) != doc.source_hash
        changed = item.source_hash != doc.source_hash
        item.action = "edited" if edited else ("update" if changed else "unchanged")
    return items


def apply(
    session: Session, project: Project, memory_dir: Path, choices: dict[str, dict[str, Any]]
) -> dict[str, int]:
    """Import the chosen items. choices: key -> {"include": bool, "status": DocStatus | None}.

    Items not in `choices` get their default (create and update included). An "edited"
    item is only overwritten when explicitly included.
    """
    counts = {"created": 0, "updated": 0, "skipped": 0}
    collections: dict[str, int] = {}

    def collection_for(item: ImportItem) -> int:
        """The collection for a split memory: the one this import made before, or a top-level one
        with the same name, or a new one."""
        key = f"{SOURCE}:{item.file}"
        if key not in collections:
            existing = [c for c in doc_collections.all_for(session, project.id) if c.parent_id is None]
            found = next((c for c in existing if c.source_key == key), None) or next(
                (c for c in existing if c.name.lower() == item.collection.lower()), None
            )
            if found is None:
                found = doc_collections.create(
                    session, project.id, item.collection, item.collection_description, source_key=key
                )
            collections[key] = found.id
        return collections[key]

    for item in plan(session, project, memory_dir):
        choice = choices.get(item.key, {})
        include = choice.get("include", item.action in ("create", "update"))
        if not include or item.action == "unchanged":
            counts["skipped"] += 1
            continue
        status = DocStatus(choice["status"]) if choice.get("status") else None
        if item.action == "create":
            doc = Doc(
                project_id=project.id,
                title=item.title,
                body=item.body,
                kind=item.kind,
                pinned=item.pinned,
                status=status or DocStatus.current,
                origin=DocOrigin.import_,
                collection_id=collection_for(item) if item.collection else None,
                source_key=item.key,
                source_hash=item.source_hash,
            )
            doc_store.save_doc(session, doc, summary=item.summary or None)
            if item.modified is not None:  # keep the memory's age, so recency ordering means something
                doc.created_at = doc.updated_at = item.modified
                session.add(doc)
                session.commit()
            counts["created"] += 1
        else:
            doc = session.get(Doc, item.doc_id)
            doc.title, doc.body, doc.kind, doc.source_hash = item.title, item.body, item.kind, item.source_hash
            if status:
                doc.status = status
            doc_store.save_doc(session, doc, summary=item.summary or None)
            counts["updated"] += 1
    return counts


def remove_imported(session: Session, project: Project) -> int:
    docs = session.exec(select(Doc).where(Doc.project_id == project.id, Doc.source_key.startswith(f"{SOURCE}:"))).all()
    for doc in docs:
        doc_store.delete_doc(session, doc)
    counts = doc_collections.doc_counts(session, project.id)
    cols = doc_collections.all_for(session, project.id)
    for c in cols:
        has_children = any(k.parent_id == c.id for k in cols)
        if (c.source_key or "").startswith(f"{SOURCE}:") and not counts.get(c.id) and not has_children:
            doc_collections.delete(session, c)
    return len(docs)
