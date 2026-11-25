"""App-wide preferences, stored as JSON next to the database (see services/appearance.py for why not localStorage)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from pydantic import ValidationError

from .. import config
from ..models import Preferences

log = logging.getLogger(__name__)


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
