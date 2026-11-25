from __future__ import annotations

from fastapi import APIRouter

from ..models import Appearance
from ..services import appearance

router = APIRouter(prefix="/api/appearance", tags=["appearance"])


@router.get("", response_model=Appearance)
def get_appearance():
    return appearance.load()


@router.put("", response_model=Appearance)
def put_appearance(body: Appearance):
    return appearance.save(body)
