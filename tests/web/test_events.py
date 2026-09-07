"""Server-sent events: the browser's live feed.

Driven at the generator level, where timing is controllable — the HTTP
plumbing above it is one StreamingResponse and not worth a socket.
"""

from __future__ import annotations

import asyncio
import json

from livepulse.core.bus import EventBus
from livepulse.core.events import DisplayModeChanged, SettingChanged
from livepulse.core.models import DisplayMode
from livepulse.web.events import event_stream, format_event


async def test_forwards_events_typed_by_class_name() -> None:
    bus = EventBus()
    stream = event_stream(bus, heartbeat=60.0)

    opening = await anext(stream)
    assert opening.startswith(": connected")

    bus.publish(DisplayModeChanged(mode=DisplayMode.OFF))
    frame = await asyncio.wait_for(anext(stream), timeout=1.0)

    assert frame.startswith("event: DisplayModeChanged\n")
    payload = json.loads(frame.split("data: ", 1)[1])
    assert payload["mode"] == "off"

    await stream.aclose()


async def test_quiet_stream_heartbeats_instead_of_dying() -> None:
    bus = EventBus()
    stream = event_stream(bus, heartbeat=0.05)

    await anext(stream)  # the connected comment
    frame = await asyncio.wait_for(anext(stream), timeout=1.0)

    assert frame.startswith(": keep-alive")

    # And a real event still gets through after a heartbeat.
    bus.publish(SettingChanged(key="brightness"))

    async def until_event() -> str:
        while True:
            candidate = await anext(stream)
            if not candidate.startswith(":"):
                return candidate

    frame = await asyncio.wait_for(until_event(), timeout=1.0)
    assert frame.startswith("event: SettingChanged\n")

    await stream.aclose()


async def test_unsubscribes_when_the_client_goes_away() -> None:
    bus = EventBus()
    stream = event_stream(bus, heartbeat=60.0)

    await anext(stream)
    assert bus.subscriber_count == 1

    await stream.aclose()
    assert bus.subscriber_count == 0


def test_format_is_valid_sse() -> None:
    frame = format_event(SettingChanged(key="mode"))

    lines = frame.splitlines()
    assert lines[0] == "event: SettingChanged"
    assert lines[1].startswith("data: ")
    assert frame.endswith("\n\n")
    assert json.loads(lines[1][len("data: ") :])["key"] == "mode"
