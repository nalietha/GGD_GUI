"""Twitch Helix provider.

Implements the two things the scheduler asks of any platform: turn a handle
into a stable id, and report whether a batch of channels is live.

Everything else — batching, jitter, backoff, rate limiting, circuit breaking —
is already the scheduler's, so this file is small. That is the abstraction
working: the platform-specific part *should* be small.

Differences from V1's two clients:

* It chunks at 100. V1's C# client did not chunk at all, so more than 100
  streamers would have produced a request Helix rejects; its Python client did.
  Two clients, one of them wrong, and no test that could tell.
* One shared ``AsyncClient`` with connection pooling. V1 constructed a new
  ``HttpClient`` per call, which is the standard way to exhaust sockets.
* 429 is surfaced as ``ProviderRateLimited`` carrying ``Retry-After``, so the
  scheduler waits the amount actually requested. V1 had no handling at all.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

import httpx
import structlog

from livepulse.core.errors import (
    ChannelNotFoundError,
    ProviderAuthError,
    ProviderError,
    ProviderRateLimited,
)
from livepulse.core.models import AuthKind, ChannelRef, LiveState, StateSource
from livepulse.providers.base import (
    BaseProvider,
    Capabilities,
    ProviderConfig,
    PushSession,
    handle_from_url,
)
from livepulse.providers.registry import register_provider
from livepulse.providers.twitch.auth import AppTokenSource
from livepulse.providers.twitch.eventsub import ConnectFn, EventSubWatcher
from livepulse.providers.twitch.userauth import UserTokenSource

__all__ = ["PROVIDER_ID", "TwitchProvider"]

log = structlog.get_logger(__name__)

PROVIDER_ID = "twitch"
HELIX = "https://api.twitch.tv/helix"

#: Helix caps repeated `user_id` / `login` parameters at 100 per request.
MAX_BATCH = 100

#: Twitch's published budget is generous (800 points/minute for an app token),
#: so this floor exists to stop *us* being silly rather than to satisfy them.
MIN_POLL_INTERVAL = 10.0

#: Fallback when a 429 arrives without a usable Retry-After.
DEFAULT_RETRY_AFTER = 60.0

#: Helix takes repeated keys (``user_id=1&user_id=2``), so query parameters are
#: a list of pairs rather than a mapping. Widened to httpx's own value type
#: because a list is invariant and ``list[tuple[str, str]]`` will not fit.
QueryParams = list[tuple[str, str | int | float | bool | None]]


class TwitchProvider(BaseProvider):
    """Polls Twitch Helix for live status."""

    id = PROVIDER_ID
    caps = Capabilities(
        supports_batch=True,
        max_batch=MAX_BATCH,
        # Twitch supports push, but only with a user token. A provider built
        # without one still satisfies this contract for polling; watch() then
        # says what to do about it.
        supports_push=True,
        needs_auth=AuthKind.OAUTH_APP,
        min_poll_interval=MIN_POLL_INTERVAL,
    )

    def __init__(
        self,
        config: ProviderConfig,
        http: httpx.AsyncClient | None = None,
        auth: AppTokenSource | None = None,
        user_auth: UserTokenSource | None = None,
        connect: ConnectFn | None = None,
    ) -> None:
        super().__init__(config)

        # Injected in tests via httpx.MockTransport, so the whole provider is
        # exercised with no network and no credentials.
        self._owns_client = http is None
        self._http = http or httpx.AsyncClient(timeout=config.timeout)

        if auth is not None:
            self._auth = auth
        else:
            self._auth = AppTokenSource(
                config.require_secret("client_id"),
                config.require_secret("client_secret"),
                self._http,
            )

        # Push needs a user token, which needs the database, which the registry
        # factory has no access to. So it is injected by the composition root;
        # a registry-built provider is poll-only and watch() explains why.
        self._user_auth = user_auth
        self._connect = connect

    async def watch(self, refs: Sequence[ChannelRef], session: PushSession) -> None:
        """Hold an EventSub subscription for these channels.

        Raises:
            ProviderAuthError: no user token is configured. The WebSocket
                transport refuses an app token, so this is not something the
                process can resolve on its own.
        """
        if self._user_auth is None:
            raise ProviderAuthError(
                PROVIDER_ID,
                "EventSub needs a user token — run 'livepulse provider login twitch'",
            )

        watcher = EventSubWatcher(self._user_auth, self._http, connect=self._connect)
        await watcher.watch(refs, session)

    async def aclose(self) -> None:
        """Close the HTTP client, but only if we created it."""
        if self._owns_client:
            await self._http.aclose()

    # ----------------------------------------------------------------- #
    # Provider protocol
    # ----------------------------------------------------------------- #

    async def resolve(self, handle: str) -> ChannelRef:
        """Look up a login name and return its numeric Twitch id.

        Called once when a channel is added. The id is what everything else
        keys on, because it survives a rename — which a login name does not.
        """
        login = handle_from_url(handle).lower()
        if not login:
            raise ChannelNotFoundError(PROVIDER_ID, "a channel name is required")

        params: QueryParams = [("login", login)]
        payload = await self._get("/users", params)
        entries = payload.get("data") or []
        if not entries:
            raise ChannelNotFoundError(PROVIDER_ID, f"no Twitch channel named {login!r}")

        user = entries[0]
        return ChannelRef(
            provider=PROVIDER_ID,
            platform_ref=str(user["id"]),
            handle=str(user.get("login") or login),
        )

    async def poll(self, refs: Sequence[ChannelRef]) -> dict[str, LiveState]:
        """Report which of these channels are broadcasting.

        Helix returns only live streams, so absence from the response means
        offline — which :meth:`fill_offline` turns into an explicit observation
        rather than a gap. Returning a partial map would make "offline" and
        "never checked" indistinguishable downstream.
        """
        if not refs:
            return {}
        if len(refs) > MAX_BATCH:
            raise ProviderError(
                PROVIDER_ID, f"batch of {len(refs)} exceeds the Helix limit of {MAX_BATCH}"
            )

        params: QueryParams = [("user_id", ref.platform_ref) for ref in refs]
        payload = await self._get("/streams", params)

        live: dict[str, LiveState] = {}
        for stream in payload.get("data") or []:
            user_id = str(stream.get("user_id", ""))
            if user_id:
                live[user_id] = _to_live_state(stream)

        return self.fill_offline(refs, live, source=StateSource.POLL)

    # ----------------------------------------------------------------- #
    # HTTP
    # ----------------------------------------------------------------- #

    async def _get(self, path: str, params: QueryParams) -> dict[str, Any]:
        """GET a Helix endpoint, refreshing the token once on a 401."""
        response = await self._request(path, params)

        if response.status_code == httpx.codes.UNAUTHORIZED:
            # The token expired earlier than advertised, or was revoked.
            # One retry with a fresh token; a second failure is a real problem.
            await self._auth.invalidate()
            response = await self._request(path, params)

        return self._parse(response)

    async def _request(self, path: str, params: QueryParams) -> httpx.Response:
        token = await self._auth.token()
        try:
            return await self._http.get(
                f"{HELIX}{path}",
                params=params,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Client-Id": self._auth.client_id,
                },
            )
        except httpx.HTTPError as exc:
            # A network failure is transient; the scheduler's breaker counts it
            # and backs off rather than treating it as a bad credential.
            raise ProviderError(PROVIDER_ID, f"request to {path} failed: {exc}") from exc

    def _parse(self, response: httpx.Response) -> dict[str, Any]:
        if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
            raise ProviderRateLimited(PROVIDER_ID, _retry_after(response))

        if response.status_code in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
            raise ProviderAuthError(
                PROVIDER_ID, f"Twitch rejected the request (HTTP {response.status_code})"
            )

        if response.status_code >= 400:
            raise ProviderError(PROVIDER_ID, f"Helix returned HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError(PROVIDER_ID, "Helix returned a non-JSON response") from exc

        if not isinstance(payload, dict):
            raise ProviderError(PROVIDER_ID, "Helix returned an unexpected payload")
        return payload


def _retry_after(response: httpx.Response) -> float:
    """Seconds to wait, from Retry-After or Ratelimit-Reset.

    Helix normally sends ``Ratelimit-Reset`` as a unix timestamp rather than a
    Retry-After delta, so both are read; a guess is the last resort.
    """
    raw = response.headers.get("retry-after")
    if raw:
        try:
            return max(1.0, float(raw))
        except ValueError:
            pass

    reset = response.headers.get("ratelimit-reset")
    if reset:
        try:
            remaining = float(reset) - datetime.now().timestamp()
            if 0 < remaining < 3600:
                return remaining
        except ValueError:
            pass

    return DEFAULT_RETRY_AFTER


def _to_live_state(stream: dict[str, Any]) -> LiveState:
    """Map a Helix stream object into our model.

    Every field beyond ``is_live`` is optional on purpose: Twitch has changed
    this payload before, and a missing title must not cost us the live status.
    """
    return LiveState(
        is_live=True,
        title=stream.get("title") or None,
        category=stream.get("game_name") or None,
        viewers=_as_int(stream.get("viewer_count")),
        started_at=_as_datetime(stream.get("started_at")),
        thumbnail_url=stream.get("thumbnail_url") or None,
        source=StateSource.POLL,
    )


def _as_int(value: object) -> int | None:
    if not isinstance(value, int | float | str):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _as_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _build(config: ProviderConfig) -> TwitchProvider:
    return TwitchProvider(config)


register_provider(
    PROVIDER_ID,
    _build,
    TwitchProvider.caps,
    description="Twitch — live status via Helix",
    url_patterns=[r"twitch\.tv/(?P<handle>[A-Za-z0-9_]{3,25})"],
)
