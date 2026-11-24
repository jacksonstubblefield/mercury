from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlmodel import func, select

from ..models import Project, Task, TaskStatus, utcnow
from ..services import github
from .deps import SessionDep, get_project_or_404

router = APIRouter(prefix="/api/projects/{project_id}/github", tags=["github"])


def _repo(project: Project) -> str:
    if not project.github_repo:
        raise HTTPException(400, "This project isn't linked to a GitHub repository.")
    return project.github_repo


@router.get("/issues")
def issues(project_id: int, session: SessionDep, state: str = "open"):
    project = get_project_or_404(session, project_id)
    try:
        return github.list_issues(_repo(project), state=state)
    except github.GitHubError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/pulls")
def pulls(project_id: int, session: SessionDep, state: str = "open"):
    project = get_project_or_404(session, project_id)
    try:
        return github.list_pulls(_repo(project), state=state)
    except github.GitHubError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("/sync-issues")
def sync_issues(project_id: int, session: SessionDep):
    """Create tasks for open issues and mark tasks done when their issue closes."""
    project = get_project_or_404(session, project_id)
    try:
        open_issues = github.list_issues(_repo(project), state="open", limit=200)
        closed_issues = github.list_issues(_repo(project), state="closed", limit=200)
    except github.GitHubError as exc:
        raise HTTPException(502, str(exc)) from exc

    linked = {
        t.github_issue_number: t
        for t in session.exec(
            select(Task).where(Task.project_id == project_id, Task.github_issue_number.is_not(None))
        ).all()
    }
    last_pos = session.exec(select(func.max(Task.position)).where(Task.project_id == project_id)).one() or 0
    created = updated = closed = 0

    for issue in open_issues:
        task = linked.get(issue["number"])
        if task is None:
            last_pos += 1
            session.add(
                Task(
                    project_id=project_id,
                    title=issue["title"],
                    description=issue.get("body") or "",
                    status=TaskStatus.backlog,
                    position=last_pos,
                    github_issue_number=issue["number"],
                    github_url=issue["url"],
                )
            )
            created += 1
        elif task.title != issue["title"]:
            task.title = issue["title"]
            task.updated_at = utcnow()
            session.add(task)
            updated += 1

    for issue in closed_issues:
        task = linked.get(issue["number"])
        if task is not None and task.status != TaskStatus.done:
            task.status = TaskStatus.done
            task.updated_at = utcnow()
            session.add(task)
            closed += 1

    session.commit()
    return {"created": created, "updated": updated, "closed": closed}
