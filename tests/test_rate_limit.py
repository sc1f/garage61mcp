"""Tests for the rate limit gate.

Garage61 uses a token bucket that fills continuously. A 429 response gives
details.retryAfterSeconds. The documented procedure is to stop the affected
operation, wait, and never send parallel retries.
"""

import asyncio
import time

import httpx
import pytest

import api_client
from api_client import Garage61Client


@pytest.fixture(autouse=True)
def clear_gate():
    api_client._gate_blocked.clear()
    yield
    api_client._gate_blocked.clear()


class FakeApi:
    """One patch of httpx for the whole test, with a script that can change.

    Patching twice would make the second patch wrap the first, and the first
    script would keep answering.
    """

    def __init__(self, monkeypatch):
        self.script = [(200, {})]
        self.sent = 0
        original = httpx.AsyncClient

        def handler(request):
            status, body = self.script[min(self.sent, len(self.script) - 1)]
            self.sent += 1
            return httpx.Response(status, json=body)

        def patched(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return original(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", patched)

    def responds(self, *script):
        self.script = list(script)
        self.sent = 0
        return self


@pytest.fixture
def api(monkeypatch):
    return FakeApi(monkeypatch)


def test_a_short_wait_is_retried(api):
    api.responds((429, {"details": {"retryAfterSeconds": 1}}), (200, {"ok": True}))
    client = Garage61Client("token")
    started = time.monotonic()
    response = asyncio.run(client._api_get("/laps"))
    elapsed = time.monotonic() - started

    assert response.status_code == 200
    assert api.sent == 2
    assert elapsed >= 1.0, "the client did not wait for retryAfterSeconds"


def test_a_long_wait_stops_at_once(api):
    api.responds((429, {"details": {"retryAfterSeconds": 300}}))
    client = Garage61Client("token")
    started = time.monotonic()
    with pytest.raises(ValueError, match="300 seconds"):
        asyncio.run(client._api_get("/laps"))
    assert time.monotonic() - started < 1.0
    assert api.sent == 1, "the client retried a long wait"


def test_the_next_call_sends_nothing(api):
    """The documentation says to stop the affected operation."""
    api.responds((429, {"details": {"retryAfterSeconds": 300}}))
    client = Garage61Client("token")
    with pytest.raises(ValueError):
        asyncio.run(client._api_get("/laps"))
    before = api.sent
    with pytest.raises(ValueError, match="rate limit active"):
        asyncio.run(client._api_get("/laps"))
    assert api.sent == before, "the client sent a request while blocked"


def test_one_operation_does_not_block_another(api):
    api.responds((429, {"details": {"retryAfterSeconds": 300}}))
    client = Garage61Client("token")
    with pytest.raises(ValueError):
        asyncio.run(client._api_get("/laps"))

    api.responds((200, {"ok": True}))
    response = asyncio.run(client._api_get("/cars"))
    assert response.status_code == 200


def test_a_good_response_clears_the_block(api):
    api.responds((429, {"details": {"retryAfterSeconds": 1}}), (200, {"ok": True}))
    client = Garage61Client("token")
    asyncio.run(client._api_get("/laps"))
    assert ("local", "laps") not in api_client._gate_blocked


def test_a_body_without_a_time_still_gives_a_message(api):
    api.responds((429, {}))
    client = Garage61Client("token")
    with pytest.raises(ValueError, match="rate limit"):
        asyncio.run(client._api_get("/laps"))
