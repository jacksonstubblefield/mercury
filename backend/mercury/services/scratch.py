"""The Scratch project: the home of agent sessions that don't belong to any of the user's projects.

It's a built-in project (``Project.scratch``), created at startup, so a scratch session gets
everything a project's session has (follow-ups, task links, docs, the sidebar's Sessions
list) without special cases. It can't be deleted, renamed or archived.

Its sessions share one folder, by default ``<data dir>/scratch``. The user may point it
elsewhere. They share it, rather than getting one each, because a Claude session can only
be resumed from the folder it ran in, and so that later sessions can pick up earlier files.
"""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, select

from .. import config
from ..models import Project

NAME = "Scratch"
COLOR = "#9aa1b2"


def default_folder() -> Path:
    return config.data_dir() / "scratch"


def prepare_folder(project: Project) -> None:
    """Make sure Scratch's own folder exists (the user may have cleared it out). A folder the user
    chose is left to them, like any project's."""
    if project.scratch and project.repo_path == str(default_folder()):
        default_folder().mkdir(parents=True, exist_ok=True)


def ensure(session: Session) -> Project:
    """The Scratch project, created if this database doesn't have it yet."""
    project = session.exec(select(Project).where(Project.scratch)).first()
    if project is None:
        project = Project(name=NAME, color=COLOR, repo_path=str(default_folder()), scratch=True)
        session.add(project)
        session.commit()
        session.refresh(project)
    prepare_folder(project)
    return project
