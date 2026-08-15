"""Event bus behaviour."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from livepulse.core.bus import EventBus
from livepulse.core.events import (
    ChannelStateChanged,
    DisplayModeChanged,
    Event,
    ProviderHealthChanged,
)
from livepulse.core.models import DisplayMode, LiveState, StateSource


def _state_changed(*, live: bool = True, previously: bool | None = None) -> ChannelStateChanged:
    return ChannelStateChanged(
        channel_id=uuid4(),
        current=LiveState(is_live=live, source=StateSource.PUSH),
        previous=None if previously is None else LiveState(is_live=previously),
    )


async def _take(subscription: object, count: int, timeout: float = 1.0) -> list[Event]:
    """Pull exactly ``count`` events, failing fast instead of hanging forever."""
    received: list[Event] = []

    async def pump() -> None:
        async for event in subscription:  # type: ignore[attr-defined]
            received.append(event)
            if len(received) == count:
                return

    await asyncio.wait_for(pump(), timeout=timeout)
    return received


async def test_delivers_to_matching_subscriber(bus: EventBus) -> None:
    with bus.subscribe(ChannelStateChanged) as events:
        bus.publish(_state_changed())
        assert len(await _take(events, 1)) == 1


async def test_routing_respects_subclassing(bus: EventBus) -> None:
    """Subscribing to the base type yields the firehose.

    This is what lets the web layer stream every event to the browser without
    knowing the event catalogue — new event types need no dispatcher change.
    """
    with bus.subscribe(Event) as events:
        bus.publish(_state_changed())
        bus.publish(DisplayModeChanged(mode=DisplayMode.AMBIENT))
        bus.publish(ProviderHealthChanged(provider="twitch", healthy=False))

        assert len(await _take(events, 3)) == 3


async def test_non_matching_events_are_not_delivered(bus: EventBus) -> None:
    with bus.subscribe(DisplayModeChanged) as events:
        bus.publish(_state_changed())
        bus.publish(DisplayModeChanged(mode=DisplayMode.OFF))

        received = await _take(events, 1)
        assert isinstance(received[0], DisplayModeChanged)


async def test_multiple_subscribers_each_get_a_copy(bus: EventBus) -> None:
    with bus.subscribe(Event) as first, bus.subscribe(Event) as second:
        bus.publish(DisplayModeChanged(mode=DisplayMode.OFF))

        assert len(await _take(first, 1)) == 1
        assert len(await _take(second, 1)) == 1


async def test_subscription_is_removed_on_exit(bus: EventBus) -> None:
    with bus.subscribe(Event):
        assert bus.subscriber_count == 1
    assert bus.subscriber_count == 0


async def test_slow_subscriber_does_not_block_publisher(bus: EventBus) -> None:
    """A stalled consumer must degrade itself, never the ingestion loop.

    An SSE client on dead wifi holds its queue full; publishing has to stay
    non-blocking or one bad browser tab freezes the lights.
    """
    with bus.subscribe(Event, queue_size=4) as events:
        for _ in range(100):
            bus.publish(DisplayModeChanged(mode=DisplayMode.OFF))

        assert events.dropped == 96
        assert bus.drain_report()["dropped"] == 96
        # The most recent events survive; the oldest were discarded.
        assert len(await _take(events, 4)) == 4


async def test_publish_with_no_subscribers_is_a_noop(bus: EventBus) -> None:
    bus.publish(DisplayModeChanged(mode=DisplayMode.OFF))
    assert bus.subscriber_count == 0


@pytest.mark.parametrize(
    ("live", "previously", "went_live", "went_offline"),
    [
        (True, False, True, False),
        (False, True, False, True),
        (True, True, False, False),
        (True, None, False, False),  # cold start: already live, do not flash
        (False, None, False, False),
    ],
)
def test_transition_helpers(
    live: bool, previously: bool | None, went_live: bool, went_offline: bool
) -> None:
    event = _state_changed(live=live, previously=previously)
    assert event.went_live is went_live
    assert event.went_offline is went_offline
