from __future__ import annotations

import email.message
import json
import os
import time
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest
from claude_agent_sdk import ResultMessage
from test_agent_runs import FakeClient, _setup, _wait_for

from mercury import config
from mercury.services import claude_usage


def _in(hours: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


SESSION_RESET = _in(3)
SAMPLE = {
    "five_hour": {"utilization": 97.0, "resets_at": SESSION_RESET},
    "seven_day": {"utilization": 63.0, "resets_at": _in(72)},
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
def clock(monkeypatch):
    """Stands in for claude_usage's clock, so tests can skip ahead."""

    class Clock:
        now = time.time()

        def time(self) -> float:
            return self.now

        def advance(self, seconds: float) -> None:
            self.now += seconds

    fake = Clock()
    monkeypatch.setattr(claude_usage, "time", fake)
    return fake


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


def _sign_in(expires_in_s: float = 100_000, token: str = "tok-1"):
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


def _usage(client, refresh: bool = False) -> dict:
    return client.get("/api/usage/claude-code", params={"refresh": refresh}).json()


def _rate_limited(retry_after=None):
    return claude_usage.UsageHTTPError(429, "rate limited (HTTP 429)", retry_after)


def _wait_s(body: dict, clock) -> int:
    return round(datetime.fromisoformat(body["retry_at"].replace("Z", "+00:00")).timestamp() - clock.now)


def test_config_dir_is_isolated_in_tests(client):
    assert os.environ["CLAUDE_CONFIG_DIR"] in str(claude_usage.config_dir())


def test_signed_out_without_credentials(client, fetches):
    body = _usage(client)
    assert body["status"] == "signed_out"
    assert body["windows"] == []
    assert fetches.tokens == []


def test_reports_windows(client, fetches):
    _sign_in()
    body = _usage(client)
    assert body["status"] == "ok"
    assert body["plan"] == "pro"
    assert [w["key"] for w in body["windows"]] == ["five_hour", "seven_day"]
    five = body["windows"][0]
    assert five["label"] == "Session (5hr)"
    assert five["utilization"] == 97.0
    assert five["window_seconds"] == 5 * 3600
    assert five["resets_at"].startswith(SESSION_RESET[:19])
    assert fetches.tokens == ["tok-1"]


def test_caches_and_limits_refreshes(client, fetches, clock):
    _sign_in()
    _usage(client)
    clock.advance(claude_usage.CACHE_TTL_S - 1)
    _usage(client)
    assert len(fetches.tokens) == 1
    clock.advance(1)
    _usage(client)
    assert len(fetches.tokens) == 2

    _usage(client, refresh=True)  # too soon after the last read
    assert len(fetches.tokens) == 2
    clock.advance(claude_usage.MIN_REFRESH_S)
    _usage(client, refresh=True)
    assert len(fetches.tokens) == 3

    _sign_in(token="tok-2")  # a new sign-in is asked right away
    _usage(client)
    assert fetches.tokens[-1] == "tok-2"


def test_expired_sign_in_is_not_sent(client, fetches):
    _sign_in(expires_in_s=-60)
    assert _usage(client)["status"] == "expired"
    assert fetches.tokens == []


def test_rejected_token_reads_as_expired(client, fetches):
    _sign_in()
    fetches.result = claude_usage.UsageHTTPError(401, "Anthropic returned HTTP 401")
    for _ in range(2):
        body = _usage(client)
        assert body["status"] == "expired" and body["retry_at"] is None
    assert len(fetches.tokens) == 1  # a rejected token isn't retried straight away

    _sign_in(token="tok-2")  # renewing the sign-in is tried at once
    fetches.result = SAMPLE
    assert _usage(client)["status"] == "ok"


@pytest.mark.parametrize("trouble", ["expired", "signed out", "rejected", "renewed and failing"])
def test_last_reading_survives_sign_in_trouble(client, fetches, clock, trouble):
    _sign_in()
    first = _usage(client)
    clock.advance(claude_usage.CACHE_TTL_S)
    if trouble == "expired":
        _sign_in(expires_in_s=-60)
    elif trouble == "signed out":
        (claude_usage.config_dir() / ".credentials.json").unlink()
    elif trouble == "rejected":
        fetches.result = claude_usage.UsageHTTPError(401, "Anthropic returned HTTP 401")
    else:
        _sign_in(token="tok-2")
        fetches.result = _rate_limited()
    claude_usage.reset_cache()  # even after a restart
    for _ in range(2):
        body = _usage(client)
        assert body["status"] == "ok"
        assert body["windows"] == first["windows"]
        assert body["message"]


def test_errors_are_waited_out(client, fetches, clock):
    _sign_in()
    fetches.result = claude_usage.UsageHTTPError(500, "Anthropic returned HTTP 500")
    body = _usage(client)
    assert body["status"] == "error"
    assert "500" in body["message"]
    assert _wait_s(body, clock) == claude_usage.BACKOFF_S

    fetches.result = OSError("offline")
    clock.advance(claude_usage.BACKOFF_S - 1)
    assert "500" in _usage(client)["message"]
    assert len(fetches.tokens) == 1

    clock.advance(1)
    assert "offline" in _usage(client)["message"]

    clock.advance(claude_usage.MAX_BACKOFF_S)
    fetches.result = SAMPLE
    body = _usage(client)
    assert body["status"] == "ok"
    assert body["message"] is None and body["retry_at"] is None


def test_backoff_grows_then_starts_over(client, fetches, clock):
    _sign_in()
    fetches.result = _rate_limited(retry_after=0)  # seen from this endpoint; not a reason to hurry
    waits = []
    for _ in range(5):
        waits.append(_wait_s(_usage(client), clock))
        clock.advance(waits[-1])
    assert waits == [300, 600, 1200, 1800, 1800]
    assert len(fetches.tokens) == 5

    fetches.result = SAMPLE
    _usage(client)
    clock.advance(claude_usage.CACHE_TTL_S)
    fetches.result = _rate_limited()
    assert _wait_s(_usage(client), clock) == claude_usage.BACKOFF_S


def test_refresh_tries_during_the_wait_but_not_back_to_back(client, fetches, clock):
    _sign_in()
    fetches.result = _rate_limited(retry_after=0)
    _usage(client)
    clock.advance(claude_usage.MIN_REFRESH_S - 1)
    _usage(client, refresh=True)
    assert len(fetches.tokens) == 1
    clock.advance(1)
    _usage(client, refresh=True)
    assert len(fetches.tokens) == 2

    clock.advance(claude_usage.MIN_REFRESH_S)
    fetches.result = SAMPLE
    assert _usage(client, refresh=True)["message"] is None


@pytest.mark.parametrize("asked, waited", [(900, 900), (86400, claude_usage.MAX_RETRY_AFTER_S)])
def test_longer_retry_after_is_honored(client, fetches, clock, asked, waited):
    _sign_in()
    fetches.result = _rate_limited(retry_after=asked)
    assert _wait_s(_usage(client), clock) == waited
    clock.advance(waited - 1)
    _usage(client, refresh=True)
    assert len(fetches.tokens) == 1
    clock.advance(1)
    _usage(client, refresh=True)
    assert len(fetches.tokens) == 2


def test_last_reading_is_kept_while_rate_limited(client, fetches, clock):
    _sign_in()
    first = _usage(client)
    clock.advance(claude_usage.CACHE_TTL_S)
    fetches.result = _rate_limited()
    for _ in range(2):  # both the failing call and the waiting after it
        body = _usage(client)
        assert body["status"] == "ok"
        assert body["windows"] == first["windows"]
        assert body["fetched_at"] == first["fetched_at"]
        assert "429" in body["message"]
    assert len(fetches.tokens) == 2


def test_last_reading_is_kept_after_its_window_resets(client, fetches, clock):
    _sign_in()
    fetches.result = {**SAMPLE, "five_hour": {"utilization": 97.0, "resets_at": _in(-1)}}
    _usage(client)
    clock.advance(claude_usage.CACHE_TTL_S)
    fetches.result = _rate_limited()
    assert _usage(client)["windows"][0]["utilization"] == 97.0


def test_restart_remembers_reading_and_backoff(client, fetches, clock):
    _sign_in()
    _usage(client)
    claude_usage.reset_cache()
    _usage(client)
    assert len(fetches.tokens) == 1

    clock.advance(claude_usage.CACHE_TTL_S)
    fetches.result = _rate_limited()
    _usage(client)
    claude_usage.reset_cache()
    body = _usage(client)
    assert body["status"] == "ok" and "429" in body["message"]
    assert len(fetches.tokens) == 2
    assert "tok-1" not in (config.data_dir() / "claude_usage.json").read_text()


@pytest.mark.parametrize("saved", ["[not json", "[1]", '{"reading": 1}'])
def test_unreadable_saved_state_is_ignored(client, fetches, saved):
    _sign_in()
    (config.data_dir() / "claude_usage.json").write_text(saved)
    assert _usage(client)["status"] == "ok"


def test_429_response_is_read(monkeypatch):
    headers = email.message.Message()
    headers["Retry-After"] = "120"

    def urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", headers, None)

    monkeypatch.setattr(claude_usage.urllib.request, "urlopen", urlopen)
    with pytest.raises(claude_usage.UsageHTTPError) as caught:
        claude_usage._fetch("tok")
    assert caught.value.status == 429
    assert caught.value.retry_after == 120
    assert "429" in str(caught.value)


@pytest.mark.parametrize("fails", [False, True])
def test_only_a_succeeded_session_pulls_usage(make_client, tmp_path, monkeypatch, fails):
    pulls: list[bool] = []
    monkeypatch.setattr(claude_usage, "get_usage", lambda refresh=False: pulls.append(refresh))

    class Client(FakeClient):
        async def receive_response(self):
            async for message in super().receive_response():
                if isinstance(message, ResultMessage):
                    message.is_error = fails
                yield message

    client = make_client(Client)
    project, _ = _setup(client, tmp_path)
    run = client.post(f"/api/projects/{project['id']}/runs", json={"prompt": "list files"}).json()
    _wait_for(client, run["id"], {"failed" if fails else "succeeded"})

    deadline = time.time() + (0.3 if fails else 5)  # the pull runs on a thread, just after the run ends
    while not pulls and time.time() < deadline:
        time.sleep(0.02)
    assert pulls == ([] if fails else [True])
