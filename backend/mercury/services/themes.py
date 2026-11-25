"""Themes: the system palettes that ship with Mercury, and the user's own (custom) ones.

System themes are defined here and can't be changed or deleted. Custom themes are kept in themes.json
next to the database, apart from appearance.json, so the Appearance page autosaving the active theme can't
overwrite a theme an agent just created. The frontend gets every theme from GET /api/appearance/themes;
the only palette it hard-codes is Mercury Dark, for painting before the backend answers (styles.css too).

A theme sets every base token. The derived tokens are worked out in the frontend from the base ones
(theme.ts `derive`) unless a theme or the user's overrides pin them.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Optional

from pydantic import TypeAdapter, ValidationError

from .. import config
from ..models import Theme
from . import appearance

log = logging.getLogger(__name__)

# Token -> what it colors. Agents see these hints in get_appearance; keep them in step with TOKEN_GROUPS in theme.ts.
BASE_TOKENS: dict[str, str] = {
    "--bg": "Background: main area, inputs, code blocks",
    "--bg-elev": "Panels: sidebar, cards, dialogs",
    "--bg-elev-2": "Raised: buttons, selected items",
    "--bg-hover": "Hover: rows and buttons under the pointer",
    "--border": "Borders: dividers and outlines",
    "--text": "Text: body text and headings",
    "--text-dim": "Secondary text: labels, metadata",
    "--text-faint": "Faint text: hints, placeholders, line numbers",
    "--accent": "Accent: primary buttons, links, active tab",
    "--ok": "Success: finished runs, added lines",
    "--warn": "Warning: approvals, high priority",
    "--err": "Error: failures, deleted lines",
}
DERIVED_TOKENS: dict[str, str] = {
    "--accent-dim": "Accent, subtle: badges, highlights, focus rings (default: accent mixed into the background)",
    "--on-accent": "Text on accent: label on primary buttons (default: near-black or white, whichever reads better)",
}
TOKENS = {**BASE_TOKENS, **DERIVED_TOKENS}

DEFAULT_THEME_ID = "mercury-dark"
MAX_CUSTOM_THEMES = 50
MAX_NAME_CHARS = 60

_STATUS_DARK = {"--ok": "#4fd18b", "--warn": "#f0b44c", "--err": "#ff6b6b"}
_STATUS_LIGHT = {"--ok": "#1e8a50", "--warn": "#a86a00", "--err": "#d33a3a"}


def _system(theme_id: str, name: str, colors: dict[str, str]) -> Theme:
    assert set(colors) == set(BASE_TOKENS), theme_id
    return Theme(id=theme_id, name=name, system=True, colors=colors)


SYSTEM_THEMES: tuple[Theme, ...] = (
    _system("mercury-dark", "Mercury Dark", {
        "--bg": "#0f1115", "--bg-elev": "#161920", "--bg-elev-2": "#1d212a", "--bg-hover": "#232835",
        "--border": "#2a2f3b", "--text": "#e6e8ee", "--text-dim": "#9aa1b2", "--text-faint": "#6b7285",
        "--accent": "#7c8cff", **_STATUS_DARK,
    }),
    _system("mercury-light", "Mercury Light", {
        "--bg": "#fbfbfd", "--bg-elev": "#f1f2f6", "--bg-elev-2": "#e6e8ef", "--bg-hover": "#dfe2ea",
        "--border": "#d3d7e0", "--text": "#1d2130", "--text-dim": "#555d72", "--text-faint": "#868da0",
        "--accent": "#4f5fe0", **_STATUS_LIGHT,
    }),
    _system("hot-pink", "Hot Pink", {
        "--bg": "#1a0a14", "--bg-elev": "#250f1d", "--bg-elev-2": "#321428", "--bg-hover": "#3d1932",
        "--border": "#521f43", "--text": "#ffe8f4", "--text-dim": "#e9a6cb", "--text-faint": "#a86b8d",
        "--accent": "#ff3ea5", "--ok": "#5ee0a0", "--warn": "#ffc35c", "--err": "#ff7348",
    }),
    _system("midnight", "Midnight", {
        "--bg": "#0a0e17", "--bg-elev": "#111726", "--bg-elev-2": "#18203a", "--bg-hover": "#1f2947",
        "--border": "#263152", "--text": "#dce6f5", "--text-dim": "#8ea0bf", "--text-faint": "#5d6c8a",
        "--accent": "#4cc9f0", **_STATUS_DARK,
    }),
    _system("forest", "Forest", {
        "--bg": "#0d1310", "--bg-elev": "#131b17", "--bg-elev-2": "#1a251f", "--bg-hover": "#202f27",
        "--border": "#2a3b32", "--text": "#e1ebe4", "--text-dim": "#97ab9e", "--text-faint": "#66796d",
        "--accent": "#7fd49b", "--ok": "#b5e06a", "--warn": "#f0b44c", "--err": "#ff7b6b",
    }),
    _system("ember", "Ember", {
        "--bg": "#14100e", "--bg-elev": "#1c1613", "--bg-elev-2": "#251d19", "--bg-hover": "#2e241f",
        "--border": "#3a2e27", "--text": "#f0e6df", "--text-dim": "#b2a194", "--text-faint": "#7d6e63",
        "--accent": "#ff9f68", **_STATUS_DARK,
    }),
    _system("paper", "Paper", {
        "--bg": "#fffdf8", "--bg-elev": "#f6f2e9", "--bg-elev-2": "#ece6da", "--bg-hover": "#e5ddce",
        "--border": "#d9cfbd", "--text": "#2b2620", "--text-dim": "#6b6153", "--text-faint": "#9a8f7f",
        "--accent": "#c2410c", **_STATUS_LIGHT,
    }),
)
_SYSTEM_BY_ID = {t.id: t for t in SYSTEM_THEMES}

_HEX = re.compile(r"#[0-9a-fA-F]{6}")
_lock = threading.RLock()  # themes.json is read-modify-written by the API (threads) and agent tools
_custom_list = TypeAdapter(list[Theme])


class ThemeError(ValueError):
    """A theme change that can't be made. The message is meant for whoever asked; `status` is the HTTP code."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------- storage


