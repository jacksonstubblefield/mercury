"""App-wide settings, stored as JSON next to the database (see services/appearance.py for why not localStorage).

The user edits them on the Settings page; agents read and change them with the get_settings and
update_settings tools (services/mercury_tools.py), as far as `Preferences.agent_settings_access` allows.
"""

from __future__ import annotations

import json
import logging
import os
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .. import config
from ..models import AgentSettingsAccess, Preferences

log = logging.getLogger(__name__)


# Settings only the user can change, so an agent can't widen its own access.
USER_ONLY = frozenset({"agent_settings_access"})


class SettingsError(ValueError):
    """A change that can't be applied; the message is meant for whoever asked for it."""


def _path() -> Path:
    return config.data_dir() / "preferences.json"


def load() -> Preferences:
    """The saved preferences, or the defaults when there are none or the file can't be used."""
    path = _path()
    try:
        return Preferences.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return Preferences()
    except (OSError, ValueError, ValidationError) as exc:  # json.JSONDecodeError is a ValueError
        log.warning("ignoring unreadable %s: %s", path, exc)
        return Preferences()


def save(prefs: Preferences) -> Preferences:
    path = _path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(prefs.model_dump(mode="json"), indent=2), encoding="utf-8")
    os.replace(tmp, path)  # atomic, so a crash mid-write can't leave half a file
    return prefs


def apply_changes(changes: dict[str, Any], *, by_agent: bool = False) -> Preferences:
    """Merge `changes` into the saved settings, validate and save. Raises SettingsError."""
    problem = change_problem(changes, by_agent=by_agent)
    if problem:
        raise SettingsError(problem)
    merged = {**load().model_dump(mode="json"), **changes}
    try:
        prefs = Preferences.model_validate(merged)
    except ValidationError as exc:
        details = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        raise SettingsError(f"Invalid value: {details}") from None
    return save(prefs)


def change_problem(changes: Any, *, by_agent: bool) -> str | None:
    """Why `changes` can't be applied without looking at the values, or None. Cheap enough to check before asking the user."""
    if not isinstance(changes, dict) or not changes:
        return "Pass changes as an object of setting keys to new values."
    unknown = sorted(k for k in changes if k not in Preferences.model_fields)
    if unknown:
        return f"Unknown setting(s): {', '.join(unknown)}. Known: {', '.join(Preferences.model_fields)}."
    if by_agent:
        access = load().agent_settings_access
        if access != AgentSettingsAccess.change:
            return (
                "The user doesn't let agents change Mercury's settings (Settings > Agent access is "
                f"{access.value!r}). Tell the user what to change instead."
            )
        locked = sorted(k for k in changes if k in USER_ONLY)
        if locked:
            return f"Only the user can change {', '.join(locked)}, on Mercury's Settings page."
    return None


def describe(prefs: Preferences | None = None) -> list[dict[str, Any]]:
    """Each setting with its value, default, allowed values and whether agents may change it."""
    prefs = prefs or load()
    out = []
    for key, field in Preferences.model_fields.items():
        value = getattr(prefs, key)
        entry: dict[str, Any] = {
            "key": key,
            "value": value.value if isinstance(value, Enum) else value,
            "default": field.default.value if isinstance(field.default, Enum) else field.default,
            "description": field.description or "",
        }
        if isinstance(field.annotation, type) and issubclass(field.annotation, Enum):
            entry["allowed_values"] = [m.value for m in field.annotation]
        entry["agent_can_change"] = key not in USER_ONLY
        out.append(entry)
    return out
