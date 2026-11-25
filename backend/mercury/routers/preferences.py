from __future__ import annotations

from fastapi import APIRouter

from ..models import Preferences
from ..services import preferences

router = APIRouter(prefix="/api/preferences", tags=["preferences"])


@router.get("", response_model=Preferences)
def get_preferences():
    return preferences.load()


@router.put("", response_model=Preferences)
def put_preferences(body: Preferences):
    return preferences.save(body)
