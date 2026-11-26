"""Claude Code plan usage: the 5-hour session limit and the weekly limits.

Reads the sign-in Claude Code keeps (`<config dir>/.credentials.json`, or the macOS Keychain)
and asks Anthropic's usage endpoint, the same one Claude Code's `/usage` and the VS Code
extension read. Mercury only reads the sign-in. It never refreshes the token, because a
refresh rotates it and would sign the user's own Claude Code out.

The endpoint rate limits hard, so Mercury asks it rarely: readings are reused for minutes and
a failure is waited out (longer each time it repeats), apart from the occasional refresh. The
last good reading is always shown until a newer one arrives. All of that is saved to the data
dir, so restarting Mercury keeps the number and doesn't ask again early.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Optional

from pydantic import ValidationError

from .. import config
from ..models import ClaudeUsage, UsageWindow, utcnow

log = logging.getLogger(__name__)

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
OAUTH_BETA = "oauth-2025-04-20"
TIMEOUT_S = 10

# How long a good reading is reused. A refresh (see get_usage) may skip this, but never asks more
# often than MIN_REFRESH_S. The UI can poll as often as it likes: these limit the calls to Anthropic.
CACHE_TTL_S = 5 * 60
MIN_REFRESH_S = 60
# The wait before the next timed try after a failed call, doubled for each failure in a row.
# A longer Retry-After from Anthropic wins, up to MAX_RETRY_AFTER_S.
BACKOFF_S = 5 * 60
MAX_BACKOFF_S = 30 * 60
MAX_RETRY_AFTER_S = 60 * 60

# Response keys shown in the UI, in order, with their window length (for the time marker).
WINDOWS: list[tuple[str, str, int]] = [
    ("five_hour", "Session (5hr)", 5 * 3600),
    ("seven_day", "Weekly (7 day)", 7 * 86400),
    ("seven_day_opus", "Weekly · Opus", 7 * 86400),
    ("seven_day_sonnet", "Weekly · Sonnet", 7 * 86400),
]


class UsageHTTPError(Exception):
    def __init__(self, status: int, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after  # seconds Anthropic asked us to wait


def config_dir() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(base) if base else Path.home() / ".claude"


def _read_credentials() -> Optional[dict[str, Any]]:
    """The `claudeAiOauth` block Claude Code saved at sign-in, or None."""
    path = config_dir() / ".credentials.json"
    raw: Optional[str] = None
    if path.is_file():
        raw = path.read_text(encoding="utf-8")
    elif sys.platform == "darwin" and not os.environ.get("CLAUDE_CONFIG_DIR"):
        # On macOS Claude Code keeps the sign-in in the Keychain. A custom config dir uses
        # a different Keychain entry, so only the default one is read.
        try:
            out = subprocess.run(
                ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        raw = out.stdout if out.returncode == 0 else None
    if not raw:
        return None
    try:
        oauth = json.loads(raw).get("claudeAiOauth")
    except (ValueError, AttributeError):
        return None
    return oauth if isinstance(oauth, dict) and oauth.get("accessToken") else None


def _retry_after(value: Optional[str]) -> Optional[float]:
    """A Retry-After header (seconds, or an HTTP date) as seconds from now."""
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(value).timestamp() - time.time()
    except (TypeError, ValueError):
        return None


def _fetch(token: str) -> dict[str, Any]:
    req = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": OAUTH_BETA,
            "Accept": "application/json",
            "User-Agent": "mercury",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        message = (
            "Anthropic is rate limiting usage checks (HTTP 429)."
            if exc.code == 429
            else f"Anthropic returned HTTP {exc.code}."
        )
        raise UsageHTTPError(exc.code, message, _retry_after(exc.headers.get("Retry-After"))) from exc


def _parse_time(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse(data: dict[str, Any]) -> list[UsageWindow]:
    windows = []
    for key, label, seconds in WINDOWS:
        w = data.get(key)
        if not isinstance(w, dict) or not isinstance(w.get("utilization"), (int, float)):
            continue
        windows.append(
            UsageWindow(
                key=key,
                label=label,
                utilization=max(0.0, float(w["utilization"])),
                resets_at=_parse_time(w.get("resets_at")),
                window_seconds=seconds,
            )
        )
    return windows


# --------------------------------------------------------------------------- saved state
#
# One JSON object, kept in memory and in <data dir>/claude_usage.json. Times are Unix seconds.
#   reading       the last good ClaudeUsage; only a newer one replaces it
#   token         fingerprint of the sign-in last asked with (never the token itself)
#   attempted_at  when that sign-in was last asked
#   failures      failed calls in a row, with `retry_at`, and the `status` and `error` to show
#   hold_until    Anthropic's Retry-After: not even a refresh asks before this

_lock = threading.Lock()
_state: Optional[dict[str, Any]] = None  # None: not loaded from disk yet


def _path() -> Path:
    return config.data_dir() / "claude_usage.json"


def _load() -> dict[str, Any]:
    try:
        state = json.loads(_path().read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError("not a JSON object")
        if "reading" in state:
            ClaudeUsage.model_validate(state["reading"])
        float(state.get("attempted_at", 0)), float(state.get("hold_until", 0))
        if "retry_at" in state:
            float(state["retry_at"]), int(state["failures"]), str(state["status"]), str(state["error"])
        return state
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, TypeError, KeyError, ValidationError) as exc:
        log.warning("ignoring unreadable %s: %s", _path(), exc)
        return {}


def _save(state: dict[str, Any]) -> None:
    path = _path()
    try:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:  # the copy in memory still limits calls until Mercury restarts
        log.warning("couldn't save %s: %s", path, exc)


def reset_cache() -> None:
    """Drop the copy in memory, as a restart does. The saved file stays."""
    global _state
    with _lock:
        _state = None


def _last_reading(
    state: dict[str, Any],
    status: str,
    plan: Optional[str],
    message: Optional[str],
    retry_at: Optional[datetime] = None,
) -> ClaudeUsage:
    """The saved reading, however old, with `message` saying why it isn't newer. Without one, just the problem."""
    if "reading" in state:
        usage = ClaudeUsage.model_validate(state["reading"])
        usage.message, usage.retry_at = message, retry_at
        return usage
    return ClaudeUsage(status=status, plan=plan, message=message, retry_at=retry_at)


