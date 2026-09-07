"""In-process asynchronous event bus.

Decouples producers from consumers: ingestion publishes state changes without
knowing that a renderer or a web client exists. This is the piece V1 lacked —
its web app and LED process communicated by writing a JSON file and hoping the
other side noticed within ten seconds.

Design notes
------------

*Routing is by type, and respects inheritance.* Publishing a
``ChannelStateChanged`` delivers to subscribers of ``ChannelStateChanged`` and
to subscribers of ``Event``. New event types therefore need no dispatcher
changes (open/closed).

*Publishing never blocks.* A subscriber that stops draining — a browser on a
dead wifi link holding an SSE stream — must not stall the ingestion loop. Each
subscriber has a bounded queue; when it fills, the oldest event is dropped and
counted. Slow consumers degrade themselves, not the system.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager, suppress
from typing import Generic, TypeVar

import structlog

from livepulse.core.events import Event

__all__ = ["EventBus", "Subscription"]

log = structlog.get_logger(__name__)

E = TypeVar("E", bound=Event)

DEFAULT_QUEUE_SIZE = 256


class Subscription(Generic[E]):
    """A bounded stream of events of the subscribed types.

    Async-iterable. Iteration ends when :meth:`close` is called, which the
    :meth:`EventBus.subscribe` context manager does on exit.
    """

    __slots__ = ("_closed", "_dropped", "_queue", "_types")

    def __init__(self, types: tuple[type[Event], ...], maxsize: int) -> None:
        self._types = types
        self._queue: asyncio.Queue[Event | None] = asyncio.Queue(maxsize=maxsize)
        self._dropped = 0
        self._closed = False

    @property
    def dropped(self) -> int:
        """How many events this subscriber missed by being too slow."""
        return self._dropped

    def matches(self, event: Event) -> bool:
        return isinstance(event, self._types)

    def offer(self, event: Event) -> None:
        """Enqueue without blocking, discarding the oldest event if full."""
        if self._closed:
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            with suppress(asyncio.QueueEmpty):  # pragma: no branch
                self._queue.get_nowait()
            self._dropped += 1
            self._queue.put_nowait(event)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Sentinel wakes a consumer parked on `get()` so iteration can finish.
        try:
            self._queue.put_nowait(None)
        except asyncio.QueueFull:  # pragma: no cover - drop one to make room
            self._queue.get_nowait()
            self._queue.put_nowait(None)

    async def next_event(self, timeout: float | None = None) -> E | None:
        """The next event, ``None`` once the subscription is closed.

        Unlike cancelling ``anext()`` on the iterator — which kills the
        generator and silently ends the stream — a timeout here consumes
        nothing and is safe to retry. That is what a keep-alive loop needs:
        the SSE stream waits with a timeout, emits a heartbeat, and waits
        again on the same subscription.

        Raises:
            TimeoutError: nothing arrived in time.
        """
        if timeout is None:
            event = await self._queue.get()
        else:
            event = await asyncio.wait_for(self._queue.get(), timeout)
        return event  # type: ignore[return-value]  # guarded by `matches` at publish time

    def __aiter__(self) -> AsyncIterator[E]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[E]:
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event  # type: ignore[misc]  # guarded by `matches` at publish time


class EventBus:
    """Fan-out of events to type-matched subscribers."""

    __slots__ = ("_default_queue_size", "_subscriptions")

    def __init__(self, *, queue_size: int = DEFAULT_QUEUE_SIZE) -> None:
        self._subscriptions: list[Subscription[Event]] = []
        self._default_queue_size = queue_size

    @property
    def subscriber_count(self) -> int:
        return len(self._subscriptions)

    @contextmanager
    def subscribe(
        self,
        *types: type[E],
        queue_size: int | None = None,
    ) -> Iterator[Subscription[E]]:
        """Subscribe for the lifetime of the ``with`` block.

        With no types, subscribes to everything.

        >>> with bus.subscribe(ChannelStateChanged) as events:  # doctest: +SKIP
        ...     async for event in events:
        ...         ...
        """
        wanted: tuple[type[Event], ...] = types or (Event,)
        subscription: Subscription[Event] = Subscription(
            wanted, queue_size or self._default_queue_size
        )
        self._subscriptions.append(subscription)
        try:
            yield subscription  # type: ignore[misc]
        finally:
            subscription.close()
            self._subscriptions.remove(subscription)

    def publish(self, event: Event) -> None:
        """Deliver to every matching subscriber. Never blocks, never raises."""
        for subscription in self._subscriptions:
            if subscription.matches(event):
                subscription.offer(event)

    def drain_report(self) -> dict[str, int]:
        """Per-subscriber drop counts, for the diagnostics page."""
        return {
            "subscribers": len(self._subscriptions),
            "dropped": sum(s.dropped for s in self._subscriptions),
        }
