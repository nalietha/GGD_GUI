"""Device-code flow and the stored user token.

EventSub over WebSocket refuses an app access token, so a user token is
mandatory for push. These cover getting one on a headless device and keeping it
alive without the user ever being asked again.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from livepulse.core.clock import ManualClock
from livepulse.core.errors import ProviderAuthError, ProviderError
from livepulse.providers.twitch.userauth import (
    REFRESH_MARGIN,
    DeviceCodeFlow,
    DeviceGrant,
    UserTokenSource,
)
from livepulse.storage.repositories import Repositories, StoredToken


class FakeIdApi:
    """Stand-in for id.twitch.tv."""

    def __init__(self) -> None:
        self.device_status = 200
        self.approved = False
        self.pending_polls = 0
        self.polls = 0
        self.refresh_status = 200
        self.access_token = "user-token-1"
        self.refresh_token: str | None = "refresh-1"
        self.expires_in: object = 14400
        self.slow_down_once = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        body = dict(httpx.QueryParams(request.content.decode()))

        if url.startswith("https://id.twitch.tv/oauth2/device"):
            if self.device_status != 200:
                return httpx.Response(self.device_status, json={"message": "bad client"})
            return httpx.Response(
                200,
                json={
                    "device_code": "dev-code",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://twitch.tv/activate",
                    "interval": 0,
                    "expires_in": 1800,
                },
            )

        # token endpoint
        if body.get("grant_type") == "refresh_token":
            if self.refresh_status != 200:
                return httpx.Response(self.refresh_status, json={"message": "invalid"})
            return httpx.Response(
                200,
                json={
                    "access_token": self.access_token,
                    "refresh_token": self.refresh_token,
                    "expires_in": self.expires_in,
                },
            )

        self.polls += 1
        if self.slow_down_once:
            self.slow_down_once = False
            return httpx.Response(400, json={"message": "slow_down"})
        if self.polls <= self.pending_polls:
            return httpx.Response(400, json={"message": "authorization_pending"})
        if not self.approved:
            return httpx.Response(400, json={"message": "access_denied"})

        return httpx.Response(
            200,
            json={
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "expires_in": self.expires_in,
            },
        )


@pytest.fixture
def id_api() -> FakeIdApi:
    return FakeIdApi()


@pytest.fixture
def id_http(id_api: FakeIdApi) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(id_api.handler))


@pytest.fixture
def flow(id_http: httpx.AsyncClient) -> DeviceCodeFlow:
    # min_interval=0 so the suite does not sit through the real 5s floor.
    return DeviceCodeFlow("client-id", id_http, min_interval=0.0)


class TestDeviceCodeFlow:
    async def test_start_returns_what_the_user_needs(self, flow: DeviceCodeFlow) -> None:
        """A code and a URL — no browser on the device, no redirect to host."""
        grant = await flow.start()

        assert grant.user_code == "ABCD-EFGH"
        assert grant.verification_uri == "https://twitch.tv/activate"
        assert grant.device_code == "dev-code"

    async def test_a_bad_client_id_is_named(self, flow: DeviceCodeFlow, id_api: FakeIdApi) -> None:
        id_api.device_status = 400

        with pytest.raises(ProviderAuthError) as caught:
            await flow.start()
        assert "CLIENT_ID" in str(caught.value)

    async def test_poll_returns_the_token_once_approved(
        self, flow: DeviceCodeFlow, id_api: FakeIdApi
    ) -> None:
        id_api.approved = True
        grant = await flow.start()

        token = await flow.poll(grant)

        assert token.access_token == "user-token-1"
        assert token.refresh_token == "refresh-1"
        assert token.expires_at is not None

    async def test_poll_waits_through_pending(
        self, flow: DeviceCodeFlow, id_api: FakeIdApi
    ) -> None:
        """The user takes a while to walk to another machine."""
        id_api.pending_polls = 3
        id_api.approved = True
        grant = await flow.start()

        token = await flow.poll(grant)

        assert token.access_token == "user-token-1"
        assert id_api.polls == 4

    async def test_slow_down_is_obeyed(self, flow: DeviceCodeFlow, id_api: FakeIdApi) -> None:
        """Twitch says we are asking too often; persisting would earn worse."""
        id_api.slow_down_once = True
        id_api.approved = True
        grant = await flow.start()

        token = await flow.poll(grant)
        assert token.access_token == "user-token-1"

    async def test_a_refusal_is_reported(self, flow: DeviceCodeFlow, id_api: FakeIdApi) -> None:
        id_api.approved = False
        grant = await flow.start()

        with pytest.raises(ProviderAuthError, match="authorisation failed"):
            await flow.poll(grant)

    async def test_an_expired_code_is_reported(
        self, flow: DeviceCodeFlow, id_api: FakeIdApi
    ) -> None:
        id_api.pending_polls = 1000
        grant = DeviceGrant(
            device_code="dev-code",
            user_code="ABCD",
            verification_uri="https://x",
            interval=0.0,
            # Already expired, so the loop never runs.
            expires_in=-1.0,
        )
        with pytest.raises(ProviderAuthError, match="expired"):
            await flow.poll(grant)


class TestUserTokenSource:
    async def test_reports_when_nobody_has_logged_in(
        self, repos: Repositories, id_http: httpx.AsyncClient, clock: ManualClock
    ) -> None:
        """On a headless device this message is the only prompt there is."""
        source = UserTokenSource("id", "secret", id_http, repos.tokens, clock)

        with pytest.raises(ProviderAuthError) as caught:
            await source.token()

        message = str(caught.value)
        assert "provider login twitch" in message
        assert "app token" in message, "should say why an app token will not do"

    async def test_returns_a_stored_token(
        self, repos: Repositories, id_http: httpx.AsyncClient, clock: ManualClock
    ) -> None:
        await repos.tokens.save(
            "twitch",
            StoredToken(access_token="stored", expires_at=clock.now() + timedelta(hours=4)),
        )
        source = UserTokenSource("id", "secret", id_http, repos.tokens, clock)

        assert await source.token() == "stored"

    async def test_refreshes_a_stale_token(
        self,
        repos: Repositories,
        id_http: httpx.AsyncClient,
        id_api: FakeIdApi,
        clock: ManualClock,
    ) -> None:
        """The user is never asked again; the refresh token does the work."""
        await repos.tokens.save(
            "twitch",
            StoredToken(
                access_token="old",
                refresh_token="refresh-1",
                expires_at=clock.now() + timedelta(seconds=1),
            ),
        )
        id_api.access_token = "user-token-2"
        source = UserTokenSource("id", "secret", id_http, repos.tokens, clock)

        assert await source.token() == "user-token-2"

        persisted = await repos.tokens.get("twitch")
        assert persisted is not None
        assert persisted.access_token == "user-token-2", "the refresh must be saved"

    async def test_refreshes_ahead_of_expiry(
        self,
        repos: Repositories,
        id_http: httpx.AsyncClient,
        id_api: FakeIdApi,
        clock: ManualClock,
    ) -> None:
        """A request must never race the expiry."""
        await repos.tokens.save(
            "twitch",
            StoredToken(
                access_token="old",
                refresh_token="refresh-1",
                expires_at=clock.now() + REFRESH_MARGIN - timedelta(seconds=1),
            ),
        )
        id_api.access_token = "user-token-2"
        source = UserTokenSource("id", "secret", id_http, repos.tokens, clock)

        assert await source.token() == "user-token-2"

    async def test_a_rejected_refresh_token_asks_for_a_new_login(
        self,
        repos: Repositories,
        id_http: httpx.AsyncClient,
        id_api: FakeIdApi,
        clock: ManualClock,
    ) -> None:
        """Terminal — only a human can fix a revoked grant."""
        await repos.tokens.save(
            "twitch",
            StoredToken(
                access_token="old",
                refresh_token="revoked",
                expires_at=clock.now() - timedelta(hours=1),
            ),
        )
        id_api.refresh_status = 400
        source = UserTokenSource("id", "secret", id_http, repos.tokens, clock)

        with pytest.raises(ProviderAuthError, match="provider login twitch"):
            await source.token()

    async def test_an_expired_token_without_a_refresh_token_asks_for_login(
        self, repos: Repositories, id_http: httpx.AsyncClient, clock: ManualClock
    ) -> None:
        await repos.tokens.save(
            "twitch",
            StoredToken(access_token="old", expires_at=clock.now() - timedelta(hours=1)),
        )
        source = UserTokenSource("id", "secret", id_http, repos.tokens, clock)

        with pytest.raises(ProviderAuthError, match="no refresh token"):
            await source.token()

    async def test_a_token_with_unknown_expiry_is_used(
        self, repos: Repositories, id_http: httpx.AsyncClient, clock: ManualClock
    ) -> None:
        await repos.tokens.save("twitch", StoredToken(access_token="stored"))
        source = UserTokenSource("id", "secret", id_http, repos.tokens, clock)

        assert await source.token() == "stored"

    async def test_a_network_failure_is_transient(
        self, repos: Repositories, clock: ManualClock
    ) -> None:
        def explode(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route")

        await repos.tokens.save(
            "twitch",
            StoredToken(
                access_token="old",
                refresh_token="r",
                expires_at=clock.now() - timedelta(hours=1),
            ),
        )
        client = httpx.AsyncClient(transport=httpx.MockTransport(explode))
        source = UserTokenSource("id", "secret", client, repos.tokens, clock)

        with pytest.raises(ProviderError) as caught:
            await source.token()
        assert not isinstance(caught.value, ProviderAuthError), "a blip is not a bad credential"
        await client.aclose()


class TestTokenParsing:
    async def test_expires_in_becomes_an_absolute_time(
        self, flow: DeviceCodeFlow, id_api: FakeIdApi
    ) -> None:
        id_api.approved = True
        id_api.expires_in = 3600
        grant = await flow.start()

        token = await flow.poll(grant)
        assert token.expires_at is not None
        assert token.expires_at > datetime.now(UTC)

    async def test_a_missing_expires_in_is_tolerated(
        self, flow: DeviceCodeFlow, id_api: FakeIdApi
    ) -> None:
        id_api.approved = True
        id_api.expires_in = None
        grant = await flow.start()

        assert (await flow.poll(grant)).expires_at is None

    async def test_a_missing_refresh_token_is_tolerated(
        self, flow: DeviceCodeFlow, id_api: FakeIdApi
    ) -> None:
        id_api.approved = True
        id_api.refresh_token = None
        grant = await flow.start()

        assert (await flow.poll(grant)).refresh_token is None
