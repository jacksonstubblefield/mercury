"""Project keys and the per-project ids of tasks and docs.

Every project has a unique key of 2-4 characters (letters and digits, starting with a letter),
e.g. "MER". Tasks and docs are numbered within their project, so people and agents refer to
them as "MER-12" (task 12) and "MER-D3" (doc 3). The database rows keep their global primary
keys (foreign keys, URLs and the REST API use those); `number` is the per-project id.

Numbers come from counters on the project (task_seq, doc_seq), so a deleted task's number is
never handed out again. They're assigned in a before_flush hook, so every way of creating a
task or doc gets one. Keys of new projects without one are generated from the name the same way.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from sqlalchemy import event, text
from sqlalchemy.orm import Session as OrmSession
from sqlmodel import Session, select

from ..models import Doc, Project, Task

KEY_RE = re.compile(r"[A-Z][A-Z0-9]{1,3}")
KEY_RULES = "A project key is 2-4 letters or digits, starting with a letter (e.g. MER)."


class ProjectKeyError(ValueError):
    """A project key that is malformed or already taken."""


def normalize_key(raw: Any) -> str:
    """Upper-case and validate a key someone typed. Raises ProjectKeyError when it's malformed."""
    key = str(raw or "").strip().upper()
    if not KEY_RE.fullmatch(key):
        raise ProjectKeyError(KEY_RULES)
    return key


def taken_keys(session: Session, exclude_project_id: Optional[int] = None) -> set[str]:
    query = select(Project.key, Project.id).where(Project.key.is_not(None))
    return {k for k, pid in session.exec(query).all() if pid != exclude_project_id}


def suggest_key(name: str, taken: set[str]) -> str:
    """A free key for a project called `name`: initials for several words, else the first letters."""
    words = re.findall(r"[A-Za-z0-9]+", name or "")
    letters = "".join(words).upper()
    if letters[:1].isdigit() or not letters:
        letters = "P" + letters
    if len(letters) < 2:
        letters += "X"
    candidates: list[str] = []
    if len(words) >= 2:
        initials = "".join(w[0] for w in words).upper()
        if initials[0].isalpha():
            candidates += [initials[:4], initials[:3]]
            # Initials plus more of the last word, e.g. "Big Data" -> "BDA".
            candidates.append((initials[:-1] + words[-1].upper())[:3])
    candidates += [letters[:3], letters[:4], letters[:2]]
    # Then the first letter with later letters of the name, e.g. "MERCURY" -> "MEC", "MEU".
    for i in range(2, min(len(letters), 12)):
        candidates.append(letters[:2] + letters[i])
    for c in candidates:
        if len(c) >= 2 and KEY_RE.fullmatch(c) and c not in taken:
            return c
    for n in range(1, 100):  # MER1..MER9, then ME10..ME99
        c = (letters[:3] if n < 10 else letters[:2]) + str(n)
        if c not in taken:
            return c
    raise ProjectKeyError("Couldn't find a free project key; pick one yourself.")


# --------------------------------------------------------------------------- refs


def task_ref(key: Optional[str], number: Optional[int]) -> str:
    return f"{key}-{number}" if key and number is not None else f"#{number}"


def doc_ref(key: Optional[str], number: Optional[int]) -> str:
    return f"{key}-D{number}" if key and number is not None else f"D{number}"


_TASK_REF = re.compile(r"(?:([A-Za-z][A-Za-z0-9]{1,3})-|#)?(\d+)")
_DOC_REF = re.compile(r"(?:([A-Za-z][A-Za-z0-9]{1,3})-)?D?(\d+)", re.IGNORECASE)


def parse_task_number(value: Any, key: Optional[str]) -> Optional[int]:
    """The task number in 12, "12", "#12" or "MER-12"; None if it's not one, or names another project."""
    return _parse(_TASK_REF, value, key)


def parse_doc_number(value: Any, key: Optional[str]) -> Optional[int]:
    """The doc number in 3, "3", "D3" or "MER-D3"; None if it's not one, or names another project."""
    return _parse(_DOC_REF, value, key)


