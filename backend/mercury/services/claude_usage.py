"""Claude Code plan usage: the 5-hour session limit and the weekly limits.

Reads the sign-in Claude Code keeps (`<config dir>/.credentials.json`, or the macOS Keychain)
and asks Anthropic's usage endpoint, the same one Claude Code's `/usage` and the VS Code
extension read. Mercury only reads the sign-in. It never refreshes the token, because a
refresh rotates it and would sign the user's own Claude Code out.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..models import ClaudeUsage, UsageWindow, utcnow

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
OAUTH_BETA = "oauth-2025-04-20"
TIMEOUT_S = 10

# How long a good reading is reused. The UI polls once a minute; a manual refresh may skip
# the cache but never more often than MIN_REFRESH_S.
CACHE_TTL_S = 60
MIN_REFRESH_S = 10

# Response keys shown in the UI, in order, with their window length (for the time marker).
WINDOWS: list[tuple[str, str, int]] = [
    ("five_hour", "Session (5hr)", 5 * 3600),
    ("seven_day", "Weekly (7 day)", 7 * 86400),
    ("seven_day_opus", "Weekly · Opus", 7 * 86400),
    ("seven_day_sonnet", "Weekly · Sonnet", 7 * 86400),
]


class UsageHTTPError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


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
        raise UsageHTTPError(exc.code, f"Anthropic returned HTTP {exc.code}") from exc


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


_lock = threading.Lock()
_cache: dict[str, tuple[float, ClaudeUsage]] = {}  # access token -> (monotonic time, reading)


def reset_cache() -> None:
    with _lock:
        _cache.clear()


def get_usage(refresh: bool = False) -> ClaudeUsage:
    creds = _read_credentials()
    if creds is None:
        return ClaudeUsage(
            status="signed_out",
            message="Claude Code isn't signed in with a Claude subscription on this machine.",
        )
    plan = creds.get("subscriptionType") if isinstance(creds.get("subscriptionType"), str) else None
    expires_ms = creds.get("expiresAt")
    if isinstance(expires_ms, (int, float)) and expires_ms / 1000 < time.time():
        return ClaudeUsage(
            status="expired", plan=plan,
            message="Claude Code's sign-in has expired. Run `claude` once to renew it, then refresh.",
        )

    token = creds["accessToken"]
    with _lock:
        cached = _cache.get(token)
        if cached:
            age = time.monotonic() - cached[0]
            if age < (MIN_REFRESH_S if refresh else CACHE_TTL_S):
                return cached[1]
        try:
            data = _fetch(token)
        except UsageHTTPError as exc:
            if exc.status in (401, 403):
                return ClaudeUsage(
                    status="expired", plan=plan,
                    message="Anthropic didn't accept Claude Code's sign-in. Run `claude` once to renew it, then refresh.",
                )
            return ClaudeUsage(status="error", plan=plan, message=str(exc))
        except (OSError, ValueError) as exc:
            return ClaudeUsage(status="error", plan=plan, message=f"Couldn't reach Anthropic: {exc}")
        usage = ClaudeUsage(status="ok", plan=plan, windows=parse(data), fetched_at=utcnow())
        _cache.clear()  # only the current sign-in's reading is worth keeping
        _cache[token] = (time.monotonic(), usage)
        return usage
