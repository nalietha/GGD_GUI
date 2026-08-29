"""Twitch user access token, obtained by the device-code flow.

EventSub over WebSocket will not accept an app access token — creating a
subscription on that transport requires a *user* token. That is the one real
cost of push, and this module pays it.

Device code is the right flow for a Pi: there is no browser on the device and
no redirect URL to host. LivePulse prints a short code, the user enters it on
another machine, and the Pi polls until it is approved. Nothing needs to be
reachable from the internet — which matters, because the whole reason we use
the WebSocket transport rather than webhooks is that a home NAT blocks inbound.

The refresh token then keeps it alive indefinitely, so this is a once-ever step
rather than a recurring chore.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
import structlog

from livepulse.core.clock import Clock, SystemClock
from livepulse.core.errors import ProviderAuthError, ProviderError
from livepulse.storage.repositories import StoredToken, TokenRepository

__all__ = ["DeviceCodeFlow", "DeviceGrant", "UserTokenSource"]

log = structlog.get_logger(__name__)

PROVIDER = "twitch"
DEVICE_URL = "https://id.twitch.tv/oauth2/device"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"

#: stream.online and stream.offline need no scope, but the device flow requires
#: the parameter to be present. Empty is accepted and keeps the grant minimal —
#: LivePulse should not hold permissions it does not use.
DEFAULT_SCOPES = ""

#: Floor on how often we poll for approval. Twitch returns its own interval;
#: going faster than it asks earns slow_down.
MIN_POLL_INTERVAL = 5.0

#: Refresh this far ahead of expiry. User tokens last about four hours, so this
#: is a small fraction rather than the near-whole-life margin an app token wants.
REFRESH_MARGIN = timedelta(minutes=10)


@dataclass(frozen=True, slots=True)
class DeviceGrant:
    """What the user has to do, and what we poll with."""

    device_code: str
    user_code: str
    verification_uri: str
    interval: float
    expires_in: float


class DeviceCodeFlow:
    """Runs the one-time authorisation."""

    __slots__ = ("_client", "_client_id", "_min_interval", "_scopes")

    def __init__(
        self,
        client_id: str,
        http: httpx.AsyncClient,
        scopes: str = DEFAULT_SCOPES,
        min_interval: float = MIN_POLL_INTERVAL,
    ) -> None:
        self._client_id = client_id
        self._client = http
        self._scopes = scopes
        # Injectable so tests do not sit through the real five-second floor.
        # Production always uses it: polling faster than Twitch asks earns
        # slow_down, which makes the login slower, not faster.
        self._min_interval = min_interval

    async def start(self) -> DeviceGrant:
        """Ask Twitch for a device code and the URL to show the user."""
        try:
            response = await self._client.post(
                DEVICE_URL, data={"client_id": self._client_id, "scopes": self._scopes}
            )
        except httpx.HTTPError as exc:
            raise ProviderError(PROVIDER, f"could not reach the device endpoint: {exc}") from exc

        if response.status_code >= 400:
            raise ProviderAuthError(
                PROVIDER,
                f"Twitch refused the device request (HTTP {response.status_code}) — "
                "check LIVEPULSE_SECRET_TWITCH_CLIENT_ID",
            )

        try:
            payload = response.json()
            return DeviceGrant(
                device_code=str(payload["device_code"]),
                user_code=str(payload["user_code"]),
                verification_uri=str(payload["verification_uri"]),
                interval=max(
                    self._min_interval, float(payload.get("interval", self._min_interval))
                ),
                expires_in=float(payload.get("expires_in", 1800)),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderError(PROVIDER, "device response was not in the expected shape") from exc

    async def poll(self, grant: DeviceGrant, clock: Clock | None = None) -> StoredToken:
        """Wait for the user to approve, then return the token.

        Raises:
            ProviderAuthError: the user declined, or the code expired.
        """
        active_clock = clock or SystemClock()
        deadline = active_clock.monotonic() + grant.expires_in
        interval = grant.interval

        while active_clock.monotonic() < deadline:
            await asyncio.sleep(interval)

            response = await self._client.post(
                TOKEN_URL,
                data={
                    "client_id": self._client_id,
                    "device_code": grant.device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "scopes": self._scopes,
                },
            )

            if response.status_code == 200:
                return _to_token(response.json(), active_clock, self._scopes)

            message = _error_message(response)
            if "authorization_pending" in message or "pending" in message:
                continue
            if "slow_down" in message:
                # Twitch says we are asking too often; obey rather than persist.
                interval += max(self._min_interval, 1.0)
                continue

            raise ProviderAuthError(PROVIDER, f"authorisation failed: {message}")

        raise ProviderAuthError(PROVIDER, "the device code expired before it was approved")


class UserTokenSource:
    """A stored user token, refreshed as needed.

    The counterpart to :class:`~livepulse.providers.twitch.auth.AppTokenSource`,
    but backed by the database because a refresh token has to outlive the
    process.
    """

    __slots__ = ("_cached", "_client", "_client_id", "_client_secret", "_clock", "_lock", "_tokens")

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        http: httpx.AsyncClient,
        tokens: TokenRepository,
        clock: Clock | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._client = http
        self._tokens = tokens
        self._clock = clock or SystemClock()
        self._cached: StoredToken | None = None
        self._lock = asyncio.Lock()

    @property
    def client_id(self) -> str:
        return self._client_id

    async def token(self) -> str:
        """Return a usable user token, refreshing it if needed.

        Raises:
            ProviderAuthError: nobody has logged in yet, or the refresh token
                has been revoked. The message names the command to fix it,
                because on a headless device there is no other prompt.
        """
        async with self._lock:
            stored = self._cached or await self._tokens.get(PROVIDER)

            if stored is None:
                raise ProviderAuthError(
                    PROVIDER,
                    "no Twitch user token — run 'livepulse provider login twitch'. "
                    "EventSub over WebSocket cannot use an app token.",
                )

            if stored.is_fresh(self._clock.now(), REFRESH_MARGIN):
                self._cached = stored
                return stored.access_token

            refreshed = await self._refresh(stored)
            self._cached = refreshed
            return refreshed.access_token

    async def invalidate(self) -> None:
        """Drop the cached token after the platform rejected it."""
        async with self._lock:
            self._cached = None

    async def _refresh(self, stored: StoredToken) -> StoredToken:
        if not stored.refresh_token:
            raise ProviderAuthError(
                PROVIDER,
                "the Twitch user token expired and there is no refresh token — "
                "run 'livepulse provider login twitch'",
            )

        try:
            response = await self._client.post(
                TOKEN_URL,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": stored.refresh_token,
                },
            )
        except httpx.HTTPError as exc:
            raise ProviderError(PROVIDER, f"could not reach the token endpoint: {exc}") from exc

        if response.status_code >= 400:
            # A revoked refresh token is terminal: only a human can fix it.
            raise ProviderAuthError(
                PROVIDER,
                "the Twitch refresh token was rejected — "
                "run 'livepulse provider login twitch' again",
            )

        token = _to_token(response.json(), self._clock, stored.scopes)
        await self._tokens.save(PROVIDER, token)
        log.info("twitch user token refreshed")
        return token


def _to_token(payload: object, clock: Clock, scopes: str) -> StoredToken:
    if not isinstance(payload, dict):
        raise ProviderError(PROVIDER, "token response was not an object")

    try:
        access = str(payload["access_token"])
    except (KeyError, TypeError) as exc:
        raise ProviderError(PROVIDER, "token response had no access_token") from exc

    expires_at: datetime | None = None
    raw_expiry = payload.get("expires_in")
    if isinstance(raw_expiry, int | float | str):
        try:
            expires_at = clock.now() + timedelta(seconds=int(raw_expiry))
        except (TypeError, ValueError):
            expires_at = None

    refresh = payload.get("refresh_token")
    return StoredToken(
        access_token=access,
        refresh_token=str(refresh) if refresh else None,
        expires_at=expires_at,
        scopes=scopes,
        obtained_at=clock.now(),
    )


def _error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text or f"HTTP {response.status_code}"
    if isinstance(payload, dict):
        return str(payload.get("message") or payload.get("error") or payload)
    return str(payload)
