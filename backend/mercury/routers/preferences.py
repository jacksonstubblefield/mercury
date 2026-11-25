from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException

from ..models import Preferences
from ..services import preferences

router = APIRouter(prefix="/api/preferences", tags=["preferences"])


@router.get("", response_model=Preferences)
def get_preferences():
    return preferences.load()


@router.put("", response_model=Preferences)
def put_preferences(body: Preferences):
    return preferences.save(body)


@router.patch("", response_model=Preferences)
def patch_preferences(changes: dict[str, Any] = Body(...)):
    """Change only the settings given, so two editors (the Settings page, an agent) don't undo each other."""
    try:
        return preferences.apply_changes(changes)
    except preferences.SettingsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