def _parse(pattern: re.Pattern, value: Any, key: Optional[str]) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if not isinstance(value, str):
        return None
    m = pattern.fullmatch(value.strip())
    if m is None:
        return None
    if m.group(1) and (key is None or m.group(1).upper() != key.upper()):
        return None
    return int(m.group(2))


def project_key(session: Session, project_id: int) -> Optional[str]:
    return session.exec(select(Project.key).where(Project.id == project_id)).first()


def get_task(session: Session, project_id: int, value: Any) -> Optional[Task]:
    number = parse_task_number(value, project_key(session, project_id))
    if number is None:
        return None
    return session.exec(select(Task).where(Task.project_id == project_id, Task.number == number)).first()


def get_doc(session: Session, project_id: int, value: Any) -> Optional[Doc]:
    number = parse_doc_number(value, project_key(session, project_id))
    if number is None:
        return None
    return session.exec(select(Doc).where(Doc.project_id == project_id, Doc.number == number)).first()


def doc_refs(session: Session, doc_ids: list[int]) -> dict[int, str]:
    """Global doc id -> "MER-D3", for showing docs found by their primary keys."""
    ids = [i for i in doc_ids if i is not None]
    if not ids:
        return {}
    rows = session.exec(
        select(Doc.id, Doc.number, Project.key).join(Project, Project.id == Doc.project_id).where(Doc.id.in_(ids))
    ).all()
    return {i: doc_ref(k, n) for i, n, k in rows}


# --------------------------------------------------------------------------- assignment


def _next(session: OrmSession, column: str, project_id: int) -> int:
    # One UPDATE ... RETURNING takes the write lock and bumps the counter atomically,
    # so two sessions adding tasks at once can't get the same number.
    row = session.connection().execute(
        text(f"UPDATE project SET {column} = {column} + 1 WHERE id = :id RETURNING {column}"), {"id": project_id}
    ).first()
    if row is None:
        raise ValueError(f"Project {project_id} doesn't exist.")
    return row[0]


@event.listens_for(OrmSession, "before_flush")
def _assign(session: OrmSession, _ctx, _instances) -> None:
    new = list(session.new)
    projects = [o for o in new if isinstance(o, Project) and not o.key]
    if projects:
        taken = {k for (k,) in session.connection().execute(text("SELECT key FROM project WHERE key IS NOT NULL"))}
        taken |= {o.key for o in new if isinstance(o, Project) and o.key}
        for p in projects:
            p.key = suggest_key(p.name, taken)
            taken.add(p.key)
    for o in new:
        if isinstance(o, Task) and o.number is None and o.project_id is not None:
            o.number = _next(session, "task_seq", o.project_id)
        elif isinstance(o, Doc) and o.number is None and o.project_id is not None:
            o.number = _next(session, "doc_seq", o.project_id)


# --------------------------------------------------------------------------- migration


def backfill(conn) -> None:
    """Give projects made before keys existed a key, and number their tasks and docs in creation order."""
    taken = {k for (k,) in conn.execute(text("SELECT key FROM project WHERE key IS NOT NULL"))}
    for pid, name in conn.execute(text("SELECT id, name FROM project WHERE key IS NULL ORDER BY id")).all():
        key = suggest_key(name, taken)
        taken.add(key)
        conn.execute(text("UPDATE project SET key = :k WHERE id = :id"), {"k": key, "id": pid})
    for table, seq in (("task", "task_seq"), ("doc", "doc_seq")):
        rows = conn.execute(text(f"SELECT id, project_id FROM {table} WHERE number IS NULL ORDER BY id")).all()
        for row_id, pid in rows:
            n = conn.execute(
                text(f"UPDATE project SET {seq} = MAX({seq}, (SELECT COALESCE(MAX(number), 0) FROM {table} "
                     f"WHERE project_id = :p)) + 1 WHERE id = :p RETURNING {seq}"),
                {"p": pid},
            ).scalar_one()
            conn.execute(text(f"UPDATE {table} SET number = :n WHERE id = :id"), {"n": n, "id": row_id})
