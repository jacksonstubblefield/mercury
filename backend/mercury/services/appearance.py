"""The UI theme, stored as JSON next to the database.

It lives on disk rather than in the browser's localStorage because the packaged app serves the UI
from a new random port on each launch, so localStorage (which is per origin) would start empty.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from pydantic import ValidationError

from .. import config
from ..models import Appearance

log = logging.getLogger(__name__)


def _path() -> Path:
    return config.data_dir() / "appearance.json"


def load() -> Appearance:
    """The saved theme, or the default when there is none or the file can't be used."""
    path = _path()
    try:
        return Appearance.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return Appearance()
    except (OSError, ValueError, ValidationError) as exc:  # json.JSONDecodeError is a ValueError
        log.warning("ignoring unreadable %s: %s", path, exc)
        return Appearance()


def save(appearance: Appearance) -> Appearance:
    path = _path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(appearance.model_dump(), indent=2), encoding="utf-8")
    os.replace(tmp, path)  # atomic, so a crash mid-write can't leave half a file
    return appearance
