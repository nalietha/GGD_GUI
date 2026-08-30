"""Twitch EventSub over WebSocket.

Driven by a scripted fake socket, so a whole session — welcome, subscribe,
notify, keepalive, reconnect, revoke, die — runs deterministically with no
network.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from livepulse.core.errors import ProviderAuthError, ProviderError
from livepulse.core.models import ChannelRef, LiveState, StateSource
from livepulse.providers.twitch.eventsub import (
    KEEPALIVE_GRACE,
    OFFLINE,
    ONLINE,
    EventSubWatcher,
)


def ref(user_id: str, handle: str = "someone") -> ChannelRef:
    return ChannelRef(provider="twitch", platform_ref=user_id, handle=handle)


def welcome(session_id: str = "sess-1", keepalive: float = 30.0) -> str:
    return json.dumps(
        {
            "metadata": {"message_id": f"w-{session_id}", "message_type": "session_welcome"},
            "payload": {"session": {"id": session_id, "keepalive_timeout_seconds": keepalive}},
        }
    )


def notification(
    user_id: str, event_type: str = ONLINE, message_id: str = "n-1", login: str = "someone"
) -> str:
    return json.dumps(
        {
            "metadata": {
                "message_id": message_id,
                "message_type": "notification",
                "subscription_type": event_type,
            },
            "payload": {
                "event": {
                    "broadcaster_user_id": user_id,
                    "broadcaster_user_login": login,
                    "started_at": "2026-03-01T18:30:00Z",
                }
            },
        }
    )


def keepalive(message_id: str = "k-1") -> str:
    return json.dumps({"metadata": {"message_id": message_id, "message_type": "session_keepalive"}})


def reconnect(
    url: str = "wss://eventsub.wss.twitch.tv/ws?reconnect=1", message_id: str = "r-1"
) -> str:
    return json.dumps(
        {
            "metadata": {"message_id": message_id, "message_type": "session_reconnect"},
            "payload": {"session": {"id": "sess-1", "reconnect_url": url}},
        }
    )


def revocation() -> str:
    return json.dumps(
        {
            "metadata": {"message_id": "v-1", "message_type": "revocation"},
            "payload": {"subscription": {"status": "authorization_revoked"}},
        }
    )


class FakeSocket:
    """Replays a script, then blocks so the read timeout can be exercised."""

    def __init__(self, script: list[str]) -> None:
        self._script = list(script)
        self.closed = False

    async def recv(self) -> str:
        if self._script:
            return self._script.pop(0)
        # Nothing more to say: mimic a socket that has gone quiet without
        # closing, which is the failure the keepalive timeout exists to catch.
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")

    async def close(self) -> None:
        self.closed = True


class FakeTransport:
    """Hands out a socket per connection, in order."""

    def __init__(self, *scripts: list[str]) -> None:
        self.scripts = list(scripts)
        self.sockets: list[FakeSocket] = []
        self.urls: list[str] = []

    def connect(self, url: str):
        self.urls.append(url)
        script = self.scripts.pop(0) if self.scripts else []
        socket = FakeSocket(script)
        self.sockets.append(socket)

        @asynccontextmanager
        async def opened() -> AsyncIterator[FakeSocket]:
            try:
                yield socket
            finally:
                await socket.close()

        return opened()


class RecordingSession:
    """Captures what the watcher reports to its supervisor."""

    def __init__(self) -> None:
        self.subscribed_calls: list[list[ChannelRef]] = []
        self.events: list[tuple[ChannelRef, LiveState]] = []

    async def emit(self, channel_ref: ChannelRef, state: LiveState) -> None:
        self.events.append((channel_ref, state))

    def subscribed(self, refs: Sequence[ChannelRef]) -> None:
        self.subscribed_calls.append(list(refs))


class FakeHelix:
    """Accepts or refuses EventSub subscription creates."""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.status = 202
        self.reject_types: set[str] = set()

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.created.append(body)

        if body["type"] in self.reject_types:
            return httpx.Response(400, json={"message": "nope"})
        if self.status != 202:
            return httpx.Response(self.status, json={"message": "refused"})
        return httpx.Response(202, json={"data": [{"id": "sub-1"}]})


class StubTokens:
    client_id = "client-id"

    async def token(self) -> str:
        return "user-token"

    async def invalidate(self) -> None:
        return None


@pytest.fixture
def helix() -> FakeHelix:
    return FakeHelix()


@pytest.fixture
def helix_http(helix: FakeHelix) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(helix.handler))


def build(transport: FakeTransport, http: httpx.AsyncClient) -> EventSubWatcher:
    return EventSubWatcher(StubTokens(), http, connect=transport.connect)  # type: ignore[arg-type]


async def run_watch(watcher: EventSubWatcher, refs: list[ChannelRef], session: RecordingSession):
    """Run watch() to completion, returning any exception it raised."""
    try:
        await asyncio.wait_for(watcher.watch(refs, session), timeout=2.0)
    except Exception as exc:
        return exc
    return None


class TestSubscribing:
    async def test_creates_online_and_offline_for_every_channel(
        self, helix: FakeHelix, helix_http: httpx.AsyncClient
    ) -> None:
        """Online without offline would light a node that never goes out."""
        transport = FakeTransport([welcome(), reconnect("")])
        session = RecordingSession()

        await run_watch(build(transport, helix_http), [ref("1"), ref("2")], session)

        types = {(c["type"], c["condition"]["broadcaster_user_id"]) for c in helix.created}
        assert types == {(ONLINE, "1"), (OFFLINE, "1"), (ONLINE, "2"), (OFFLINE, "2")}

    async def test_uses_the_websocket_transport_and_session_id(
        self, helix: FakeHelix, helix_http: httpx.AsyncClient
    ) -> None:
        transport = FakeTransport([welcome("sess-abc"), reconnect("")])

        await run_watch(build(transport, helix_http), [ref("1")], RecordingSession())

        assert helix.created[0]["transport"] == {
            "method": "websocket",
            "session_id": "sess-abc",
        }

    async def test_reports_subscribed_only_after_confirmation(
        self, helix: FakeHelix, helix_http: httpx.AsyncClient
    ) -> None:
        """Not when the socket opens — nothing is covered until Twitch accepts.

        That call is what triggers the reconciliation poll, so firing it early
        would reconcile against a subscription that does not exist yet.
        """
        transport = FakeTransport([welcome(), reconnect("")])
        session = RecordingSession()

        await run_watch(build(transport, helix_http), [ref("1")], session)

        assert len(session.subscribed_calls) >= 1
        assert [r.platform_ref for r in session.subscribed_calls[0]] == ["1"]

    async def test_a_partly_failed_subscribe_covers_only_what_worked(
        self, helix: FakeHelix, helix_http: httpx.AsyncClient
    ) -> None:
        """Reporting an uncovered channel as pushed would silence it: it would
        stop polling and never receive events either."""
        helix.reject_types = {OFFLINE}
        transport = FakeTransport([welcome(), reconnect("")])
        session = RecordingSession()

        error = await run_watch(build(transport, helix_http), [ref("1")], session)

        assert isinstance(error, ProviderError)
        assert session.subscribed_calls == [], "nothing was fully covered"

    async def test_a_rejected_token_says_to_log_in(
        self, helix: FakeHelix, helix_http: httpx.AsyncClient
    ) -> None:
        """The WebSocket transport refuses an app token; only a human fixes it."""
        helix.status = 401
        transport = FakeTransport([welcome(), reconnect("")])

        error = await run_watch(build(transport, helix_http), [ref("1")], RecordingSession())

        assert isinstance(error, ProviderAuthError)
        assert "provider login twitch" in str(error)

    async def test_no_refs_does_nothing(
        self, helix: FakeHelix, helix_http: httpx.AsyncClient
    ) -> None:
        transport = FakeTransport([welcome()])
        await run_watch(build(transport, helix_http), [], RecordingSession())
        assert helix.created == []


class TestNotifications:
    async def test_a_go_live_is_emitted(
        self, helix: FakeHelix, helix_http: httpx.AsyncClient
    ) -> None:
        transport = FakeTransport([welcome(), notification("1"), reconnect("")])
        session = RecordingSession()

        await run_watch(build(transport, helix_http), [ref("1")], session)

        assert len(session.events) == 1
        emitted_ref, state = session.events[0]
        assert emitted_ref.platform_ref == "1"
        assert state.is_live is True
        assert state.source is StateSource.PUSH
        assert state.started_at is not None

    async def test_a_go_offline_is_emitted(
        self, helix: FakeHelix, helix_http: httpx.AsyncClient
    ) -> None:
        transport = FakeTransport([welcome(), notification("1", OFFLINE), reconnect("")])
        session = RecordingSession()

        await run_watch(build(transport, helix_http), [ref("1")], session)

        assert session.events[0][1].is_live is False

    async def test_keepalives_are_ignored(
        self, helix: FakeHelix, helix_http: httpx.AsyncClient
    ) -> None:
        transport = FakeTransport(
            [welcome(), keepalive("k1"), keepalive("k2"), notification("1"), reconnect("")]
        )
        session = RecordingSession()

        await run_watch(build(transport, helix_http), [ref("1")], session)

        assert len(session.events) == 1

    async def test_a_redelivered_message_is_ignored(
        self, helix: FakeHelix, helix_http: httpx.AsyncClient
    ) -> None:
        """Twitch may resend; acting twice would double-count."""
        transport = FakeTransport(
            [
                welcome(),
                notification("1", message_id="dup"),
                notification("1", message_id="dup"),
                reconnect(""),
            ]
        )
        session = RecordingSession()

        await run_watch(build(transport, helix_http), [ref("1")], session)

        assert len(session.events) == 1

    async def test_control_messages_are_not_deduplicated(self) -> None:
        """Dedupe is for notifications only.

        Twitch documents that *notifications* may be redelivered. Control
        messages are not, and dropping one — a welcome or a reconnect — would
        strand the watcher until the keepalive timeout with nothing to explain
        why. Two reconnects sharing an id must both be acted on.
        """
        import httpx as _httpx

        helix = FakeHelix()
        http = _httpx.AsyncClient(transport=_httpx.MockTransport(helix.handler))
        transport = FakeTransport(
            [welcome(), reconnect("wss://second.example/ws", message_id="same")],
            [welcome("sess-2"), reconnect("", message_id="same")],
        )
        session = RecordingSession()

        error = await run_watch(build(transport, http), [ref("1")], session)

        assert error is None, "the second reconnect was deduplicated and hung"
        assert transport.urls[1] == "wss://second.example/ws"
        await http.aclose()

    async def test_an_unrelated_event_type_is_ignored(
        self, helix: FakeHelix, helix_http: httpx.AsyncClient
    ) -> None:
        odd = json.dumps(
            {
                "metadata": {
                    "message_id": "x",
                    "message_type": "notification",
                    "subscription_type": "channel.follow",
                },
                "payload": {"event": {"broadcaster_user_id": "1"}},
            }
        )
        transport = FakeTransport([welcome(), odd, reconnect("")])
        session = RecordingSession()

        await run_watch(build(transport, helix_http), [ref("1")], session)

        assert session.events == []

    async def test_a_notification_without_a_broadcaster_is_ignored(
        self, helix: FakeHelix, helix_http: httpx.AsyncClient
    ) -> None:
        broken = json.dumps(
            {
                "metadata": {
                    "message_id": "x",
                    "message_type": "notification",
                    "subscription_type": ONLINE,
                },
                "payload": {"event": {}},
            }
        )
        transport = FakeTransport([welcome(), broken, reconnect("")])
        session = RecordingSession()

        await run_watch(build(transport, helix_http), [ref("1")], session)

        assert session.events == []


class TestReconnect:
    async def test_a_planned_reconnect_moves_to_the_new_url(
        self, helix: FakeHelix, helix_http: httpx.AsyncClient
    ) -> None:
        transport = FakeTransport(
            [welcome(), reconnect("wss://new.example/ws")],
            [welcome("sess-2"), reconnect("")],
        )
        session = RecordingSession()

        await run_watch(build(transport, helix_http), [ref("1")], session)

        assert transport.urls[1] == "wss://new.example/ws"

    async def test_a_planned_reconnect_does_not_recreate_subscriptions(
        self, helix: FakeHelix, helix_http: httpx.AsyncClient
    ) -> None:
        """Subscriptions carry across a planned handover."""
        transport = FakeTransport(
            [welcome(), reconnect("wss://new.example/ws")],
            [welcome("sess-2"), reconnect("")],
        )

        await run_watch(build(transport, helix_http), [ref("1")], RecordingSession())

        assert len(helix.created) == 2, "one online + one offline, created once"

    async def test_a_planned_reconnect_still_reconciles(
        self, helix: FakeHelix, helix_http: httpx.AsyncClient
    ) -> None:
        """The socket changed, so a batched poll is cheap insurance against a
        gap during the handover."""
        transport = FakeTransport(
            [welcome(), reconnect("wss://new.example/ws")],
            [welcome("sess-2"), reconnect("")],
        )
        session = RecordingSession()

        await run_watch(build(transport, helix_http), [ref("1")], session)

        assert len(session.subscribed_calls) == 2


class TestFailures:
    async def test_a_missing_welcome_is_an_error(
        self, helix: FakeHelix, helix_http: httpx.AsyncClient
    ) -> None:
        transport = FakeTransport([keepalive()])

        error = await run_watch(build(transport, helix_http), [ref("1")], RecordingSession())

        assert isinstance(error, ProviderError)
        assert "session_welcome" in str(error)

    async def test_a_welcome_without_a_session_id_is_an_error(
        self, helix: FakeHelix, helix_http: httpx.AsyncClient
    ) -> None:
        bad = json.dumps(
            {"metadata": {"message_type": "session_welcome"}, "payload": {"session": {}}}
        )
        transport = FakeTransport([bad])

        error = await run_watch(build(transport, helix_http), [ref("1")], RecordingSession())
        assert isinstance(error, ProviderError)

    async def test_non_json_is_an_error(
        self, helix: FakeHelix, helix_http: httpx.AsyncClient
    ) -> None:
        transport = FakeTransport(["<html>nope</html>"])

        error = await run_watch(build(transport, helix_http), [ref("1")], RecordingSession())

        assert isinstance(error, ProviderError)
        assert "non-JSON" in str(error)

    async def test_a_silent_socket_is_treated_as_dead(
        self, helix: FakeHelix, helix_http: httpx.AsyncClient
    ) -> None:
        """A socket that stops sending keepalives without closing is the failure
        with no other signal, so the read timeout *is* the detection."""
        transport = FakeTransport([welcome(session_id="s", keepalive=0.01)])
        watcher = build(transport, helix_http)

        # Keepalive of 0.01s plus grace, shortened so the test is instant.
        import livepulse.providers.twitch.eventsub as module

        original = module.KEEPALIVE_GRACE
        module.KEEPALIVE_GRACE = 0.05
        try:
            error = await run_watch(watcher, [ref("1")], RecordingSession())
        finally:
            module.KEEPALIVE_GRACE = original

        assert isinstance(error, ProviderError)
        assert "socket died" in str(error)

    async def test_a_revocation_ends_the_watch(
        self, helix: FakeHelix, helix_http: httpx.AsyncClient
    ) -> None:
        """Ending drops those channels back to polling, the safe direction."""
        transport = FakeTransport([welcome(), revocation()])
        session = RecordingSession()

        error = await run_watch(build(transport, helix_http), [ref("1")], session)

        assert error is None, "a revocation is a clean end, not a crash"

    async def test_the_socket_is_closed_on_the_way_out(
        self, helix: FakeHelix, helix_http: httpx.AsyncClient
    ) -> None:
        transport = FakeTransport([welcome(), reconnect("")])

        await run_watch(build(transport, helix_http), [ref("1")], RecordingSession())

        assert transport.sockets[0].closed is True


class TestGraceConstant:
    def test_grace_is_generous_enough_for_one_missed_keepalive(self) -> None:
        """One missed keepalive is noise; two is a dead connection."""
        assert KEEPALIVE_GRACE >= 10.0


class TestSupervisorIntegration:
    """The watcher plus the supervisor, joined up.

    This is the pairing that answers "does a missed notification ever recover?"
    — the watcher reports subscription, the supervisor turns that into a
    reconciliation poll.
    """

    async def test_twitch_provider_satisfies_the_push_protocol(self) -> None:
        from livepulse.providers.base import ProviderConfig, PushProvider
        from livepulse.providers.twitch.poll import TwitchProvider

        provider = TwitchProvider(
            ProviderConfig(provider_id="twitch", secrets={"client_id": "a", "client_secret": "b"})
        )
        assert isinstance(provider, PushProvider)
        await provider.aclose()

    async def test_watch_without_a_user_token_says_to_log_in(self) -> None:
        """A registry-built provider is poll-only; watch() explains why."""
        from livepulse.providers.base import ProviderConfig
        from livepulse.providers.twitch.poll import TwitchProvider

        provider = TwitchProvider(
            ProviderConfig(provider_id="twitch", secrets={"client_id": "a", "client_secret": "b"})
        )
        try:
            with pytest.raises(ProviderAuthError, match="provider login twitch"):
                await provider.watch([ref("1")], RecordingSession())
        finally:
            await provider.aclose()

    async def test_subscribing_drives_a_reconcile(
        self, helix: FakeHelix, helix_http: httpx.AsyncClient
    ) -> None:
        """End to end: the watcher confirms a subscription and the supervisor
        makes those channels due immediately, so anything missed while
        disconnected is fetched rather than lost forever."""
        import random
        from uuid import uuid4

        from livepulse.config.settings import IngestSettings
        from livepulse.core.bus import EventBus
        from livepulse.core.clock import ManualClock
        from livepulse.ingest.scheduler import IngestScheduler
        from livepulse.ingest.supervisor import PushSupervisor
        from livepulse.providers.manual import ManualProvider

        clock = ManualClock()
        bus = EventBus()
        scheduler = IngestScheduler(
            bus, IngestSettings(jitter=0.0), clock=clock, rng=random.Random(1)
        )

        # A stand-in poller for the reconcile side; the watcher supplies push.
        poller = ManualProvider()
        scheduler.register(poller)
        channel_id = uuid4()
        poll_ref = await poller.resolve("1")
        scheduler.track(channel_id, poll_ref, last=LiveState.offline())
        await scheduler.poll_once()

        clock.advance(5.0)
        assert await scheduler.poll_once() == 0, "not due yet"

        supervisor = PushSupervisor(scheduler, clock=clock, rng=random.Random(1))
        transport = FakeTransport([welcome(), reconnect("")])
        watcher = build(transport, helix_http)

        supervisor.watch(watcher, "manual", {channel_id: poll_ref})  # type: ignore[arg-type]
        await supervisor.start()
        await asyncio.sleep(0.05)

        assert await scheduler.poll_once() == 1, "subscribing should force a reconcile"
        await supervisor.stop()
