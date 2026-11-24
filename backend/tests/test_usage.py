from __future__ import annotations

import json
import os
import time

import pytest

from mercury.services import claude_usage

SAMPLE = {
    "five_hour": {"utilization": 97.0, "resets_at": "2026-11-25T00:30:00.898449+00:00"},
    "seven_day": {"utilization": 63.0, "resets_at": "2026-11-26T06:00:00+00:00"},
    "seven_day_opus": None,
    "seven_day_sonnet": {"utilization": None, "resets_at": None},
    "extra_usage": {"is_enabled": False},
}


@pytest.fixture(autouse=True)
def _fresh_cache():
    claude_usage.reset_cache()
    yield
    claude_usage.reset_cache()


@pytest.fixture
def fetches(monkeypatch):
    """Replace the HTTP call; tests set `.result` to a dict or an exception."""

    class Fake:
        result: object = SAMPLE
        tokens: list[str] = []

        def __call__(self, token):
            self.tokens.append(token)
            if isinstance(self.result, Exception):
                raise self.result
            return self.result

    fake = Fake()
    fake.tokens = []
    monkeypatch.setattr(claude_usage, "_fetch", fake)
    return fake


def _sign_in(expires_in_s: float = 3600, token: str = "tok-1"):
    path = claude_usage.config_dir()
    path.mkdir(parents=True, exist_ok=True)
    (path / ".credentials.json").write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": token,
                    "refreshToken": "r",
                    "expiresAt": int((time.time() + expires_in_s) * 1000),
                    "subscriptionType": "pro",
                }
            }
        )
    )


def test_config_dir_is_isolated_in_tests(client):
    assert os.environ["CLAUDE_CONFIG_DIR"] in str(claude_usage.config_dir())


def test_signed_out_without_credentials(client, fetches):
    body = client.get("/api/usage/claude-code").json()
    assert body["status"] == "signed_out"
    assert body["windows"] == []
    assert fetches.tokens == []


def test_reports_windows(client, fetches):
    _sign_in()
    body = client.get("/api/usage/claude-code").json()
    assert body["status"] == "ok"
    assert body["plan"] == "pro"
    assert [w["key"] for w in body["windows"]] == ["five_hour", "seven_day"]
    five = body["windows"][0]
    assert five["label"] == "Session (5hr)"
    assert five["utilization"] == 97.0
    assert five["window_seconds"] == 5 * 3600
    assert five["resets_at"].startswith("2026-11-25T00:30:00")
    assert fetches.tokens == ["tok-1"]


def test_caches_and_limits_refreshes(client, fetches, monkeypatch):
    _sign_in()
    client.get("/api/usage/claude-code")
    client.get("/api/usage/claude-code")
    client.get("/api/usage/claude-code?refresh=true")  # too soon after the last read
    assert len(fetches.tokens) == 1

    monkeypatch.setattr(claude_usage, "MIN_REFRESH_S", 0)
    client.get("/api/usage/claude-code?refresh=true")
    assert len(fetches.tokens) == 2

    _sign_in(token="tok-2")  # a new sign-in never reuses the old reading
    client.get("/api/usage/claude-code")
    assert fetches.tokens[-1] == "tok-2"


def test_expired_sign_in_is_not_sent(client, fetches):
    _sign_in(expires_in_s=-60)
    body = client.get("/api/usage/claude-code").json()
    assert body["status"] == "expired"
    assert fetches.tokens == []


def test_rejected_token_reads_as_expired(client, fetches):
    _sign_in()
    fetches.result = claude_usage.UsageHTTPError(401, "Anthropic returned HTTP 401")
    assert client.get("/api/usage/claude-code").json()["status"] == "expired"


def test_errors_are_reported_and_not_cached(client, fetches):
    _sign_in()
    fetches.result = claude_usage.UsageHTTPError(500, "Anthropic returned HTTP 500")
    body = client.get("/api/usage/claude-code").json()
    assert body["status"] == "error"
    assert "500" in body["message"]

    fetches.result = OSError("offline")
    assert "offline" in client.get("/api/usage/claude-code").json()["message"]

    fetches.result = SAMPLE
    assert client.get("/api/usage/claude-code").json()["status"] == "ok"
