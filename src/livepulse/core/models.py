"""Domain models.

These are the vocabulary of the application: everything else — providers,
storage, renderer, web — speaks in these types. They carry no I/O and no
framework coupling, so they are safe to import from anywhere.

Deliberate departures from V1:

* ``mode`` had three incompatible meanings in V1 (``"static"`` in appsettings,
  ``"on"/"off"`` from the web handler, ``"streamer"/"gradient"`` in the LED
  loop). :class:`DisplayMode` is now the single definition.
* ``LiveState`` carries more than a boolean, so effects can react to viewer
  count or category rather than only on/off.
* A channel is identified by ``id`` (ours, stable, survives platform changes)
  and located by ``(provider, platform_ref)``. V1 had this shape already in
  ``privateId``/``publicStreamerId`` but only ever populated it for Twitch.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "AuthKind",
    "Channel",
    "ChannelRef",
    "DisplayMode",
    "HexColor",
    "LiveState",
    "Node",
    "StateSource",
]

_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

#: A CSS-style hex colour. Validated here so no other module needs to.
HexColor = Annotated[str, Field(pattern=_HEX_COLOR_RE.pattern)]

#: Twitch purple — the historical default, kept so migrated configs look unchanged.
DEFAULT_COLOR = "#9146FF"


class StateSource(StrEnum):
    """How we learned a channel's state.

    Recorded because it changes how much we trust the observation: a ``PUSH``
    event is authoritative and instant, a ``POLL`` result may be up to one
    interval stale, and ``MANUAL`` overrides both until cleared.
    """

    POLL = "poll"
    PUSH = "push"
    MANUAL = "manual"


class AuthKind(StrEnum):
    """What a provider needs before it can be used."""

    NONE = "none"
    API_KEY = "api_key"
    OAUTH_APP = "oauth_app"
    OAUTH_USER = "oauth_user"


class DisplayMode(StrEnum):
    """Top-level behaviour of the whole display."""

    OFF = "off"
    """Dark. Master fade to black; ingestion keeps running."""

    AMBIENT = "ambient"
    """Effects only, no platform polling. The 'no internet required' mode."""

    STREAMER = "streamer"
    """Nodes follow their bound channels; ambient takes over when none are live."""


class ChannelRef(BaseModel):
    """The minimum needed to ask a provider about a channel.

    Frozen and hashable so it can key caches and dedupe batches.
    """

    model_config = ConfigDict(frozen=True)

    provider: str
    """Provider id, e.g. ``"twitch"``. Lowercase, stable, matches the registry key."""

    platform_ref: str
    """The platform's own stable identifier (Twitch numeric user id, YouTube channel id)."""

    handle: str
    """Human-facing name, used for display and for URL construction."""

    @field_validator("provider")
    @classmethod
    def _normalise_provider(cls, value: str) -> str:
        return value.strip().lower()


class LiveState(BaseModel):
    """A single observation of whether a channel is broadcasting."""

    model_config = ConfigDict(frozen=True)

    is_live: bool
    title: str | None = None
    category: str | None = None
    viewers: int | None = Field(default=None, ge=0)
    started_at: datetime | None = None
    thumbnail_url: str | None = None
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: StateSource = StateSource.POLL

    @classmethod
    def offline(cls, source: StateSource = StateSource.POLL, **kwargs: Any) -> LiveState:
        """An explicit offline observation.

        Distinct from 'no observation'. V1 conflated the two, so a node whose
        streamer had never been polled kept whatever colour it happened to hold.
        """
        return cls(is_live=False, source=source, **kwargs)


class Channel(BaseModel):
    """A stream someone wants to watch, on any platform."""

    id: UUID = Field(default_factory=uuid4)
    """Ours. Stable across renames and platform migrations. V1's ``privateId``."""

    provider: str
    platform_ref: str
    handle: str
    display_name: str | None = None
    url: str | None = None

    color: HexColor = DEFAULT_COLOR
    """Shown while live."""

    alt_color: HexColor | None = None
    """Optional second colour for two-tone effects."""

    enabled: bool = True
    """False keeps the channel configured but excludes it from ingestion."""

    @field_validator("provider")
    @classmethod
    def _normalise_provider(cls, value: str) -> str:
        return value.strip().lower()

    @property
    def label(self) -> str:
        """What to show a human. Never empty."""
        return self.display_name or self.handle

    @property
    def ref(self) -> ChannelRef:
        """The provider-facing view of this channel."""
        return ChannelRef(
            provider=self.provider,
            platform_ref=self.platform_ref,
            handle=self.handle,
        )


class Node(BaseModel):
    """One physical light position on the stand.

    ``id`` is the *logical* node number the user sees (0-based, left to right).
    Translating that to pixel indices is :mod:`livepulse.leds.layout`'s job and
    nobody else's — V1 duplicated that transform across three files.
    """

    id: int = Field(ge=0)

    channel_id: UUID | None = None
    """Bound channel, if any. ``None`` means this node renders ambient only."""

    effect: str = "static"
    """Effect registry key. Validated at render time against the registry."""

    color: HexColor = DEFAULT_COLOR
    """Used when the node has no bound channel, or by effects that want a local colour."""

    params: dict[str, Any] = Field(default_factory=dict)
    """Effect-specific tuning (speed, cycles, ...). Opaque to the core."""
