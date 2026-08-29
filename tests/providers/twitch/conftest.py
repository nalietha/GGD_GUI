"""Twitch test doubles.

Everything runs through ``httpx.MockTransport``, so the real provider — auth,
retry, batching, error mapping — is exercised with no network and no
credentials. A test that hit the real API would be slow, flaky, and would need
secrets in CI.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import httpx
import pytest

from livepulse.core.clock import ManualClock
from livepulse.providers.base import ProviderConfig
from livepulse.providers.twitch.auth import TOKEN_URL, AppTokenSource
from livepulse.providers.twitch.poll import TwitchProvider


class FakeTwitch:
    """A scriptable stand-in for the Twitch API."""

    def __init__(self) -> None:
        self.users: dict[str, str] = {}
        """login → numeric id"""

        self.live: dict[str, dict[str, object]] = {}
        """user_id → the stream object Helix would return"""

        self.requests: list[httpx.Request] = []
        self.token_fetches = 0
        self.token_value = "token-1"
        self.token_expires_in: object = 3600

        # Scripted failures, popped one per matching request.
        self.next_responses: list[httpx.Response] = []
        self.token_status = 200

    def set_live(self, user_id: str, **fields: object) -> None:
        self.live[user_id] = {
            "user_id": user_id,
            "title": "a stream",
            "game_name": "Just Chatting",
            "viewer_count": 1234,
            "started_at": "2026-03-01T18:30:00Z",
            "thumbnail_url": "https://example.invalid/thumb.jpg",
            **fields,
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)

        if str(request.url).startswith(TOKEN_URL):
            self.token_fetches += 1
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"message": "bad"})
            body: dict[str, object] = {"access_token": self.token_value}
            if self.token_expires_in is not None:
                body["expires_in"] = self.token_expires_in
            return httpx.Response(200, json=body)

        if self.next_responses:
            return self.next_responses.pop(0)

        path = request.url.path
        if path.endswith("/users"):
            logins = request.url.params.get_list("login")
            data = [
                {"id": self.users[login], "login": login, "display_name": login}
                for login in logins
                if login in self.users
            ]
            return httpx.Response(200, json={"data": data})

        if path.endswith("/streams"):
            wanted = set(request.url.params.get_list("user_id"))
            data = [stream for uid, stream in self.live.items() if uid in wanted]
            return httpx.Response(200, json={"data": data})

        return httpx.Response(404, json={"error": "unmapped"})

    # --- request introspection ----------------------------------------

    def helix_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if "helix" in str(r.url)]

    def user_ids_requested(self) -> list[list[str]]:
        return [
            list(r.url.params.get_list("user_id"))
            for r in self.helix_requests()
            if r.url.path.endswith("/streams")
        ]


@pytest.fixture
def twitch() -> FakeTwitch:
    return FakeTwitch()


@pytest.fixture
def http(twitch: FakeTwitch) -> Iterator[httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(twitch.handler))
    yield client


@pytest.fixture
def token_source(http: httpx.AsyncClient, clock: ManualClock) -> AppTokenSource:
    return AppTokenSource("client-id", "client-secret", http, clock)


@pytest.fixture
def provider(http: httpx.AsyncClient, token_source: AppTokenSource) -> TwitchProvider:
    return TwitchProvider(ProviderConfig(provider_id="twitch"), http=http, auth=token_source)


ResponseFactory = Callable[[], httpx.Response]


@pytest.fixture
async def repos(tmp_path):
    """A migrated database for token-storage tests."""
    from livepulse.storage.db import IN_MEMORY, Database
    from livepulse.storage.repositories import Repositories

    async with Database(IN_MEMORY) as db:
        yield Repositories(db)
