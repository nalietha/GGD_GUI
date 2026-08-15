"""What the strip should be showing, resolved into a snapshot.

The render thread must never touch the database, the event bus, or a lock held
by the async side. It reads one immutable :class:`RenderState` per frame, built
here from domain objects by the async side and swapped in atomically.

This module is also the single place that answers *"what colour is this node
right now?"* — resolving mode, ambient fallback, live status, and the
channel-versus-node colour precedence. V1 spread that decision across
``StreamerModeStrategy.update``, ``DisplayEffectStrategy.update``,
``resolve_node_color``, and a Razor page that re-implemented it for the preview
grid, and the four disagreed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from livepulse.core.models import Channel, DisplayMode, LiveState, Node
from livepulse.leds.color import RGB, parse_hex

__all__ = ["DisplaySettings", "NodeView", "RenderState", "build_render_state"]

#: What an unbound or dark node renders. Named so the intent is legible.
OFF_EFFECT = "off"


class DisplaySettings(Protocol):
    """The runtime settings rendering depends on.

    Declared here, in the layer that *uses* it, rather than imported from
    storage. That keeps ``leds`` free of any dependency on persistence — the
    concrete ``RuntimeSettings`` simply happens to satisfy it.
    """

    @property
    def brightness(self) -> int: ...

    @property
    def mode(self) -> DisplayMode: ...

    @property
    def ambient_effect(self) -> str: ...

    @property
    def ambient_enabled(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class NodeView:
    """One node, fully resolved for rendering.

    ``effect`` and ``color`` are what to draw — every precedence rule has
    already been applied. The compositor does not re-decide anything.
    """

    node_id: int
    effect: str
    color: RGB
    alt_color: RGB | None
    params: Mapping[str, Any]

    is_bound: bool
    """Whether a channel is attached. Retained for the UI, not used in drawing."""

    is_live: bool
    """Whether that channel is broadcasting. Also for the UI."""


@dataclass(frozen=True, slots=True)
class RenderState:
    """A complete description of one frame's inputs.

    Immutable, so the render thread can hold a reference while the async side
    builds the next one. Swapping a reference is atomic in CPython, which is
    the whole synchronisation story.
    """

    nodes: tuple[NodeView, ...]
    mode: DisplayMode
    brightness: int
    ambient_active: bool
    """True when the ambient effect has taken the whole stand."""

    @property
    def any_live(self) -> bool:
        return any(node.is_live for node in self.nodes)


def build_render_state(
    nodes: Sequence[Node],
    channels: Mapping[UUID, Channel],
    live_states: Mapping[UUID, LiveState],
    settings: DisplaySettings,
) -> RenderState:
    """Resolve domain state into a render snapshot.

    The rules, in order:

    1. **Off** — the master fade handles it, so nodes still resolve normally
       and the compositor scales the result to black. Fading beats cutting.
    2. **Ambient takes the stand** when the mode is ``AMBIENT``, or when the
       mode is ``STREAMER`` and nothing bound is live. This is V1's good idea:
       an idle stand shows a gradient rather than going dark.
    3. **Otherwise**, a node bound to a live channel draws its own effect in
       that channel's colour; everything else is off.

    A node bound to a channel that has never been observed counts as offline,
    not as unknown — but the two remain distinguishable upstream, so a cold
    start does not fire go-live animations for channels that were already live.
    """
    ambient_active = settings.mode is DisplayMode.AMBIENT or (
        settings.mode is DisplayMode.STREAMER and not _any_live(nodes, live_states)
    )

    views = tuple(
        _resolve_node(node, channels, live_states, settings, ambient_active=ambient_active)
        for node in nodes
    )

    return RenderState(
        nodes=views,
        mode=settings.mode,
        brightness=settings.brightness,
        ambient_active=ambient_active and settings.ambient_enabled,
    )


def _any_live(nodes: Sequence[Node], live_states: Mapping[UUID, LiveState]) -> bool:
    """Whether any *bound* node's channel is live.

    Deliberately scoped to bound nodes: a live channel nobody has placed on the
    stand should not suppress the ambient effect.
    """
    return any(
        node.channel_id is not None and _is_live(live_states.get(node.channel_id)) for node in nodes
    )


def _is_live(state: LiveState | None) -> bool:
    return state is not None and state.is_live


def _resolve_node(
    node: Node,
    channels: Mapping[UUID, Channel],
    live_states: Mapping[UUID, LiveState],
    settings: DisplaySettings,
    *,
    ambient_active: bool,
) -> NodeView:
    channel = channels.get(node.channel_id) if node.channel_id else None
    live = _is_live(live_states.get(node.channel_id)) if node.channel_id else False

    if ambient_active:
        effect = settings.ambient_effect if settings.ambient_enabled else OFF_EFFECT
        color = _safe_color(node.color)
        alt = None
    elif live and channel is not None:
        # The channel's colour wins over the node's: the node's is a fallback
        # for when it is unbound, and V1 kept both for exactly this reason.
        effect = node.effect
        color = _safe_color(channel.color)
        alt = _safe_color(channel.alt_color) if channel.alt_color else None
    else:
        effect = OFF_EFFECT
        color = _safe_color(node.color)
        alt = None

    return NodeView(
        node_id=node.id,
        effect=effect,
        color=color,
        alt_color=alt,
        params=node.params,
        is_bound=channel is not None,
        is_live=live,
    )


def _safe_color(value: str) -> RGB:
    """Parse a stored colour, never raising into the render path.

    Colours are validated on the way into the database, so a failure here means
    something bypassed that — a hand-edited row. Going black for one node beats
    stopping the whole display, which is what V1 did when ``hex_to_color("")``
    raised inside its poll loop.
    """
    try:
        return parse_hex(value)
    except Exception:  # pragma: no cover - defensive; validation happens upstream
        from livepulse.leds.color import BLACK

        return BLACK
