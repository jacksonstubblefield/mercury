from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlmodel import select

from ..models import Task, TaskCreate, TaskUpdate, utcnow
from ..services import attachments
from ..services.mercury_tools import next_position
from .deps import SessionDep, get_project_or_404

router = APIRouter(prefix="/api", tags=["tasks"])

# Pictures one task can hold. A run on the task sends them all with its first message.
MAX_TASK_IMAGES = attachments.MAX_IMAGES


def _save_images(images: list[str], room: int) -> list[str]:
    if not images:
        return []
    try:
        return attachments.save_images(images, limit=max(room, 0), where="one task")
    except attachments.AttachmentError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/projects/{project_id}/tasks", response_model=list[Task])
def list_tasks(project_id: int, session: SessionDep):
    get_project_or_404(session, project_id)
    return session.exec(select(Task).where(Task.project_id == project_id).order_by(Task.position, Task.id)).all()


@router.post("/projects/{project_id}/tasks", response_model=Task, status_code=201)
def create_task(project_id: int, body: TaskCreate, session: SessionDep):
    get_project_or_404(session, project_id)
    task = Task.model_validate(body.model_dump(exclude={"add_images"}), update={"project_id": project_id})
    if "position" not in body.model_fields_set:
        task.position = next_position(session, project_id, task.status)
    task.images = _save_images(body.add_images, MAX_TASK_IMAGES)
    session.add(task)
    try:
        session.commit()
    except Exception:
        attachments.delete(task.images)
        raise
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
    changes = body.model_dump(exclude_unset=True, exclude={"images", "add_images"})
    current = list(task.images or [])
    kept = current
    if body.images is not None:
        unknown = [n for n in body.images if n not in current]
        if unknown:
            raise HTTPException(400, "Some of those pictures aren't attached to this task. Reload it and try again.")
        kept = list(dict.fromkeys(body.images))
    removed = [n for n in current if n not in kept]
    added = _save_images(body.add_images, MAX_TASK_IMAGES - len(kept))
    task.sqlmodel_update(changes)
    task.images = kept + added  # a new list, so the JSON column is saved
    task.updated_at = utcnow()
    session.add(task)
    try:
        session.commit()
    except Exception:
        attachments.delete(added)
        raise
    attachments.delete(removed)
    session.refresh(task)
    return task


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: int, session: SessionDep):
    task = _get_task(session, task_id)
    images = list(task.images or [])
    session.delete(task)
    session.commit()
    attachments.delete(images)
