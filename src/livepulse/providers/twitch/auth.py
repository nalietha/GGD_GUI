"""Twitch credentials.

Only the app access token (client credentials) for now — that is all the Helix
endpoints we poll require. EventSub over WebSocket needs a *user* token, which
arrives with the push provider; this module is shaped to hold both.

V1 fetched a token once at startup and refreshed only on a 401. That works
until the process runs longer than the token's ~60-day life and then fails one
request before recovering. Here it refreshes proactively, and a 401 is still
handled as a belt-and-braces retry.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import httpx
import structlog

from livepulse.core.clock import Clock, SystemClock
from livepulse.core.errors import ProviderAuthError, ProviderError

__all__ = ["TOKEN_URL", "AppTokenSource"]

log = structlog.get_logger(__name__)

TOKEN_URL = "https://id.twitch.tv/oauth2/token"

PROVIDER = "twitch"

#: Refresh once this much of the token's life remains, so a request never races
#: an expiry. Twitch app tokens last around 60 days, so this is generous.
REFRESH_MARGIN = timedelta(hours=1)

#: The margin is also capped at this fraction of the token's actual lifetime.
#: Without the cap, a token shorter than REFRESH_MARGIN would be considered
#: stale the instant it was issued, and every single call would fetch a new one
#: — an unbounded loop against the token endpoint, which is exactly the kind of
#: thing that gets a client id suspended.
MAX_MARGIN_FRACTION = 0.1

#: Used when Twitch omits expires_in. Short enough to be safe, long enough not
#: to hammer the token endpoint.
FALLBACK_LIFETIME = timedelta(hours=1)


class AppTokenSource:
    """Fetches and caches a Twitch app access token.

    Safe to share across concurrent requests: a lock ensures a token storm
    (sixteen channels polling at once on a cold start) produces one fetch, not
    sixteen.
    """

    __slots__ = (
        "_client",
        "_client_id",
        "_client_secret",
        "_clock",
        "_lock",
        "_refresh_at",
        "_token",
    )

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        http: httpx.AsyncClient,
        clock: Clock | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._client = http
        self._clock = clock or SystemClock()
        self._token: str | None = None
        self._refresh_at: datetime | None = None
        self._lock = asyncio.Lock()

    @property
    def client_id(self) -> str:
        return self._client_id

    @property
    def has_token(self) -> bool:
        return self._token is not None

    def _is_fresh(self) -> bool:
        if self._token is None or self._refresh_at is None:
            return False
        return self._clock.now() < self._refresh_at

    async def token(self) -> str:
        """Return a usable token, fetching one if needed."""
        if self._is_fresh():
            assert self._token is not None
            return self._token

        async with self._lock:
            # Another coroutine may have fetched while we waited for the lock.
            if self._is_fresh():
                assert self._token is not None
                return self._token
            return await self._fetch()

    async def invalidate(self) -> None:
        """Discard the cached token after the platform rejected it."""
        async with self._lock:
            self._token = None
            self._refresh_at = None

    async def _fetch(self) -> str:
        try:
            response = await self._client.post(
                TOKEN_URL,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "grant_type": "client_credentials",
                },
            )
        except httpx.HTTPError as exc:
            raise ProviderError(PROVIDER, f"could not reach the token endpoint: {exc}") from exc

        if response.status_code in (400, 401, 403):
            # Wrong client id or secret. Not transient, and the scheduler trips
            # its circuit immediately on this rather than retrying four times.
            raise ProviderAuthError(
                PROVIDER,
                "Twitch rejected the client credentials — check "
                "LIVEPULSE_SECRET_TWITCH_CLIENT_ID and _CLIENT_SECRET",
            )
        if response.status_code >= 400:
            raise ProviderError(PROVIDER, f"token endpoint returned HTTP {response.status_code}")

        try:
            payload = response.json()
            token = str(payload["access_token"])
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderError(PROVIDER, "token response was not in the expected shape") from exc

        lifetime = _lifetime(payload.get("expires_in"))
        # Capped at a fraction of the lifetime so a short token cannot be born
        # already stale — see MAX_MARGIN_FRACTION.
        margin = min(REFRESH_MARGIN, lifetime * MAX_MARGIN_FRACTION)

        self._token = token
        self._refresh_at = self._clock.now() + lifetime - margin

        log.info(
            "twitch app token acquired",
            lifetime_hours=round(lifetime.total_seconds() / 3600, 1),
            refresh_in_hours=round((lifetime - margin).total_seconds() / 3600, 1),
        )
        return token


def _lifetime(expires_in: object) -> timedelta:
    """Interpret ``expires_in``, tolerating absence or nonsense."""
    if not isinstance(expires_in, int | float | str):
        return FALLBACK_LIFETIME
    try:
        seconds = int(expires_in)
    except (TypeError, ValueError):
        return FALLBACK_LIFETIME
    return timedelta(seconds=seconds) if seconds > 0 else FALLBACK_LIFETIME
