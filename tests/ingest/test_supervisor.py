"""Push subscription supervision.

The central question these answer: **if the connection drops and a notification
is missed, does the display ever recover?**

Twitch EventSub over WebSocket does not retry, does not acknowledge, and does
not replay. A missed notification is gone permanently, and an unplanned
disconnect destroys the subscriptions bound to that session. So the answer
cannot be "the platform will resend it" — it has to be built here.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Sequence
from uuid import UUID, uuid4

import pytest

from livepulse.config.settings import IngestSettings
from livepulse.core.bus import EventBus
from livepulse.core.clock import ManualClock
from livepulse.core.events import ChannelStateChanged
from livepulse.core.models import ChannelRef, LiveState, StateSource
from livepulse.ingest.scheduler import IngestScheduler
from livepulse.ingest.supervisor import PushSupervisor
from livepulse.providers.base import BaseProvider, Capabilities, ProviderConfig, PushSession


class FakePushProvider(BaseProvider):
    """A push provider whose connection can be dropped on demand.

    Stands in for Twitch EventSub so every failure mode — clean drop, raised
    error, missed notification — can be exercised deterministically.
    """

    id = "fakepush"
    caps = Capabilities(
        supports_batch=True, max_batch=100, supports_push=True, min_poll_interval=1.0
    )

    def __init__(self) -> None:
        super().__init__(ProviderConfig(provider_id=self.id))
        self.live: set[str] = set()
        self.poll_calls = 0
        self.watch_calls = 0
        self.fail_next = False
        self.always_fail = False

        self._connected = asyncio.Event()
        self._drop = asyncio.Event()
        self._session: PushSession | None = None
        self._refs: Sequence[ChannelRef] = ()

    async def resolve(self, handle: str) -> ChannelRef:
        return ChannelRef(provider=self.id, platform_ref=handle, handle=handle)

    async def poll(self, refs: Sequence[ChannelRef]) -> dict[str, LiveState]:
        """The reconciliation path — reports the platform's actual truth."""
        self.poll_calls += 1
        found = {
            ref.platform_ref: LiveState(is_live=True, source=StateSource.POLL)
            for ref in refs
            if ref.platform_ref in self.live
        }
        return self.fill_offline(refs, found)

    async def watch(self, refs: Sequence[ChannelRef], session: PushSession) -> None:
        self.watch_calls += 1
        self._refs = refs
        self._session = session

        if self.always_fail or self.fail_next:
            self.fail_next = False
            raise ConnectionError("socket refused")

        session.subscribed(refs)
        self._connected.set()
        self._drop.clear()

        await self._drop.wait()
        self._connected.clear()
        raise ConnectionResetError("connection lost")

    # --- test controls -------------------------------------------------

    async def wait_connected(self, timeout: float = 1.0) -> None:
        await asyncio.wait_for(self._connected.wait(), timeout)

    def drop(self) -> None:
        """Simulate the network going away.

        Clears the connected flag synchronously: the connection is gone the
        moment the network dies, not when the watch coroutine is next
        scheduled. Otherwise a test can race past the drop entirely.
        """
        self._connected.clear()
        self._drop.set()

    async def push(self, platform_ref: str, is_live: bool) -> None:
        """Deliver a notification over the (connected) subscription."""
        assert self._session is not None
        if is_live:
            self.live.add(platform_ref)
        else:
            self.live.discard(platform_ref)
        await self._session.emit(
            ChannelRef(provider=self.id, platform_ref=platform_ref, handle=platform_ref),
            LiveState(is_live=is_live, source=StateSource.PUSH),
        )

    def go_live_silently(self, platform_ref: str) -> None:
        """The channel goes live, but no notification is delivered.

        This is exactly what happens during a disconnect: the platform's truth
        changes and the socket that would have told us was not there.
        """
        self.live.add(platform_ref)


def build(clock: ManualClock, bus: EventBus) -> tuple[IngestScheduler, PushSupervisor]:
    scheduler = IngestScheduler(bus, IngestSettings(jitter=0.0), clock=clock, rng=random.Random(1))
    # Short backoff so reconnect tests do not each wait a real second.
    supervisor = PushSupervisor(scheduler, clock=clock, rng=random.Random(1), base_backoff=0.01)
    return scheduler, supervisor


async def register(
    scheduler: IngestScheduler,
    supervisor: PushSupervisor,
    provider: FakePushProvider,
    handles: Sequence[str],
) -> dict[str, UUID]:
    channels: dict[UUID, ChannelRef] = {}
    by_handle: dict[str, UUID] = {}

    scheduler.register(provider)
    for handle in handles:
        channel_id = uuid4()
        ref = await provider.resolve(handle)
        scheduler.track(channel_id, ref, last=LiveState.offline())
        channels[channel_id] = ref
        by_handle[handle] = channel_id

    supervisor.watch(provider, provider.id, channels)
    return by_handle


