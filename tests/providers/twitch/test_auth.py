"""Twitch app access token handling."""

from __future__ import annotations

import asyncio

import pytest

from livepulse.core.clock import ManualClock
from livepulse.core.errors import ProviderAuthError, ProviderError
from livepulse.providers.twitch.auth import FALLBACK_LIFETIME, AppTokenSource

from .conftest import FakeTwitch


class TestFetching:
    async def test_fetches_a_token_on_first_use(
        self, token_source: AppTokenSource, twitch: FakeTwitch
    ) -> None:
        assert await token_source.token() == "token-1"
        assert twitch.token_fetches == 1

    async def test_caches_the_token(self, token_source: AppTokenSource, twitch: FakeTwitch) -> None:
        """One token per lifetime, not one per request."""
        for _ in range(5):
            await token_source.token()
        assert twitch.token_fetches == 1

    async def test_concurrent_callers_share_one_fetch(
        self, token_source: AppTokenSource, twitch: FakeTwitch
    ) -> None:
        """A cold start polls every channel at once; that must not be N fetches."""
        await asyncio.gather(*(token_source.token() for _ in range(10)))
        assert twitch.token_fetches == 1

    async def test_refreshes_before_expiry(
        self, token_source: AppTokenSource, twitch: FakeTwitch, clock: ManualClock
    ) -> None:
        """Proactive, so a request never races the expiry.

        V1 refreshed only after a 401, so it always failed one request first.
        """
        await token_source.token()
        twitch.token_value = "token-2"

        clock.advance(3600)  # past the one-hour lifetime
        assert await token_source.token() == "token-2"
        assert twitch.token_fetches == 2

    async def test_does_not_refresh_while_fresh(
        self, token_source: AppTokenSource, twitch: FakeTwitch, clock: ManualClock
    ) -> None:
        await token_source.token()
        clock.advance(60)
        await token_source.token()
        assert twitch.token_fetches == 1

    async def test_invalidate_forces_a_refetch(
        self, token_source: AppTokenSource, twitch: FakeTwitch
    ) -> None:
        await token_source.token()
        await token_source.invalidate()
        await token_source.token()
        assert twitch.token_fetches == 2


class TestLifetime:
    async def test_a_missing_expires_in_uses_a_safe_fallback(
        self, token_source: AppTokenSource, twitch: FakeTwitch, clock: ManualClock
    ) -> None:
        twitch.token_expires_in = None
        await token_source.token()

        clock.advance(FALLBACK_LIFETIME.total_seconds())
        await token_source.token()
        assert twitch.token_fetches == 2

    @pytest.mark.parametrize("value", ["nonsense", -1, 0])
    async def test_an_unusable_expires_in_uses_the_fallback(
        self, token_source: AppTokenSource, twitch: FakeTwitch, value: object
    ) -> None:
        twitch.token_expires_in = value
        assert await token_source.token() == "token-1"


class TestFailures:
    @pytest.mark.parametrize("status", [400, 401, 403])
    async def test_rejected_credentials_are_an_auth_error(
        self, token_source: AppTokenSource, twitch: FakeTwitch, status: int
    ) -> None:
        """Not transient. The scheduler trips its circuit immediately on this
        rather than retrying four more times with the same bad key."""
        twitch.token_status = status

        with pytest.raises(ProviderAuthError) as caught:
            await token_source.token()
        assert "CLIENT_ID" in str(caught.value), "should name the env var to set"

    async def test_a_server_error_is_a_plain_provider_error(
        self, token_source: AppTokenSource, twitch: FakeTwitch
    ) -> None:
        """Transient — the breaker counts it and backs off instead of tripping."""
        twitch.token_status = 500
        with pytest.raises(ProviderError):
            await token_source.token()

    async def test_a_malformed_response_is_reported(
        self, token_source: AppTokenSource, twitch: FakeTwitch
    ) -> None:
        twitch.token_value = ""  # access_token present but empty is still a string
        assert await token_source.token() == ""

    async def test_has_token_reflects_state(self, token_source: AppTokenSource) -> None:
        assert token_source.has_token is False
        await token_source.token()
        assert token_source.has_token is True
