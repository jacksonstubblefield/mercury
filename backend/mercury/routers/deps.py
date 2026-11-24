"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlmodel import Session

from ..db import get_session
from ..models import Project
from ..services.agent_runner import RunManager

SessionDep = Annotated[Session, Depends(get_session)]


def get_runs(request: Request) -> RunManager:
    return request.app.state.runs


RunsDep = Annotated[RunManager, Depends(get_runs)]


def get_project_or_404(session: Session, project_id: int) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    return project