def drain(subscription: object, limit: int = 20) -> list[object]:
    received: list[object] = []
    queue = subscription._queue  # type: ignore[attr-defined]
    while not queue.empty() and len(received) < limit:
        event = queue.get_nowait()
        if event is not None:
            received.append(event)
    return received


class TestRecoveryAfterAMissedEvent:
    """The question this whole module exists to answer."""

    async def test_a_notification_missed_while_disconnected_is_recovered(
        self, bus: EventBus, clock: ManualClock
    ) -> None:
        """The platform never resends it, so reconnecting must go and look.

        Sequence: connected, socket drops, channel goes live with nobody
        listening, socket comes back. The go-live notification is gone forever.
        Without reconciliation the node would stay dark until the next
        ten-minute tick — or indefinitely, if the subscription looked healthy.
        """
        scheduler, supervisor = build(clock, bus)
        provider = FakePushProvider()
        await register(scheduler, supervisor, provider, ["someone"])

        await supervisor.start()
        await provider.wait_connected()
        await scheduler.poll_once()  # the initial reconcile
        assert provider.poll_calls == 1

        # The socket dies, and while it is down the channel goes live.
        provider.drop()
        await asyncio.sleep(0)
        provider.go_live_silently("someone")

        # It reconnects. No notification is replayed — but a reconcile happens.
        await provider.wait_connected(timeout=2.0)

        with bus.subscribe(ChannelStateChanged) as events:
            await scheduler.poll_once()
            received = drain(events)

        assert len(received) == 1, "the missed go-live was never recovered"
        event = received[0]
        assert isinstance(event, ChannelStateChanged)
        assert event.current.is_live is True
        assert event.went_live is True

        await supervisor.stop()

    async def test_subscribing_makes_covered_channels_due_at_once(
        self, bus: EventBus, clock: ManualClock
    ) -> None:
        """Reconciliation must not wait for the ten-minute tick."""
        scheduler, supervisor = build(clock, bus)
        provider = FakePushProvider()
        await register(scheduler, supervisor, provider, ["a", "b"])

        await supervisor.start()
        await provider.wait_connected()
        await scheduler.poll_once()

        # Settle onto the reconciliation cadence, then drop and reconnect.
        clock.advance(5.0)
        provider.drop()
        await provider.wait_connected(timeout=2.0)

        assert await scheduler.poll_once() == 2, "reconnect did not force a poll"
        await supervisor.stop()


class TestDegradation:
    async def test_a_dropped_subscription_reverts_to_ordinary_polling(
        self, bus: EventBus, clock: ManualClock
    ) -> None:
        """A dead socket must degrade to slower updates, never to none."""
        scheduler, supervisor = build(clock, bus)
        provider = FakePushProvider()
        provider.always_fail = True  # the platform stays unreachable
        await register(scheduler, supervisor, provider, ["someone"])

        await supervisor.start()
        await asyncio.sleep(0.05)  # let a few attempts fail

        assert supervisor.connected_count == 0
        assert provider.watch_calls >= 1
        assert await scheduler.poll_once() == 1, "an unpushed channel must still poll"

        await supervisor.stop()

    async def test_reconnects_after_a_failure(self, bus: EventBus, clock: ManualClock) -> None:
        scheduler, supervisor = build(clock, bus)
        provider = FakePushProvider()
        provider.fail_next = True
        await register(scheduler, supervisor, provider, ["someone"])

        await supervisor.start()
        await provider.wait_connected(timeout=5.0)

        assert provider.watch_calls >= 2
        assert supervisor.stats()["fakepush"].connected is True
        await supervisor.stop()

    async def test_a_drop_is_counted_for_diagnostics(
        self, bus: EventBus, clock: ManualClock
    ) -> None:
        """V1 had no way to notice an integration had stopped working."""
        scheduler, supervisor = build(clock, bus)
        provider = FakePushProvider()
        await register(scheduler, supervisor, provider, ["someone"])

        await supervisor.start()
        await provider.wait_connected()
        provider.drop()
        await provider.wait_connected(timeout=2.0)

        stats = supervisor.stats()["fakepush"]
        assert stats.drops >= 1
        assert stats.connects >= 2
        assert stats.last_error is not None
        await supervisor.stop()


