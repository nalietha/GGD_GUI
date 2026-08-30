"""Twitch EventSub over WebSocket.

Gets us off the blind poll: ``stream.online`` and ``stream.offline`` arrive in
about a second over one socket, instead of being discovered up to thirty
seconds late by asking.

What this transport does **not** do, and why the supervisor exists
------------------------------------------------------------------

There is no delivery retry, no acknowledgement, and no replay. A notification
sent while the socket is down is gone permanently. An unplanned disconnect also
destroys the subscriptions bound to that session, so reconnecting means a new
session and a fresh set of subscriptions — with another gap in between.

So this class reports facts and lets
:class:`~livepulse.ingest.supervisor.PushSupervisor` decide what they mean. In
particular it calls ``session.subscribed(...)`` **only with the refs whose
subscriptions were actually accepted**, and only once they are confirmed —
never merely because a socket opened. That call is what triggers the
reconciliation poll that recovers anything missed.

Protocol notes worth knowing
----------------------------

* Twitch allows about **10 seconds** after ``session_welcome`` to create
  subscriptions before it closes the connection, so they are created
  concurrently rather than one at a time.
* ``session_reconnect`` is a *planned* move. Subscriptions carry over to the
  new session, so they are not recreated — but the socket did change, so we
  reconcile anyway. One batched poll is cheap next to a silently wrong stand.
* Twitch may redeliver a message, so ``metadata.message_id`` is deduplicated.
* Missing keepalives are the only signal that a socket has died without
  closing, so a read timeout is treated as a disconnect.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
import structlog

from livepulse.core.errors import ProviderAuthError, ProviderError
from livepulse.core.models import ChannelRef, LiveState, StateSource
from livepulse.providers.base import PushSession
from livepulse.providers.twitch.userauth import UserTokenSource

__all__ = ["EVENTSUB_URL", "ConnectFn", "EventSubWatcher", "WebSocketLike"]

log = structlog.get_logger(__name__)

PROVIDER = "twitch"
EVENTSUB_URL = "wss://eventsub.wss.twitch.tv/ws"
SUBSCRIPTIONS_URL = "https://api.twitch.tv/helix/eventsub/subscriptions"

#: The events we care about. Both are version 1 and need no OAuth scope.
ONLINE = "stream.online"
OFFLINE = "stream.offline"
EVENT_TYPES = (ONLINE, OFFLINE)

#: Twitch closes the connection if subscriptions are not created shortly after
#: session_welcome. Ten seconds is the documented allowance; we aim well under.
SUBSCRIBE_DEADLINE = 8.0

#: Parallel subscription creates. Enough to fit 32 requests inside the window,
#: modest enough not to look like an attack.
SUBSCRIBE_CONCURRENCY = 8

#: Grace added to Twitch's keepalive interval before we call the socket dead.
#: One missed keepalive is noise; two is a dead connection.
KEEPALIVE_GRACE = 10.0

#: How many message ids to remember for deduplication. Twitch may redeliver.
SEEN_MESSAGE_IDS = 256


class WebSocketLike(Protocol):
    """The slice of a websocket connection this module uses."""

    async def recv(self) -> str | bytes: ...

    async def close(self) -> None: ...


#: Opens a connection. Injected so tests can script a whole session without a
#: network, and so the websockets library stays a detail rather than a coupling.
ConnectFn = Callable[[str], AbstractAsyncContextManager[WebSocketLike]]


@dataclass(frozen=True, slots=True)
class Welcome:
    session_id: str
    keepalive_seconds: float


class EventSubWatcher:
    """Holds a live EventSub subscription for a set of channels."""

    __slots__ = ("_client", "_connect", "_seen", "_tokens", "_url")

    def __init__(
        self,
        tokens: UserTokenSource,
        http: httpx.AsyncClient,
        connect: ConnectFn | None = None,
        url: str = EVENTSUB_URL,
    ) -> None:
        self._tokens = tokens
        self._client = http
        self._connect = connect or _default_connect
        self._url = url
        self._seen: deque[str] = deque(maxlen=SEEN_MESSAGE_IDS)

    async def watch(self, refs: Sequence[ChannelRef], session: PushSession) -> None:
        """Run one subscription lifetime.

        Returns on a clean close and raises on a failure — either way the
        supervisor restarts us with a fresh session, which is correct because
        an unplanned disconnect invalidates the subscriptions anyway.

        The internal loop exists only for ``session_reconnect``, Twitch's
        planned handover, where the subscriptions do carry across.
        """
        if not refs:
            return

        url = self._url
        needs_subscriptions = True

        while True:
            async with self._connect(url) as socket:
                welcome = await self._await_welcome(socket)

                if needs_subscriptions:
                    covered = await self._subscribe(welcome.session_id, refs)
                    if not covered:
                        raise ProviderError(PROVIDER, "no EventSub subscriptions could be created")
                    needs_subscriptions = False
                else:
                    # A planned reconnect carries subscriptions over.
                    covered = list(refs)

                # Only now is anything actually covered. The supervisor turns
                # this into a reconciliation poll, which is what recovers
                # whatever changed while we were not listening.
                session.subscribed(covered)

                reconnect_url = await self._pump(socket, welcome, session)
                if reconnect_url is None:
                    return
                url = reconnect_url

    # ----------------------------------------------------------------- #
    # Connection phases
    # ----------------------------------------------------------------- #

    async def _await_welcome(self, socket: WebSocketLike) -> Welcome:
        """Read the opening message, which carries the session id."""
        message = await self._read(socket, timeout=SUBSCRIBE_DEADLINE)
        kind = _metadata(message).get("message_type")

        if kind != "session_welcome":
            raise ProviderError(PROVIDER, f"expected session_welcome, got {kind!r}")

        payload = message.get("payload") or {}
        session_info = payload.get("session") or {}
        session_id = session_info.get("id")
        if not session_id:
            raise ProviderError(PROVIDER, "session_welcome carried no session id")

        keepalive = session_info.get("keepalive_timeout_seconds")
        return Welcome(
            session_id=str(session_id),
            keepalive_seconds=float(keepalive) if keepalive else 30.0,
        )

    async def _subscribe(self, session_id: str, refs: Sequence[ChannelRef]) -> list[ChannelRef]:
        """Create online and offline subscriptions for every ref.

        Returns only the refs that are genuinely covered. A partial failure
        leaves the rest on ordinary polling rather than pretending they are
        pushed — reporting a channel as covered when it is not would silence it
        without anyone noticing.
        """
        token = await self._tokens.token()
        limiter = asyncio.Semaphore(SUBSCRIBE_CONCURRENCY)

        async def create(ref: ChannelRef, event_type: str) -> tuple[ChannelRef, str, bool]:
            async with limiter:
                ok = await self._create_subscription(session_id, ref, event_type, token)
                return ref, event_type, ok

        work = [create(ref, event_type) for ref in refs for event_type in EVENT_TYPES]
        results = await asyncio.gather(*work, return_exceptions=True)

        # A channel counts as covered only when *both* directions subscribed;
        # online-without-offline would light a node that never goes out.
        succeeded: dict[str, set[str]] = {}
        for outcome in results:
            if isinstance(outcome, ProviderAuthError):
                # Terminal and specific: it names the command that fixes it.
                # Letting gather flatten this into a generic "nothing
                # subscribed" would strip the only actionable detail, and on a
                # headless device that message is the whole user interface.
                raise outcome
            if isinstance(outcome, BaseException):
                log.warning("eventsub subscription raised", error=str(outcome))
                continue
            ref, event_type, ok = outcome
            if ok:
                succeeded.setdefault(ref.platform_ref, set()).add(event_type)

        covered = [
            ref for ref in refs if succeeded.get(ref.platform_ref, set()) >= set(EVENT_TYPES)
        ]

        log.info(
            "eventsub subscriptions created",
            requested=len(refs),
            covered=len(covered),
            session=session_id[:8],
        )
        return covered

    async def _create_subscription(
        self, session_id: str, ref: ChannelRef, event_type: str, token: str
    ) -> bool:
        body = {
            "type": event_type,
            "version": "1",
            "condition": {"broadcaster_user_id": ref.platform_ref},
            "transport": {"method": "websocket", "session_id": session_id},
        }

        try:
            response = await self._client.post(
                SUBSCRIPTIONS_URL,
                json=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Client-Id": self._tokens.client_id,
                    "Content-Type": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            log.warning("eventsub subscribe failed", handle=ref.handle, error=str(exc))
            return False

        if response.status_code in (200, 202):
            return True

        if response.status_code in (401, 403):
            # A user token is mandatory here; an app token is refused. Surfacing
            # this as an auth error is what tells the user to log in.
            raise ProviderAuthError(
                PROVIDER,
                "EventSub rejected the token — run 'livepulse provider login twitch'. "
                "The WebSocket transport requires a user token, not an app token.",
            )

        log.warning(
            "eventsub subscribe rejected",
            handle=ref.handle,
            type=event_type,
            status=response.status_code,
            body=response.text[:200],
        )
        return False

    async def _pump(
        self, socket: WebSocketLike, welcome: Welcome, session: PushSession
    ) -> str | None:
        """Read messages until the socket ends or asks us to move.

        Returns:
            A reconnect URL for a planned handover, or ``None`` on a clean end.
        """
        timeout = welcome.keepalive_seconds + KEEPALIVE_GRACE

        while True:
            message = await self._read(socket, timeout=timeout)
            metadata = _metadata(message)
            kind = metadata.get("message_type")

            if kind == "session_keepalive":
                continue

            if kind == "notification":
                # Deduplication applies to notifications only. Twitch documents
                # that *those* may be redelivered, and acting twice would
                # double-count. Control messages are not redelivered, and
                # dropping one — a welcome or a reconnect — would strand us
                # until the keepalive timeout with no idea why.
                message_id = metadata.get("message_id")
                if message_id:
                    if message_id in self._seen:
                        continue
                    self._seen.append(str(message_id))

                await self._handle_notification(message, metadata, session)
                continue

            if kind == "session_reconnect":
                payload = message.get("payload") or {}
                url = (payload.get("session") or {}).get("reconnect_url")
                if url:
                    log.info("eventsub asked us to reconnect")
                    return str(url)
                return None

            if kind == "revocation":
                # The subscription is gone: bad token, or the channel vanished.
                # Ending the watch drops those channels back to polling, which
                # is the safe direction.
                log.warning("eventsub subscription revoked", payload=message.get("payload"))
                return None

            log.debug("ignoring unknown eventsub message", kind=kind)

    async def _handle_notification(
        self, message: dict[str, Any], metadata: dict[str, Any], session: PushSession
    ) -> None:
        payload = message.get("payload") or {}
        event = payload.get("event") or {}

        broadcaster_id = event.get("broadcaster_user_id")
        if not broadcaster_id:
            log.warning("eventsub notification had no broadcaster id")
            return

        event_type = metadata.get("subscription_type") or (payload.get("subscription") or {}).get(
            "type"
        )

        if event_type not in EVENT_TYPES:
            log.debug("ignoring unrelated eventsub notification", type=event_type)
            return

        is_live = event_type == ONLINE
        ref = ChannelRef(
            provider=PROVIDER,
            platform_ref=str(broadcaster_id),
            handle=str(event.get("broadcaster_user_login") or broadcaster_id),
        )

        log.info("eventsub notification", handle=ref.handle, live=is_live)
        await session.emit(
            ref,
            LiveState(
                is_live=is_live,
                started_at=_parse_time(event.get("started_at")),
                source=StateSource.PUSH,
            ),
        )

    async def _read(self, socket: WebSocketLike, timeout: float) -> dict[str, Any]:
        """Read one JSON message, treating silence as a dead connection.

        A socket that stops sending keepalives without closing is the failure
        mode with no other signal, so the timeout *is* the detection.
        """
        try:
            raw = await asyncio.wait_for(socket.recv(), timeout=timeout)
        except TimeoutError as exc:
            raise ProviderError(
                PROVIDER, f"no EventSub message within {timeout:.0f}s — assuming the socket died"
            ) from exc

        try:
            decoded = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise ProviderError(PROVIDER, "EventSub sent a non-JSON message") from exc

        if not isinstance(decoded, dict):
            raise ProviderError(PROVIDER, "EventSub sent an unexpected message shape")
        return decoded


def _metadata(message: dict[str, Any]) -> dict[str, Any]:
    metadata = message.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _parse_time(value: object) -> Any:
    from datetime import datetime

    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _default_connect(url: str) -> AbstractAsyncContextManager[WebSocketLike]:
    """Open a real WebSocket.

    Imported lazily so the module stays importable — and the rest of the
    application testable — without the websockets package present.
    """
    import websockets

    return websockets.connect(url)