def _answer(state: dict[str, Any], plan: Optional[str]) -> ClaudeUsage:
    """What to show without asking Anthropic."""
    status = state.get("status", "error")
    # No retry time for a rejected sign-in: it needs the user, not a wait.
    waiting = "retry_at" in state and status == "error"
    retry_at = datetime.fromtimestamp(state["retry_at"], timezone.utc) if waiting else None
    return _last_reading(state, status, plan, state.get("error"), retry_at)


def get_usage(refresh: bool = False) -> ClaudeUsage:
    """The plan limits, asking Anthropic if it's time to.

    Without `refresh` that's every CACHE_TTL_S, or when the wait after a failure is over. A
    `refresh` (the user opened the Usage page or pressed Refresh, or a session succeeded)
    asks sooner, even during that wait, limited only by MIN_REFRESH_S and any Retry-After.
    """
    global _state
    creds = _read_credentials()
    with _lock:
        if _state is None:
            _state = _load()
        if creds is None:
            return _last_reading(
                _state, "signed_out", None,
                "Claude Code isn't signed in with a Claude subscription on this machine.",
            )
        plan = creds.get("subscriptionType") if isinstance(creds.get("subscriptionType"), str) else None
        expires_ms = creds.get("expiresAt")
        if isinstance(expires_ms, (int, float)) and expires_ms / 1000 < time.time():
            return _last_reading(
                _state, "expired", plan,
                "Claude Code's sign-in has expired. Run `claude` once to renew it, then refresh.",
            )

        token = creds["accessToken"]
        fingerprint = hashlib.sha256(token.encode()).hexdigest()
        if _state.get("token") != fingerprint:
            # A new sign-in or a renewed token is asked right away. Only the reading carries over.
            _state = {k: v for k, v in _state.items() if k == "reading"} | {"token": fingerprint}

        now = time.time()
        if "attempted_at" in _state:
            if refresh:
                due = max(_state["attempted_at"] + MIN_REFRESH_S, _state.get("hold_until", 0))
            else:
                due = _state.get("retry_at", _state["attempted_at"] + CACHE_TTL_S)
            if now < due:
                return _answer(_state, plan)

        status, retry_after = "error", None
        try:
            data = _fetch(token)
        except UsageHTTPError as exc:
            error, retry_after = str(exc), exc.retry_after
            if exc.status in (401, 403):
                status = "expired"
                error = "Anthropic didn't accept Claude Code's sign-in. Run `claude` once to renew it, then refresh."
        except (OSError, ValueError) as exc:
            error = f"Couldn't reach Anthropic: {exc}"
        else:
            usage = ClaudeUsage(status="ok", plan=plan, windows=parse(data), fetched_at=utcnow())
            _state = {"token": fingerprint, "reading": usage.model_dump(mode="json"), "attempted_at": now}
            _save(_state)
            return usage

        failures = _state.get("failures", 0) + 1
        wait = min(BACKOFF_S * 2 ** min(failures - 1, 10), MAX_BACKOFF_S)
        _state.pop("hold_until", None)
        if retry_after is not None and retry_after > 0:
            asked = min(retry_after, MAX_RETRY_AFTER_S)
            wait = max(wait, asked)
            _state["hold_until"] = now + asked
        _state.update(attempted_at=now, failures=failures, retry_at=now + wait, status=status, error=error)
        _save(_state)
        log.warning("Claude usage check failed, next timed try in %.0f s: %s", wait, error)
        return _answer(_state, plan)