class TestPushEvents:
    async def test_a_pushed_go_live_is_announced(self, bus: EventBus, clock: ManualClock) -> None:
        scheduler, supervisor = build(clock, bus)
        provider = FakePushProvider()
        await register(scheduler, supervisor, provider, ["someone"])

        await supervisor.start()
        await provider.wait_connected()

        with bus.subscribe(ChannelStateChanged) as events:
            await provider.push("someone", True)
            received = drain(events)

        assert len(received) == 1
        event = received[0]
        assert isinstance(event, ChannelStateChanged)
        assert event.went_live is True
        assert event.current.source is StateSource.PUSH
        await supervisor.stop()

    async def test_a_repeated_push_of_the_same_state_says_nothing(
        self, bus: EventBus, clock: ManualClock
    ) -> None:
        """Transition detection lives in one place, shared with polling."""
        scheduler, supervisor = build(clock, bus)
        provider = FakePushProvider()
        await register(scheduler, supervisor, provider, ["someone"])

        await supervisor.start()
        await provider.wait_connected()
        await provider.push("someone", True)

        with bus.subscribe(ChannelStateChanged) as events:
            await provider.push("someone", True)
            assert drain(events) == []

        await supervisor.stop()

    async def test_a_push_for_an_untracked_channel_is_ignored(
        self, bus: EventBus, clock: ManualClock
    ) -> None:
        scheduler, supervisor = build(clock, bus)
        provider = FakePushProvider()
        await register(scheduler, supervisor, provider, ["someone"])

        await supervisor.start()
        await provider.wait_connected()

        with bus.subscribe(ChannelStateChanged) as events:
            await provider.push("a-stranger", True)
            assert drain(events) == []

        await supervisor.stop()

    async def test_a_pushed_channel_stops_polling_frequently(
        self, bus: EventBus, clock: ManualClock
    ) -> None:
        """The point of push: stop the blind minute-by-minute poll."""
        scheduler, supervisor = build(clock, bus)
        provider = FakePushProvider()
        await register(scheduler, supervisor, provider, ["someone"])

        await supervisor.start()
        await provider.wait_connected()
        await scheduler.poll_once()
        before = provider.poll_calls

        clock.advance(120.0)
        await scheduler.poll_once()

        assert provider.poll_calls == before, "a pushed channel should not poll every 2 minutes"
        await supervisor.stop()

    async def test_the_backstop_poll_still_happens_eventually(
        self, bus: EventBus, clock: ManualClock
    ) -> None:
        """For the failure with no signal: a socket that stays open while the
        platform has silently dropped the subscription behind it."""
        scheduler, supervisor = build(clock, bus)
        provider = FakePushProvider()
        await register(scheduler, supervisor, provider, ["someone"])

        await supervisor.start()
        await provider.wait_connected()
        await scheduler.poll_once()
        before = provider.poll_calls

        clock.advance(700.0)  # past the 600s reconcile interval
        await scheduler.poll_once()

        assert provider.poll_calls == before + 1
        await supervisor.stop()


class TestLifecycle:
    async def test_stop_cancels_and_reverts_to_polling(
        self, bus: EventBus, clock: ManualClock
    ) -> None:
        scheduler, supervisor = build(clock, bus)
        provider = FakePushProvider()
        await register(scheduler, supervisor, provider, ["someone"])

        await supervisor.start()
        await provider.wait_connected()
        await supervisor.stop()

        assert supervisor.connected_count == 0
        assert await scheduler.poll_once() == 1, "channels must poll again after stop"

    async def test_stop_is_safe_before_start(self, bus: EventBus, clock: ManualClock) -> None:
        _, supervisor = build(clock, bus)
        await supervisor.stop()

    async def test_start_is_idempotent(self, bus: EventBus, clock: ManualClock) -> None:
        scheduler, supervisor = build(clock, bus)
        provider = FakePushProvider()
        await register(scheduler, supervisor, provider, ["someone"])

        await supervisor.start()
        await provider.wait_connected()
        await supervisor.start()
        await asyncio.sleep(0.02)

        assert provider.watch_calls == 1
        await supervisor.stop()


class TestBackoff:
    def test_grows_and_is_capped(self, bus: EventBus, clock: ManualClock) -> None:
        _, supervisor = build(clock, bus)

        first = supervisor._backoff(0)
        later = supervisor._backoff(5)
        assert later > first

    def test_survives_a_very_long_outage(self, bus: EventBus, clock: ManualClock) -> None:
        """Bounded before exponentiating; 2**10000 cannot become a float."""
        _, supervisor = build(clock, bus)
        assert supervisor._backoff(10_000) < 100.0

    @pytest.mark.parametrize("failures", [0, 1, 5, 20])
    def test_is_always_positive(self, bus: EventBus, clock: ManualClock, failures: int) -> None:
        _, supervisor = build(clock, bus)
        assert supervisor._backoff(failures) > 0
