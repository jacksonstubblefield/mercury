from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlmodel import func, select

from ..models import (
    ACTIVE_RUN_STATUSES,
    AgentRun,
    Project,
    ProjectCreate,
    ProjectRead,
    ProjectUpdate,
    Task,
    TaskStatus,
    utcnow,
)
from ..services import attachments, gitinfo, refs
from .deps import SessionDep, get_project_or_404

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _to_read(session, project: Project) -> ProjectRead:
    open_tasks = session.exec(
        select(func.count()).select_from(Task).where(Task.project_id == project.id, Task.status != TaskStatus.done)
    ).one()
    active_runs = session.exec(
        select(func.count())
        .select_from(AgentRun)
        .where(AgentRun.project_id == project.id, AgentRun.status.in_(list(ACTIVE_RUN_STATUSES)))
    ).one()
    return ProjectRead(**project.model_dump(), open_tasks=open_tasks, active_runs=active_runs)


def _detect_github_repo(project: Project) -> None:
    if project.repo_path and not project.github_repo:
        try:
            project.github_repo = gitinfo.repo_summary(project.repo_path, commit_limit=0)["github_repo"]
        except gitinfo.GitError:
            pass


def _check_key(session, raw, project_id: int | None = None) -> str:
    try:
        key = refs.normalize_key(raw)
    except refs.ProjectKeyError as e:
        raise HTTPException(422, str(e)) from e
    if key in refs.taken_keys(session, exclude_project_id=project_id):
        raise HTTPException(409, f"Another project already uses the key {key}.")
    return key


@router.get("/suggest-key")
def suggest_key(name: str, session: SessionDep):
    """The key a new project called `name` would get if none is given."""
    return {"key": refs.suggest_key(name, refs.taken_keys(session))}


@router.get("", response_model=list[ProjectRead])
def list_projects(session: SessionDep):
    projects = session.exec(select(Project).order_by(Project.status, Project.name)).all()
    return [_to_read(session, p) for p in projects]


@router.post("", response_model=ProjectRead, status_code=201)
def create_project(body: ProjectCreate, session: SessionDep):
    key = _check_key(session, body.key) if body.key else None  # None: refs generates one
    project = Project.model_validate(body.model_dump(exclude={"key"}), update={"key": key})
    _detect_github_repo(project)
    session.add(project)
    session.commit()
    session.refresh(project)
    return _to_read(session, project)


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(project_id: int, session: SessionDep):
    return _to_read(session, get_project_or_404(session, project_id))


@router.patch("/{project_id}", response_model=ProjectRead)
def update_project(project_id: int, body: ProjectUpdate, session: SessionDep):
    project = get_project_or_404(session, project_id)
    changes = body.model_dump(exclude_unset=True)
    if "key" in changes:
        changes["key"] = _check_key(session, changes["key"], project_id)
    project.sqlmodel_update(changes)
    _detect_github_repo(project)
    project.updated_at = utcnow()
    session.add(project)
    session.commit()
    session.refresh(project)
    return _to_read(session, project)


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: int, session: SessionDep):
    project = get_project_or_404(session, project_id)
    runs = session.exec(select(AgentRun).where(AgentRun.project_id == project_id)).all()
    tasks = session.exec(select(Task).where(Task.project_id == project_id)).all()
    images = [name for run in runs for name in run.images] + [name for task in tasks for name in task.images]
    session.delete(project)
    session.commit()
    attachments.delete(images)


@router.get("/{project_id}/git")
def project_git(project_id: int, session: SessionDep):
    project = get_project_or_404(session, project_id)
    if not project.repo_path:
        raise HTTPException(400, "This project has no local repository folder.")
    try:
        return gitinfo.repo_summary(project.repo_path)
    except gitinfo.GitError as exc:
        raise HTTPException(400, str(exc)) from exc
