"""Server-sent events.

The browser's live feed, replacing the SignalR hub V1 wired server-side and
then commented out client-side — its UI was reload-driven in practice. SSE
needs no client library: ``new EventSource("/api/v1/events")`` and the browser
handles reconnection itself.

The stream is the bus firehose. Every event type is forwarded under its class
name, so a new event reaches the browser without this module changing —
the same open/closed property the bus gives Python subscribers.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import structlog

from livepulse.core.bus import EventBus
from livepulse.core.events import Event

__all__ = ["HEARTBEAT_SECONDS", "event_stream", "format_event"]

log = structlog.get_logger(__name__)

#: A comment line is sent after this much silence, so proxies and browsers
#: keep the connection open through a quiet night.
HEARTBEAT_SECONDS = 15.0


def format_event(event: Event) -> str:
    """One event in SSE wire format, typed by class name."""
    return f"event: {type(event).__name__}\ndata: {event.model_dump_json()}\n\n"


async def event_stream(
    bus: EventBus, *, heartbeat: float = HEARTBEAT_SECONDS
) -> AsyncIterator[str]:
    """Subscribe to the bus and yield SSE frames until the client goes away.

    Slow consumers are the bus's problem, not ours: each subscription has a
    bounded queue that drops oldest-first, so a phone that wandered off wifi
    while holding this stream cannot stall ingestion.
    """
    with bus.subscribe(Event) as events:
        # An immediate comment confirms the connection before anything happens.
        yield ": connected\n\n"

        while True:
            try:
                event = await events.next_event(timeout=heartbeat)
            except TimeoutError:
                yield ": keep-alive\n\n"
                continue

            if event is None:  # the subscription was closed under us
                return
            yield format_event(event)