def _path() -> Path:
    return config.data_dir() / "themes.json"


def _load_custom() -> list[Theme]:
    path = _path()
    try:
        themes = _custom_list.validate_python(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return []
    except (OSError, ValueError, ValidationError) as exc:  # json.JSONDecodeError is a ValueError
        log.warning("ignoring unreadable %s: %s", path, exc)
        return []
    # Never let a hand-edited file shadow or claim to be a system theme.
    out = []
    for t in themes:
        if t.id in _SYSTEM_BY_ID or any(t.id == o.id for o in out):
            continue
        out.append(t.model_copy(update={"system": False}))
    return out


def _save_custom(themes: list[Theme]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps([t.model_dump() for t in themes], indent=2), encoding="utf-8")
    os.replace(tmp, path)  # atomic, so a crash mid-write can't leave half a file


# --------------------------------------------------------------------------- reading


def all_themes() -> list[Theme]:
    """System themes first, then the user's in the order they were made."""
    return [*SYSTEM_THEMES, *_load_custom()]


def get(theme_id: Any) -> Optional[Theme]:
    if not isinstance(theme_id, str):
        return None
    return _SYSTEM_BY_ID.get(theme_id) or next((t for t in _load_custom() if t.id == theme_id), None)


def default_theme() -> Theme:
    return _SYSTEM_BY_ID[DEFAULT_THEME_ID]


def resolve(a: Any = None) -> dict[str, str]:
    """The colors an appearance shows: its theme's (Mercury Dark if the theme is gone), then the user's overrides.

    Derived tokens are only included when the theme or the overrides pin them.
    """
    a = a or appearance.load()
    theme = get(a.preset) or default_theme()
    return {**theme.colors, **{k: v for k, v in a.colors.items() if k in TOKENS}}


# --------------------------------------------------------------------------- checks


def known_problem(theme_id: Any) -> Optional[str]:
    if get(theme_id) is None:
        return f"There's no theme {theme_id!r}. Use get_appearance to see the theme ids."
    return None


def change_problem(theme_id: Any) -> Optional[str]:
    """Why theme `theme_id` can't be changed or deleted, or None."""
    problem = known_problem(theme_id)
    if problem:
        return problem
    if theme_id in _SYSTEM_BY_ID:
        return (
            f"{theme_id!r} is a system theme, which can't be changed or deleted. Make a custom theme from it "
            f"with create_theme (based_on={theme_id!r}) instead."
        )
    return None


def _clean_name(name: Any) -> str:
    name = " ".join(str(name or "").split())
    if not name:
        raise ThemeError("A theme needs a name.")
    if len(name) > MAX_NAME_CHARS:
        raise ThemeError(f"Theme names are at most {MAX_NAME_CHARS} characters.")
    return name


def _check_name_free(name: str, themes: list[Theme], except_id: Optional[str] = None) -> None:
    for t in themes:
        if t.id != except_id and t.name.casefold() == name.casefold():
            raise ThemeError(f"There's already a theme called {t.name!r} ({t.id}). Pick another name.", 409)


def _check_colors(colors: Any, *, allow_null: bool = False) -> dict[str, Optional[str]]:
    if colors is None:
        return {}
    if not isinstance(colors, dict):
        raise ThemeError("colors must be an object of color tokens to #rrggbb colors.")
    unknown = sorted(k for k in colors if k not in TOKENS)
    if unknown:
        raise ThemeError(f"Unknown color token(s): {', '.join(unknown)}. Known: {', '.join(TOKENS)}.")
    out: dict[str, Optional[str]] = {}
    for k, v in colors.items():
        if v is None and allow_null:
            out[k] = None
        elif isinstance(v, str) and _HEX.fullmatch(v):
            out[k] = v.lower()
        else:
            raise ThemeError(f"{k} must be a #rrggbb color, got {v!r}.")
    return out


def _new_id(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32].strip("-") or "theme"
    candidate, n = base, 2
    while candidate in taken:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


# --------------------------------------------------------------------------- changing


def create(name: Any, colors: Any = None, based_on: Any = None) -> Theme:
    """A new custom theme: `based_on`'s colors (default Mercury Dark) with `colors` on top."""
    name = _clean_name(name)
    changes = _check_colors(colors)
    with _lock:
        if based_on is None:
            base = default_theme()
        else:
            base = get(based_on)
            if base is None:
                raise ThemeError(known_problem(based_on) or "No such theme.", 404)
        custom = _load_custom()
        if len(custom) >= MAX_CUSTOM_THEMES:
            raise ThemeError(f"You can keep at most {MAX_CUSTOM_THEMES} custom themes; delete one first.", 409)
        _check_name_free(name, [*SYSTEM_THEMES, *custom])
        theme = Theme(
            id=_new_id(name, {t.id for t in SYSTEM_THEMES} | {t.id for t in custom}),
            name=name,
            colors={**base.colors, **changes},  # type: ignore[dict-item]
        )
        _save_custom([*custom, theme])
        return theme


def update(theme_id: Any, name: Any = None, colors: Any = None) -> Theme:
    """Rename a custom theme and/or merge `colors` into it (null drops a pinned derived token)."""
    changes = _check_colors(colors, allow_null=True)
    cleared_base = sorted(k for k, v in changes.items() if v is None and k in BASE_TOKENS)
    if cleared_base:
        raise ThemeError(f"Every theme sets {', '.join(cleared_base)}; give a color instead of null.")
    with _lock:
        problem = change_problem(theme_id)
        if problem:
            raise ThemeError(problem, 404 if get(theme_id) is None else 403)
        custom = _load_custom()
        i = next(i for i, t in enumerate(custom) if t.id == theme_id)
        theme = custom[i]
        new_name = theme.name
        if name is not None:
            new_name = _clean_name(name)
            _check_name_free(new_name, [*SYSTEM_THEMES, *custom], except_id=theme.id)
        merged = {**theme.colors, **changes}
        theme = theme.model_copy(update={"name": new_name, "colors": {k: v for k, v in merged.items() if v is not None}})
        custom[i] = theme
        _save_custom(custom)
        return theme


def delete(theme_id: Any) -> tuple[Theme, bool]:
    """Delete a custom theme. Returns it and whether it was in use (the appearance then goes back to Mercury Dark)."""
    with _lock:
        problem = change_problem(theme_id)
        if problem:
            raise ThemeError(problem, 404 if get(theme_id) is None else 403)
        custom = _load_custom()
        theme = next(t for t in custom if t.id == theme_id)
        _save_custom([t for t in custom if t.id != theme_id])
        current = appearance.load()
        in_use = current.preset == theme_id
        if in_use:
            # Keep fonts and corners; the user's color tweaks belonged to the deleted theme.
            appearance.save(current.model_copy(update={"preset": DEFAULT_THEME_ID, "colors": {}}))
        return theme, in_use


APPEARANCE_KEYS = ("theme", "colors", "clear_colors", "font_ui", "font_mono", "radius")


def set_appearance(changes: dict[str, Any]) -> Any:
    """Change the active appearance; only the keys given change.

    theme: switch to this theme id (clears the user's color tweaks unless `colors` is given too).
    colors: overrides merged on top of the theme (null removes one). clear_colors: drop all overrides first.
    font_ui / font_mono / radius: null goes back to the default.
    """
    if not isinstance(changes, dict) or not changes:
        raise ThemeError("Pass at least one of: " + ", ".join(APPEARANCE_KEYS) + ".")
    unknown = sorted(k for k in changes if k not in APPEARANCE_KEYS)
    if unknown:
        raise ThemeError(f"Unknown key(s): {', '.join(unknown)}. Known: {', '.join(APPEARANCE_KEYS)}.")
    overrides = _check_colors(changes.get("colors"), allow_null=True)
    with _lock:
        current = appearance.load()
        data = current.model_dump()
        if "theme" in changes:
            problem = known_problem(changes["theme"])
            if problem:
                raise ThemeError(problem, 404)
            if changes["theme"] != current.preset:
                data["preset"] = changes["theme"]
                data["colors"] = {}
        if changes.get("clear_colors"):
            data["colors"] = {}
        merged = {**data["colors"], **overrides}
        data["colors"] = {k: v for k, v in merged.items() if v is not None}
        for key in ("font_ui", "font_mono", "radius"):
            if key in changes:
                data[key] = changes[key]
        try:
            new = type(current).model_validate(data)
        except ValidationError as exc:
            details = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
            raise ThemeError(f"Invalid value: {details}") from None
        return appearance.save(new)
