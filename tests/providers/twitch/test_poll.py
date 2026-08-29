"""The Twitch Helix provider.

Exercised end to end through httpx.MockTransport: real auth, real retry, real
batching, real error mapping — no network and no credentials.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from livepulse.core.errors import (
    ChannelNotFoundError,
    ProviderAuthError,
    ProviderError,
    ProviderRateLimited,
)
from livepulse.core.models import AuthKind, ChannelRef, StateSource
from livepulse.providers import create_provider, identify
from livepulse.providers.base import Provider, ProviderConfig
from livepulse.providers.twitch.poll import MAX_BATCH, TwitchProvider

from .conftest import FakeTwitch


def ref(user_id: str, handle: str = "someone") -> ChannelRef:
    return ChannelRef(provider="twitch", platform_ref=user_id, handle=handle)


class TestCapabilities:
    def test_declares_the_helix_batch_limit(self) -> None:
        """Helix caps repeated user_id at 100."""
        assert TwitchProvider.caps.max_batch == 100
        assert TwitchProvider.caps.supports_batch is True

    def test_declares_that_it_needs_an_app_token(self) -> None:
        assert TwitchProvider.caps.needs_auth is AuthKind.OAUTH_APP

    def test_satisfies_the_provider_protocol(self, provider: TwitchProvider) -> None:
        assert isinstance(provider, Provider)


class TestRegistration:
    def test_is_registered(self) -> None:
        assert "twitch" in __import__("livepulse.providers", fromlist=["x"]).available_providers()

    def test_recognises_a_twitch_link(self) -> None:
        """V1 had this and never called it; every channel it added said Twitch
        because it was hardcoded, not because it was detected."""
        assert identify("https://twitch.tv/ironmouse") == ("twitch", "ironmouse")

    def test_construction_without_credentials_is_a_clear_error(self) -> None:
        """A headless appliance gives no other chance to explain."""
        with pytest.raises(ProviderAuthError) as caught:
            create_provider("twitch", ProviderConfig(provider_id="twitch"))
        assert "client_id" in str(caught.value)


class TestResolve:
    async def test_returns_the_numeric_id(
        self, provider: TwitchProvider, twitch: FakeTwitch
    ) -> None:
        """The id is what everything keys on, because it survives a rename."""
        twitch.users["ironmouse"] = "175831187"

        result = await provider.resolve("ironmouse")

        assert result.platform_ref == "175831187"
        assert result.provider == "twitch"
        assert result.handle == "ironmouse"

    async def test_accepts_a_pasted_url(self, provider: TwitchProvider, twitch: FakeTwitch) -> None:
        twitch.users["ironmouse"] = "175831187"
        assert (await provider.resolve("https://twitch.tv/IronMouse")).platform_ref == "175831187"

    async def test_an_unknown_channel_is_reported(self, provider: TwitchProvider) -> None:
        with pytest.raises(ChannelNotFoundError, match="no Twitch channel"):
            await provider.resolve("nobody")

    async def test_an_empty_handle_is_refused(self, provider: TwitchProvider) -> None:
        with pytest.raises(ChannelNotFoundError):
            await provider.resolve("   ")


class TestPoll:
    async def test_reports_a_live_channel(
        self, provider: TwitchProvider, twitch: FakeTwitch
    ) -> None:
        twitch.set_live("1")
        result = await provider.poll([ref("1")])

        assert result["1"].is_live is True
        assert result["1"].source is StateSource.POLL

    async def test_carries_the_stream_details(
        self, provider: TwitchProvider, twitch: FakeTwitch
    ) -> None:
        """Effects can key off viewers or category, not just on/off."""
        twitch.set_live("1")
        state = (await provider.poll([ref("1")]))["1"]

        assert state.title == "a stream"
        assert state.category == "Just Chatting"
        assert state.viewers == 1234
        assert state.started_at == datetime(2026, 3, 1, 18, 30, tzinfo=UTC)

    async def test_absent_from_the_response_means_offline(self, provider: TwitchProvider) -> None:
        """Helix returns only live streams. Absence has to become an explicit
        observation, or 'offline' and 'never checked' become the same thing."""
        result = await provider.poll([ref("1")])

        assert result["1"].is_live is False
        assert result["1"].observed_at is not None

    async def test_returns_an_entry_for_every_ref(
        self, provider: TwitchProvider, twitch: FakeTwitch
    ) -> None:
        twitch.set_live("2")
        refs = [ref("1"), ref("2"), ref("3")]

        result = await provider.poll(refs)

        assert set(result) == {"1", "2", "3"}
        assert [result[k].is_live for k in ("1", "2", "3")] == [False, True, False]

    async def test_an_empty_batch_makes_no_request(
        self, provider: TwitchProvider, twitch: FakeTwitch
    ) -> None:
        assert await provider.poll([]) == {}
        assert twitch.helix_requests() == []

    async def test_one_request_covers_the_whole_batch(
        self, provider: TwitchProvider, twitch: FakeTwitch
    ) -> None:
        refs = [ref(str(n)) for n in range(50)]
        await provider.poll(refs)

        assert len(twitch.user_ids_requested()) == 1
        assert len(twitch.user_ids_requested()[0]) == 50

    async def test_refuses_a_batch_beyond_the_helix_limit(self, provider: TwitchProvider) -> None:
        """The scheduler already chunks; this is the guard against a caller
        that does not. V1's C# client had neither."""
        with pytest.raises(ProviderError, match="exceeds the Helix limit"):
            await provider.poll([ref(str(n)) for n in range(MAX_BATCH + 1)])

    async def test_tolerates_a_stream_missing_optional_fields(
        self, provider: TwitchProvider, twitch: FakeTwitch
    ) -> None:
        """Twitch has changed this payload before. A missing title must not
        cost us the live status."""
        twitch.live["1"] = {"user_id": "1"}

        state = (await provider.poll([ref("1")]))["1"]
        assert state.is_live is True
        assert state.title is None
        assert state.viewers is None

    async def test_ignores_a_nonsense_viewer_count(
        self, provider: TwitchProvider, twitch: FakeTwitch
    ) -> None:
        twitch.set_live("1", viewer_count="lots")
        assert (await provider.poll([ref("1")]))["1"].viewers is None

    async def test_ignores_an_unparseable_start_time(
        self, provider: TwitchProvider, twitch: FakeTwitch
    ) -> None:
        twitch.set_live("1", started_at="not a date")
        assert (await provider.poll([ref("1")]))["1"].started_at is None


