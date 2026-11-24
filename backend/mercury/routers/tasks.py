from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlmodel import select

from ..models import Task, TaskCreate, TaskUpdate, utcnow
from ..services.mercury_tools import next_position
from .deps import SessionDep, get_project_or_404

router = APIRouter(prefix="/api", tags=["tasks"])


@router.get("/projects/{project_id}/tasks", response_model=list[Task])
def list_tasks(project_id: int, session: SessionDep):
    get_project_or_404(session, project_id)
    return session.exec(select(Task).where(Task.project_id == project_id).order_by(Task.position, Task.id)).all()


@router.post("/projects/{project_id}/tasks", response_model=Task, status_code=201)
def create_task(project_id: int, body: TaskCreate, session: SessionDep):
    get_project_or_404(session, project_id)
    task = Task.model_validate(body, update={"project_id": project_id})
    if "position" not in body.model_fields_set:
        task.position = next_position(session, project_id, task.status)
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def _get_task(session, task_id: int) -> Task:
    task = session.get(Task, task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    return task


@router.get("/tasks/{task_id}", response_model=Task)
def get_task(task_id: int, session: SessionDep):
    return _get_task(session, task_id)


@router.patch("/tasks/{task_id}", response_model=Task)
def update_task(task_id: int, body: TaskUpdate, session: SessionDep):
    task = _get_task(session, task_id)
    task.sqlmodel_update(body.model_dump(exclude_unset=True))
    task.updated_at = utcnow()
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: int, session: SessionDep):
    session.delete(_get_task(session, task_id))
    session.commit()
