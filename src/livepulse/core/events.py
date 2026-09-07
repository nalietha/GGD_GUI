"""Event definitions.

Every cross-subsystem notification is one of these types. Ingestion publishes,
the renderer and the web layer subscribe; none of them import each other.

Adding a new event means adding a class here — no dispatcher to edit, because
:class:`~livepulse.core.bus.EventBus` routes by type and honours subclassing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from livepulse.core.models import DisplayMode, LiveState

__all__ = [
    "ChannelConfigChanged",
    "ChannelStateChanged",
    "DisplayModeChanged",
    "Event",
    "NodeConfigChanged",
    "ProviderHealthChanged",
    "SettingChanged",
]


class Event(BaseModel):
    """Base for everything on the bus.

    Subscribing to ``Event`` itself yields the firehose, which is how the web
    layer feeds its server-sent-events stream without knowing the event catalogue.
    """

    model_config = ConfigDict(frozen=True)

    at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ChannelStateChanged(Event):
    """A channel went live, went offline, or changed while live.

    ``previous`` is ``None`` for the first observation after startup, which is
    what the renderer uses to decide whether a go-live animation is warranted:
    a cold start that finds someone already live should not fire the flash.
    """

    channel_id: UUID
    current: LiveState
    previous: LiveState | None = None

    @property
    def went_live(self) -> bool:
        return self.current.is_live and self.previous is not None and not self.previous.is_live

    @property
    def went_offline(self) -> bool:
        return not self.current.is_live and self.previous is not None and self.previous.is_live


class NodeConfigChanged(Event):
    """A node's colour, effect, or channel binding was edited."""

    node_id: int


class ChannelConfigChanged(Event):
    """A channel was added, edited, or removed.

    Carries the id only; consumers re-read from storage, which also covers
    deletion — the id simply no longer resolves.
    """

    channel_id: UUID


class DisplayModeChanged(Event):
    """The whole display switched between off / ambient / streamer."""

    mode: DisplayMode


class SettingChanged(Event):
    """A runtime setting was written.

    Carries the key only; consumers re-read through the settings service rather
    than trusting a value off the bus, so there is one read path (DRY) and no
    chance of two components disagreeing about current state.
    """

    key: str


class ProviderHealthChanged(Event):
    """A provider started or stopped working.

    Surfaced in the UI so a broken integration is visible instead of silently
    leaving nodes stale — V1's failure mode.
    """

    provider: str
    healthy: bool
    detail: str | None = None
