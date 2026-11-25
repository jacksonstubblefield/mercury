from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response

from ..models import Appearance, Theme, ThemeCreate, ThemeUpdate
from ..services import appearance, themes

router = APIRouter(prefix="/api/appearance", tags=["appearance"])


@router.get("", response_model=Appearance)
def get_appearance():
    return appearance.load()


@router.put("", response_model=Appearance)
def put_appearance(body: Appearance):
    return appearance.save(body)


# --------------------------------------------------------------------------- themes
# System themes are listed but can't be changed or deleted (403).


def _fail(exc: themes.ThemeError):
    raise HTTPException(status_code=exc.status, detail=str(exc)) from None


@router.get("/themes", response_model=list[Theme])
def list_themes():
    return themes.all_themes()


@router.post("/themes", response_model=Theme, status_code=201)
def create_theme(body: ThemeCreate):
    try:
        return themes.create(body.name, body.colors, body.based_on)
    except themes.ThemeError as exc:
        _fail(exc)


@router.patch("/themes/{theme_id}", response_model=Theme)
def update_theme(theme_id: str, body: ThemeUpdate):
    try:
        return themes.update(theme_id, body.name, body.colors)
    except themes.ThemeError as exc:
        _fail(exc)


@router.delete("/themes/{theme_id}", status_code=204)
def delete_theme(theme_id: str):
    try:
        themes.delete(theme_id)
    except themes.ThemeError as exc:
        _fail(exc)
    return Response(status_code=204)
