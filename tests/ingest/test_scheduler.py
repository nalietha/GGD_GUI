"""The polling loop.

``poll_once`` performs exactly one pass, so these drive it directly with a
ManualClock — deterministic, instant, no sleeping and no network.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from uuid import UUID, uuid4

import pytest

from livepulse.config.settings import IngestSettings
from livepulse.core.bus import EventBus
from livepulse.core.clock import ManualClock
from livepulse.core.errors import ProviderAuthError, ProviderError, ProviderRateLimited
from livepulse.core.events import ChannelStateChanged, ProviderHealthChanged
from livepulse.core.models import ChannelRef, LiveState
from livepulse.ingest.scheduler import IngestScheduler
from livepulse.providers.base import BaseProvider, Capabilities, ProviderConfig
from livepulse.providers.manual import ManualProvider


class RecordingProvider(BaseProvider):
    """A provider that records its calls and can be made to fail."""

    id = "recording"
    caps = Capabilities(supports_batch=True, max_batch=3, min_poll_interval=1.0)

    def __init__(self) -> None:
        super().__init__(ProviderConfig(provider_id=self.id))
        self.batches: list[list[str]] = []
        self.live: set[str] = set()
        self.raises: Exception | None = None
        self.omit: set[str] = set()

    async def resolve(self, handle: str) -> ChannelRef:
        return ChannelRef(provider=self.id, platform_ref=handle, handle=handle)

    async def poll(self, refs: Sequence[ChannelRef]) -> dict[str, LiveState]:
        self.batches.append([ref.platform_ref for ref in refs])
        if self.raises is not None:
            raise self.raises
        found = {
            ref.platform_ref: LiveState(is_live=True)
            for ref in refs
            if ref.platform_ref in self.live
        }
        result = self.fill_offline(refs, found)
        for key in self.omit:
            result.pop(key, None)
        return result

    @property
    def calls(self) -> int:
        return len(self.batches)


def scheduler(bus: EventBus, clock: ManualClock, **overrides: object) -> IngestScheduler:
    return IngestScheduler(
        bus,
        IngestSettings(**overrides),  # type: ignore[arg-type]
        clock=clock,
        rng=random.Random(1),
    )


async def track(
    sched: IngestScheduler, provider: RecordingProvider, handle: str, **kwargs: object
) -> UUID:
    channel_id = uuid4()
    sched.track(channel_id, await provider.resolve(handle), **kwargs)  # type: ignore[arg-type]
    return channel_id


async def drain(bus_subscription: object, limit: int = 10) -> list[object]:
    """Collect whatever is already queued, without waiting for more."""
    received: list[object] = []
    queue = bus_subscription._queue  # type: ignore[attr-defined]
    while not queue.empty() and len(received) < limit:
        event = queue.get_nowait()
        if event is not None:
            received.append(event)
    return received


class TestPolling:
    async def test_polls_a_tracked_channel(self, bus: EventBus, clock: ManualClock) -> None:
        sched = scheduler(bus, clock)
        provider = RecordingProvider()
        sched.register(provider)
        await track(sched, provider, "someone")

        assert await sched.poll_once() == 1
        assert provider.batches == [["someone"]]

    async def test_a_new_channel_is_due_immediately(
        self, bus: EventBus, clock: ManualClock
    ) -> None:
        """Adding a streamer should light up without waiting a full interval."""
        sched = scheduler(bus, clock)
        provider = RecordingProvider()
        sched.register(provider)
        await track(sched, provider, "someone")

        assert await sched.poll_once() == 1

    async def test_nothing_is_polled_before_it_is_due(
        self, bus: EventBus, clock: ManualClock
    ) -> None:
        sched = scheduler(bus, clock)
        provider = RecordingProvider()
        sched.register(provider)
        await track(sched, provider, "someone")

        await sched.poll_once()
        assert await sched.poll_once() == 0, "polled again with no time elapsed"

    async def test_polls_again_once_the_interval_passes(
        self, bus: EventBus, clock: ManualClock
    ) -> None:
        sched = scheduler(bus, clock)
        provider = RecordingProvider()
        sched.register(provider)
        await track(sched, provider, "someone")

        await sched.poll_once()
        clock.advance(60.0)
        assert await sched.poll_once() == 1

    async def test_batches_into_one_request(self, bus: EventBus, clock: ManualClock) -> None:
        """Three channels, batch size three, one request — not three.

        V1 made one request per poll cycle for all streamers but never chunked,
        so past 100 it would have sent a request Twitch rejects.
        """
        sched = scheduler(bus, clock)
        provider = RecordingProvider()
        sched.register(provider)
        for name in ("a", "b", "c"):
            await track(sched, provider, name)

        assert await sched.poll_once() == 3
        assert provider.calls == 1
        assert sorted(provider.batches[0]) == ["a", "b", "c"]

    async def test_splits_beyond_the_batch_size(self, bus: EventBus, clock: ManualClock) -> None:
        sched = scheduler(bus, clock)
        provider = RecordingProvider()
        sched.register(provider)
        for name in ("a", "b", "c", "d", "e"):
            await track(sched, provider, name)

        await sched.poll_once()
        assert [len(batch) for batch in provider.batches] == [3, 2]

    async def test_an_unbatched_provider_gets_one_channel_per_request(
        self, bus: EventBus, clock: ManualClock
    ) -> None:
        sched = scheduler(bus, clock)
        provider = RecordingProvider()
        provider.caps = Capabilities(supports_batch=False, max_batch=1, min_poll_interval=1.0)
        sched.register(provider)
        for name in ("a", "b"):
            await track(sched, provider, name)

        await sched.poll_once()
        assert [len(batch) for batch in provider.batches] == [1, 1]

    async def test_providers_are_polled_concurrently(
        self, bus: EventBus, clock: ManualClock
    ) -> None:
        """A slow platform must not delay a fast one."""
        sched = scheduler(bus, clock)
        first, second = RecordingProvider(), ManualProvider()
        sched.register(first)
        sched.register(second)
        await track(sched, first, "a")
        sched.track(uuid4(), await second.resolve("b"))

        assert await sched.poll_once() == 2


class TestTransitions:
    async def test_announces_a_channel_going_live(self, bus: EventBus, clock: ManualClock) -> None:
        sched = scheduler(bus, clock)
        provider = RecordingProvider()
        sched.register(provider)
        channel_id = await track(sched, provider, "someone", last=LiveState.offline())

        provider.live.add("someone")
        with bus.subscribe(ChannelStateChanged) as events:
            await sched.poll_once()
            received = await drain(events)

        assert len(received) == 1
        event = received[0]
        assert isinstance(event, ChannelStateChanged)
        assert event.channel_id == channel_id
        assert event.went_live is True

    async def test_says_nothing_when_state_is_unchanged(
        self, bus: EventBus, clock: ManualClock
    ) -> None:
        """Sixteen offline channels every 30s must not produce sixteen events."""
        sched = scheduler(bus, clock)
        provider = RecordingProvider()
        sched.register(provider)
        await track(sched, provider, "someone", last=LiveState.offline())

        with bus.subscribe(ChannelStateChanged) as events:
            await sched.poll_once()
            assert await drain(events) == []

    async def test_a_first_observation_has_no_previous(
        self, bus: EventBus, clock: ManualClock
    ) -> None:
        """A cold start that finds someone already live must not flash.

        went_live requires a previous state, so seeding None here is what keeps
        a restart from setting the whole stand off at once.
        """
        sched = scheduler(bus, clock)
        provider = RecordingProvider()
        provider.live.add("someone")
        sched.register(provider)
        await track(sched, provider, "someone")

        with bus.subscribe(ChannelStateChanged) as events:
            await sched.poll_once()
            received = await drain(events)

        event = received[0]
        assert isinstance(event, ChannelStateChanged)
        assert event.previous is None
        assert event.went_live is False

    async def test_announces_going_offline(self, bus: EventBus, clock: ManualClock) -> None:
        sched = scheduler(bus, clock)
        provider = RecordingProvider()
        sched.register(provider)
        await track(sched, provider, "someone", last=LiveState(is_live=True))

        with bus.subscribe(ChannelStateChanged) as events:
            await sched.poll_once()
            received = await drain(events)

        event = received[0]
        assert isinstance(event, ChannelStateChanged)
        assert event.went_offline is True


class TestBackoff:
    async def test_a_long_offline_channel_is_polled_less_often(
        self, bus: EventBus, clock: ManualClock
    ) -> None:
        sched = scheduler(bus, clock, jitter=0.0)
        provider = RecordingProvider()
        sched.register(provider)
        await track(sched, provider, "someone")

        for _ in range(40):
            await sched.poll_once()
            clock.advance(30.0)

        # With no backoff this would be 40 polls; backoff should cut it well down.
        assert provider.calls < 25, f"expected backoff, got {provider.calls} polls"

    async def test_going_live_restores_the_base_rate(
        self, bus: EventBus, clock: ManualClock
    ) -> None:
        sched = scheduler(bus, clock, jitter=0.0)
        provider = RecordingProvider()
        sched.register(provider)
        await track(sched, provider, "someone")

        for _ in range(40):
            await sched.poll_once()
            clock.advance(30.0)

        provider.live.add("someone")
        clock.advance(300.0)
        await sched.poll_once()

        before = provider.calls
        clock.advance(31.0)
        await sched.poll_once()
        assert provider.calls == before + 1, "a live channel should poll at the base rate"

    async def test_a_pushed_channel_polls_only_as_a_backstop(
        self, bus: EventBus, clock: ManualClock
    ) -> None:
        sched = scheduler(bus, clock, jitter=0.0)
        provider = RecordingProvider()
        sched.register(provider)
        channel_id = await track(sched, provider, "someone")
        sched.set_pushed(channel_id, True)

        await sched.poll_once()
        clock.advance(60.0)
        assert await sched.poll_once() == 0, "a pushed channel should not poll every minute"

        clock.advance(600.0)
        assert await sched.poll_once() == 1, "but must still reconcile eventually"


class TestFailures:
    async def test_rate_limiting_waits_the_requested_time(
        self, bus: EventBus, clock: ManualClock
    ) -> None:
        """Back off by what the platform asked for, not a guess."""
        sched = scheduler(bus, clock)
        provider = RecordingProvider()
        provider.raises = ProviderRateLimited("recording", retry_after=120.0)
        sched.register(provider)
        await track(sched, provider, "someone")

        await sched.poll_once()
        before = provider.calls

        clock.advance(60.0)
        await sched.poll_once()
        assert provider.calls == before, "polled again inside the retry window"

        clock.advance(120.0)
        provider.raises = None
        await sched.poll_once()
        assert provider.calls > before

    async def test_an_auth_failure_opens_the_circuit_at_once(
        self, bus: EventBus, clock: ManualClock
    ) -> None:
        """A rejected credential is not transient."""
        sched = scheduler(bus, clock)
        provider = RecordingProvider()
        provider.raises = ProviderAuthError("recording", "bad key")
        sched.register(provider)
        await track(sched, provider, "someone")

        await sched.poll_once()
        assert sched.health()["recording"] is False

        before = provider.calls
        clock.advance(30.0)
        await sched.poll_once()
        assert provider.calls == before, "kept calling a provider with rejected credentials"

    async def test_a_broken_provider_is_probed_once_after_recovery(
        self, bus: EventBus, clock: ManualClock
    ) -> None:
        """Open forever would mean a fixed credential never takes effect."""
        sched = scheduler(bus, clock)
        provider = RecordingProvider()
        provider.raises = ProviderAuthError("recording", "bad key")
        sched.register(provider)
        await track(sched, provider, "someone")

        await sched.poll_once()
        before = provider.calls

        clock.advance(120.0)
        provider.raises = None
        await sched.poll_once()

        assert provider.calls == before + 1
        assert sched.health()["recording"] is True

    async def test_a_broken_provider_is_announced(self, bus: EventBus, clock: ManualClock) -> None:
        """V1 printed to stdout, which is invisible on a headless Pi."""
        sched = scheduler(bus, clock)
        provider = RecordingProvider()
        provider.raises = ProviderAuthError("recording", "bad key")
        sched.register(provider)
        await track(sched, provider, "someone")

        with bus.subscribe(ProviderHealthChanged) as events:
            await sched.poll_once()
            received = await drain(events)

        assert len(received) == 1
        event = received[0]
        assert isinstance(event, ProviderHealthChanged)
        assert event.healthy is False

    async def test_recovery_is_announced_once(self, bus: EventBus, clock: ManualClock) -> None:
        sched = scheduler(bus, clock)
        provider = RecordingProvider()
        provider.raises = ProviderError("recording", "boom")
        sched.register(provider)
        await track(sched, provider, "someone")

        for _ in range(6):
            await sched.poll_once()
            clock.advance(60.0)

        provider.raises = None
        clock.advance(120.0)

        with bus.subscribe(ProviderHealthChanged) as events:
            await sched.poll_once()
            received = await drain(events)

        assert [e.healthy for e in received if isinstance(e, ProviderHealthChanged)] == [True]

    async def test_one_failing_provider_does_not_stop_another(
        self, bus: EventBus, clock: ManualClock
    ) -> None:
        sched = scheduler(bus, clock)
        broken, working = RecordingProvider(), ManualProvider()
        broken.raises = ProviderError("recording", "boom")
        sched.register(broken)
        sched.register(working)
        await track(sched, broken, "a")
        sched.track(uuid4(), await working.resolve("b"))

        assert await sched.poll_once() == 1, "the working provider should still be polled"

    async def test_a_provider_omitting_a_channel_does_not_invent_state(
        self, bus: EventBus, clock: ManualClock
    ) -> None:
        """Breaking the poll contract must not become a fabricated observation."""
        sched = scheduler(bus, clock)
        provider = RecordingProvider()
        provider.omit = {"someone"}
        sched.register(provider)
        await track(sched, provider, "someone")

        with bus.subscribe(ChannelStateChanged) as events:
            await sched.poll_once()
            assert await drain(events) == []


class TestTracking:
    async def test_untrack_stops_polling(self, bus: EventBus, clock: ManualClock) -> None:
        sched = scheduler(bus, clock)
        provider = RecordingProvider()
        sched.register(provider)
        channel_id = await track(sched, provider, "someone")

        assert sched.untrack(channel_id) is True
        assert await sched.poll_once() == 0
        assert sched.tracked_count == 0

    async def test_untracking_an_unknown_channel_reports_false(
        self, bus: EventBus, clock: ManualClock
    ) -> None:
        assert scheduler(bus, clock).untrack(uuid4()) is False

    async def test_a_channel_for_an_unregistered_provider_is_ignored(
        self, bus: EventBus, clock: ManualClock
    ) -> None:
        """Better a warning than a KeyError inside the loop."""
        sched = scheduler(bus, clock)
        sched.track(uuid4(), ChannelRef(provider="nope", platform_ref="1", handle="x"))
        assert sched.tracked_count == 0

    async def test_health_reports_every_provider(self, bus: EventBus, clock: ManualClock) -> None:
        sched = scheduler(bus, clock)
        sched.register(RecordingProvider())
        sched.register(ManualProvider())
        assert sched.health() == {"recording": True, "manual": True}


class TestIdle:
    """AMBIENT idles ingestion; resuming must settle silently (§16)."""

    async def test_idle_polls_nothing(self, bus: EventBus, clock: ManualClock) -> None:
        sched = scheduler(bus, clock)
        provider = RecordingProvider()
        sched.register(provider)
        await track(sched, provider, "someone")

        assert sched.set_idle(True) is True
        assert await sched.poll_once() == 0
        assert provider.calls == 0

    async def test_idle_does_not_spin(self, bus: EventBus, clock: ManualClock) -> None:
        """Every channel is overdue while idle; the loop must still sleep."""
        sched = scheduler(bus, clock)
        provider = RecordingProvider()
        sched.register(provider)
        await track(sched, provider, "someone")
        sched.set_idle(True)

        assert sched._sleep_for() > 0

    async def test_setting_the_same_state_reports_no_change(
        self, bus: EventBus, clock: ManualClock
    ) -> None:
        sched = scheduler(bus, clock)
        assert sched.set_idle(False) is False
        sched.set_idle(True)
        assert sched.set_idle(True) is False

    async def test_resuming_polls_everything_at_once(
        self, bus: EventBus, clock: ManualClock
    ) -> None:
        sched = scheduler(bus, clock)
        provider = RecordingProvider()
        sched.register(provider)
        await track(sched, provider, "someone")
        await sched.poll_once()

        sched.set_idle(True)
        sched.set_idle(False)

        assert await sched.poll_once() == 1, "resuming should not wait out an interval"

    async def test_resuming_does_not_flash_what_changed_meanwhile(
        self, bus: EventBus, clock: ManualClock
    ) -> None:
        """Someone who went live during ambient is current state, not news."""
        sched = scheduler(bus, clock)
        provider = RecordingProvider()
        sched.register(provider)
        await track(sched, provider, "someone", last=LiveState.offline())

        sched.set_idle(True)
        provider.live.add("someone")
        sched.set_idle(False)

        with bus.subscribe(ChannelStateChanged) as events:
            await sched.poll_once()
            received = await drain(events)

        event = received[0]
        assert isinstance(event, ChannelStateChanged)
        assert event.current.is_live
        assert event.went_live is False

    async def test_pushed_updates_still_apply_while_idle(
        self, bus: EventBus, clock: ManualClock
    ) -> None:
        """Push costs no budget, so idling is about polling only."""
        sched = scheduler(bus, clock)
        provider = RecordingProvider()
        sched.register(provider)
        ref = await provider.resolve("someone")
        sched.track(uuid4(), ref)
        sched.set_idle(True)

        assert sched.apply_push(ref, LiveState(is_live=True)) is True


class TestEmptyState:
    async def test_polling_with_nothing_tracked_is_a_noop(
        self, bus: EventBus, clock: ManualClock
    ) -> None:
        assert await scheduler(bus, clock).poll_once() == 0

    @pytest.mark.parametrize("registered", [True, False])
    async def test_no_channels_means_no_requests(
        self, bus: EventBus, clock: ManualClock, registered: bool
    ) -> None:
        sched = scheduler(bus, clock)
        provider = RecordingProvider()
        if registered:
            sched.register(provider)

        assert await sched.poll_once() == 0
        assert provider.calls == 0
