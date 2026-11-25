from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from ..db import get_engine
from ..models import (
    ACTIVE_RUN_STATUSES,
    AgentEvent,
    AgentFollowUp,
    AgentRun,
    AgentRunCreate,
    ApprovalDecision,
    OrganizeRequest,
    PermissionMode,
    PermissionModeUpdate,
    Task,
)
from ..services import attachments, organizer, preferences
from .deps import RunsDep, SessionDep, get_project_or_404

router = APIRouter(prefix="/api", tags=["agents"])


def _event_dict(e: AgentEvent) -> dict:
    return {
        "type": "event",
        "seq": e.seq,
        "kind": e.kind,
        "payload": json.loads(e.payload),
        "created_at": e.created_at.isoformat(),
    }


def _save_images(images: list[str]) -> list[str]:
    try:
        return attachments.save_images(images)
    except attachments.AttachmentError as exc:
        raise HTTPException(400, str(exc)) from exc


def _get_run(session, run_id: int) -> AgentRun:
    run = session.get(AgentRun, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    return run


@router.get("/projects/{project_id}/runs", response_model=list[AgentRun])
def list_runs(project_id: int, session: SessionDep, task_id: int | None = None):
    get_project_or_404(session, project_id)
    query = select(AgentRun).where(AgentRun.project_id == project_id)
    if task_id is not None:
        query = query.where(AgentRun.task_id == task_id)
    return session.exec(query.order_by(AgentRun.id.desc())).all()


@router.post("/projects/{project_id}/runs", response_model=AgentRun, status_code=201)
async def create_run(project_id: int, body: AgentRunCreate, session: SessionDep, runs: RunsDep):
    project = get_project_or_404(session, project_id)
    if not project.repo_path:
        raise HTTPException(400, "Set a local repository folder for this project before running an agent.")
    if body.task_id is not None:
        task = session.get(Task, body.task_id)
        if task is None or task.project_id != project_id:
            raise HTTPException(400, "Task does not belong to this project.")
    if not body.prompt.strip() and not body.images:
        raise HTTPException(400, "Prompt is empty.")
    images = _save_images(body.images)
    mode = body.permission_mode or project.default_permission_mode or preferences.load().default_permission_mode
    run = AgentRun.model_validate(body, update={"project_id": project_id, "images": images, "permission_mode": mode})
    session.add(run)
    session.commit()
    session.refresh(run)
    runs.start(run.id)
    return run


@router.post("/projects/{project_id}/organize", response_model=AgentRun, status_code=201)
async def organize(project_id: int, body: OrganizeRequest, session: SessionDep, runs: RunsDep, response: Response):
    """Start an organizer run for the board or the docs, or return the one already running."""
    get_project_or_404(session, project_id)
    purpose = organizer.purpose_for(body.target)
    active = session.exec(
        select(AgentRun)
        .where(
            AgentRun.project_id == project_id,
            AgentRun.purpose == purpose,
            AgentRun.status.in_(list(ACTIVE_RUN_STATUSES)),
        )
        .order_by(AgentRun.id.desc())
    ).first()
    if active is not None:
        response.status_code = 200
        return active
    run = AgentRun(
        project_id=project_id,
        purpose=purpose,
        prompt=organizer.PROMPTS[purpose],
        # Organizer tools are all pre-approved, so this mode never stops to ask; anything else would.
        permission_mode=PermissionMode.default,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    runs.start(run.id)
    return run


@router.get("/runs/{run_id}", response_model=AgentRun)
def get_run(run_id: int, session: SessionDep):
    return _get_run(session, run_id)


@router.get("/runs/{run_id}/events")
def run_events(run_id: int, session: SessionDep):
    _get_run(session, run_id)
    events = session.exec(select(AgentEvent).where(AgentEvent.run_id == run_id).order_by(AgentEvent.seq)).all()
    return [_event_dict(e) for e in events]


@router.post("/runs/{run_id}/cancel", response_model=AgentRun)
async def cancel_run(run_id: int, session: SessionDep, runs: RunsDep):
    run = _get_run(session, run_id)
    if run.status not in ACTIVE_RUN_STATUSES:
        raise HTTPException(409, f"Run is already {run.status.value}.")
    await runs.cancel(run_id)
    session.refresh(run)
    return run


@router.post("/runs/{run_id}/follow-up", response_model=AgentRun, status_code=201)
async def follow_up(run_id: int, body: AgentFollowUp, session: SessionDep, runs: RunsDep):
    parent = _get_run(session, run_id)
    if parent.status in ACTIVE_RUN_STATUSES:
        raise HTTPException(409, "Wait for this run to finish before following up.")
    if not parent.session_id:
        raise HTTPException(409, "This run has no session to continue.")
    if not body.prompt.strip() and not body.images:
        raise HTTPException(400, "Message is empty.")
    images = _save_images(body.images)
    run = AgentRun(
        project_id=parent.project_id,
        task_id=parent.task_id,
        parent_run_id=parent.id,
        purpose=parent.purpose,
        prompt=body.prompt,
        images=images,
        model=parent.model,
        permission_mode=body.permission_mode or parent.permission_mode,
        max_budget_usd=parent.max_budget_usd,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    runs.start(run.id)
    return run


@router.get("/attachments/{name}")
def get_attachment(name: str):
    """A pasted image, for the transcript. Names are random, so they can't be guessed."""
    path = attachments.path_for(name)
    if path is None:
        raise HTTPException(404, "Attachment not found")
    return FileResponse(
        path,
        media_type=attachments.media_type_for(name),
        headers={"Cache-Control": "private, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/runs/{run_id}/permission-mode", response_model=AgentRun)
async def set_permission_mode(run_id: int, body: PermissionModeUpdate, session: SessionDep, runs: RunsDep):
    run = _get_run(session, run_id)
    if run.status not in ACTIVE_RUN_STATUSES or not runs.is_running(run_id):
        raise HTTPException(409, "Only an active run's permissions can be changed. Pick a mode for your follow-up instead.")
    await runs.set_permission_mode(run_id, body.permission_mode)
    session.refresh(run)
    return run


@router.post("/runs/{run_id}/approvals/{approval_id}")
def decide_approval(run_id: int, approval_id: str, body: ApprovalDecision, runs: RunsDep):
    if not runs.resolve_approval(run_id, approval_id, body.allow, body.message):
        raise HTTPException(404, "No pending approval with that id.")
    return {"ok": True}


@router.websocket("/runs/{run_id}/stream")
async def stream_run(websocket: WebSocket, run_id: int):
    runs = websocket.app.state.runs
    await websocket.accept()
    # Subscribe before reading history so nothing falls in the gap; the client dedupes by seq.
    queue = runs.subscribe(run_id)
    try:
        with Session(get_engine()) as s:
            run = s.get(AgentRun, run_id)
            if run is None:
                await websocket.close(code=4404, reason="Run not found")
                return
            events = s.exec(select(AgentEvent).where(AgentEvent.run_id == run_id).order_by(AgentEvent.seq)).all()
            await websocket.send_json({"type": "run", "run": json.loads(run.model_dump_json())})
            for e in events:
                await websocket.send_json(_event_dict(e))
        while True:
            await websocket.send_json(await queue.get())
    except WebSocketDisconnect:
        pass
    finally:
        runs.unsubscribe(run_id, queue)