class TestAuthHandling:
    async def test_sends_the_token_and_client_id(
        self, provider: TwitchProvider, twitch: FakeTwitch
    ) -> None:
        await provider.poll([ref("1")])

        request = twitch.helix_requests()[0]
        assert request.headers["Authorization"] == "Bearer token-1"
        assert request.headers["Client-Id"] == "client-id"

    async def test_a_401_refreshes_the_token_and_retries_once(
        self, provider: TwitchProvider, twitch: FakeTwitch
    ) -> None:
        """A token can be revoked before it expires. One retry, then give up."""
        twitch.next_responses = [httpx.Response(401, json={"message": "invalid token"})]
        twitch.token_value = "token-2"

        result = await provider.poll([ref("1")])

        assert result["1"].is_live is False
        assert twitch.token_fetches == 2
        assert twitch.helix_requests()[-1].headers["Authorization"] == "Bearer token-2"

    async def test_a_persistent_401_is_an_auth_error(
        self, provider: TwitchProvider, twitch: FakeTwitch
    ) -> None:
        """Two failures is not transient, and the scheduler trips its circuit."""
        twitch.next_responses = [
            httpx.Response(401, json={}),
            httpx.Response(401, json={}),
        ]
        with pytest.raises(ProviderAuthError):
            await provider.poll([ref("1")])

    async def test_a_403_is_an_auth_error(
        self, provider: TwitchProvider, twitch: FakeTwitch
    ) -> None:
        twitch.next_responses = [httpx.Response(403, json={})]
        with pytest.raises(ProviderAuthError):
            await provider.poll([ref("1")])


class TestRateLimiting:
    async def test_a_429_carries_retry_after(
        self, provider: TwitchProvider, twitch: FakeTwitch
    ) -> None:
        """Wait what Twitch asked for, not a guess. V1 had no handling at all."""
        twitch.next_responses = [httpx.Response(429, headers={"Retry-After": "42"}, json={})]

        with pytest.raises(ProviderRateLimited) as caught:
            await provider.poll([ref("1")])
        assert caught.value.retry_after == 42.0

    async def test_falls_back_when_retry_after_is_missing(
        self, provider: TwitchProvider, twitch: FakeTwitch
    ) -> None:
        twitch.next_responses = [httpx.Response(429, json={})]

        with pytest.raises(ProviderRateLimited) as caught:
            await provider.poll([ref("1")])
        assert caught.value.retry_after > 0

    async def test_reads_ratelimit_reset_when_retry_after_is_absent(
        self, provider: TwitchProvider, twitch: FakeTwitch
    ) -> None:
        """Helix sends Ratelimit-Reset as a unix timestamp rather than a delta."""
        reset_at = datetime.now().timestamp() + 30
        twitch.next_responses = [
            httpx.Response(429, headers={"Ratelimit-Reset": str(reset_at)}, json={})
        ]

        with pytest.raises(ProviderRateLimited) as caught:
            await provider.poll([ref("1")])
        assert 0 < caught.value.retry_after <= 31

    async def test_an_unparseable_retry_after_still_yields_a_delay(
        self, provider: TwitchProvider, twitch: FakeTwitch
    ) -> None:
        twitch.next_responses = [httpx.Response(429, headers={"Retry-After": "soon"}, json={})]
        with pytest.raises(ProviderRateLimited) as caught:
            await provider.poll([ref("1")])
        assert caught.value.retry_after > 0


class TestTransportFailures:
    async def test_a_server_error_is_transient(
        self, provider: TwitchProvider, twitch: FakeTwitch
    ) -> None:
        """The breaker counts it and backs off, rather than tripping outright."""
        twitch.next_responses = [httpx.Response(503, json={})]

        with pytest.raises(ProviderError) as caught:
            await provider.poll([ref("1")])
        assert not isinstance(caught.value, ProviderAuthError)

    async def test_a_network_failure_is_reported_as_a_provider_error(
        self, token_source: object
    ) -> None:
        def explode(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host")

        client = httpx.AsyncClient(transport=httpx.MockTransport(explode))
        broken = TwitchProvider(
            ProviderConfig(provider_id="twitch"),
            http=client,
            auth=token_source,  # type: ignore[arg-type]
        )
        with pytest.raises(ProviderError):
            await broken.poll([ref("1")])
        await broken.aclose()

    async def test_a_non_json_response_is_reported(
        self, provider: TwitchProvider, twitch: FakeTwitch
    ) -> None:
        twitch.next_responses = [httpx.Response(200, content=b"<html>nope</html>")]

        with pytest.raises(ProviderError, match="non-JSON"):
            await provider.poll([ref("1")])


class TestLifecycle:
    async def test_aclose_leaves_an_injected_client_alone(
        self, provider: TwitchProvider, http: httpx.AsyncClient
    ) -> None:
        """We only close what we opened; the caller owns an injected client."""
        await provider.aclose()
        assert http.is_closed is False
