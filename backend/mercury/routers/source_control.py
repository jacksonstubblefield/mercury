"""Source control (git) for a project's local checkout: the Source Control tab."""

from __future__ import annotations

import asyncio
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException

from ..models import Project, ScmCommit, ScmPaths
from ..services import commit_message
from ..services import source_control as scm
from ..services.gitinfo import GitError
from .deps import RunsDep, SessionDep, get_project_or_404

router = APIRouter(prefix="/api/projects/{project_id}/scm", tags=["source control"])


def _repo_path(project: Project) -> str:
    if not project.repo_path:
        raise HTTPException(400, "This project has no local repository folder. Set one in Settings.")
    return project.repo_path


def _root(session, project_id: int) -> str:
    project = get_project_or_404(session, project_id)
    try:
        return scm.require_root(_repo_path(project))
    except GitError as exc:
        raise HTTPException(400, str(exc)) from exc


def _git_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except GitError as exc:
        # 409: the request was fine but git refused (conflicts, rejected push, nothing staged, ...).
        raise HTTPException(409, str(exc)) from exc


def _with_status(root: str, extra: dict | None = None) -> dict:
    return {**(extra or {}), "status": _git_call(scm.status, root)}


@router.get("/status")
def get_status(project_id: int, session: SessionDep):
    project = get_project_or_404(session, project_id)
    try:
        root = scm.repo_root(_repo_path(project))
    except GitError as exc:
        raise HTTPException(400, str(exc)) from exc
    if root is None:
        return {"is_repo": False}
    return _git_call(scm.status, root)


@router.post("/init")
def init_repo(project_id: int, session: SessionDep):
    project = get_project_or_404(session, project_id)
    path = _repo_path(project)
    if _git_call(scm.repo_root, path) is not None:
        raise HTTPException(409, "This folder is already in a git repository.")
    _git_call(scm.init, path)
    return _git_call(scm.status, scm.require_root(path))


@router.get("/diff")
def get_diff(
    project_id: int,
    session: SessionDep,
    path: str,
    kind: Literal["staged", "unstaged", "untracked"],
    orig_path: Optional[str] = None,
):
    return _git_call(scm.file_diff, _root(session, project_id), path, kind, orig_path)


@router.get("/file-changes")
def get_file_changes(project_id: int, session: SessionDep, path: str):
    """One file's changes since the last commit. `path` may be absolute, as agents report it."""
    return _git_call(scm.changes_since_commit, _root(session, project_id), path)


@router.get("/file")
def get_file(project_id: int, session: SessionDep, path: str):
    """A file's current contents (inside the repository only)."""
    return _git_call(scm.read_file, _root(session, project_id), path)


@router.post("/stage")
def stage(project_id: int, body: ScmPaths, session: SessionDep):
    root = _root(session, project_id)
    _git_call(scm.stage, root, body.paths)
    return _git_call(scm.status, root)


@router.post("/unstage")
def unstage(project_id: int, body: ScmPaths, session: SessionDep):
    root = _root(session, project_id)
    _git_call(scm.unstage, root, body.paths)
    return _git_call(scm.status, root)


@router.post("/discard")
def discard(project_id: int, body: ScmPaths, session: SessionDep):
    root = _root(session, project_id)
    _git_call(scm.discard, root, body.paths)
    return _git_call(scm.status, root)


@router.post("/commit")
def commit(project_id: int, body: ScmCommit, session: SessionDep):
    root = _root(session, project_id)
    result = _git_call(scm.commit, root, body.message, body.stage_all)
    if body.push:
        try:
            result["output"] += "\n" + scm.push(root)["output"]
        except GitError as exc:
            # The commit happened; report the push failure alongside the new status.
            return _with_status(root, {**result, "error": f"Committed {result['sha']}, but the push failed:\n{exc}"})
    return _with_status(root, result)


@router.post("/push")
def push(project_id: int, session: SessionDep):
    root = _root(session, project_id)
    return _with_status(root, _git_call(scm.push, root))


@router.post("/pull")
def pull(project_id: int, session: SessionDep):
    root = _root(session, project_id)
    return _with_status(root, _git_call(scm.pull, root))


@router.post("/fetch")
def fetch(project_id: int, session: SessionDep):
    root = _root(session, project_id)
    return _with_status(root, _git_call(scm.fetch, root))


@router.post("/generate-message")
async def generate_message(project_id: int, session: SessionDep, runs: RunsDep):
    path = _repo_path(get_project_or_404(session, project_id))
    root = await asyncio.to_thread(_git_call, scm.require_root, path)
    ctx = await asyncio.to_thread(_git_call, scm.changes_for_message, root)
    if not ctx["diff"].strip() and not ctx["stat"]:
        raise HTTPException(409, "There are no changes to describe.")
    try:
        message = await commit_message.generate(root, ctx, runs.client_factory)
    except Exception as exc:  # SDK/CLI failures (not logged in, network, ...) become a readable error
        raise HTTPException(502, f"Couldn't generate a commit message: {exc}") from exc
    return {"message": message}
