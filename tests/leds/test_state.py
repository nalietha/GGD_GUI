"""Render-state resolution.

The single place that decides what a node shows. V1 spread this across
``StreamerModeStrategy``, ``DisplayEffectStrategy``, ``resolve_node_color``,
and a Razor preview grid — and the four disagreed, which is why the browser
preview and the physical stand routinely showed different things.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest

from livepulse.core.models import Channel, DisplayMode, LiveState, Node
from livepulse.leds.color import RGB
from livepulse.leds.state import build_render_state

CHANNEL_COLOR = "#e100ff"
NODE_COLOR = "#112233"


@dataclass
class FakeSettings:
    """Satisfies the DisplaySettings protocol without importing storage."""

    brightness: int = 64
    mode: DisplayMode = DisplayMode.STREAMER
    ambient_effect: str = "gradient"
    ambient_enabled: bool = True


def channel(**overrides: object) -> Channel:
    defaults: dict[str, object] = {
        "provider": "twitch",
        "platform_ref": "1",
        "handle": "someone",
        "color": CHANNEL_COLOR,
    }
    defaults.update(overrides)
    return Channel(**defaults)  # type: ignore[arg-type]


def build(
    nodes: list[Node],
    channels: list[Channel] | None = None,
    live: dict[UUID, bool] | None = None,
    settings: FakeSettings | None = None,
):
    by_id = {c.id: c for c in (channels or [])}
    states = {cid: LiveState(is_live=value) for cid, value in (live or {}).items()}
    return build_render_state(nodes, by_id, states, settings or FakeSettings())


class TestStreamerMode:
    def test_a_live_node_uses_the_channel_colour(self) -> None:
        """The channel's colour wins; the node's is the unbound fallback."""
        ch = channel()
        state = build(
            [Node(id=0, channel_id=ch.id, effect="pulse", color=NODE_COLOR)],
            [ch],
            {ch.id: True},
        )
        assert state.nodes[0].color == RGB(0xE1, 0x00, 0xFF)
        assert state.nodes[0].effect == "pulse"

    def test_an_offline_node_goes_dark_when_someone_else_is_live(self) -> None:
        live_ch, dark_ch = channel(platform_ref="1"), channel(platform_ref="2")
        state = build(
            [
                Node(id=0, channel_id=live_ch.id, effect="static"),
                Node(id=1, channel_id=dark_ch.id, effect="static"),
            ],
            [live_ch, dark_ch],
            {live_ch.id: True, dark_ch.id: False},
        )
        assert state.nodes[0].effect == "static"
        assert state.nodes[1].effect == "off"

    def test_an_unbound_node_is_dark_when_someone_is_live(self) -> None:
        """V1 skipped unbound nodes entirely, so they held their last colour."""
        ch = channel()
        state = build(
            [Node(id=0, channel_id=ch.id), Node(id=1)],
            [ch],
            {ch.id: True},
        )
        assert state.nodes[1].effect == "off"

    def test_a_never_observed_channel_counts_as_offline(self) -> None:
        ch = channel()
        state = build([Node(id=0, channel_id=ch.id)], [ch], live={})
        assert state.nodes[0].is_live is False

    def test_the_alt_colour_reaches_the_effect(self) -> None:
        ch = channel(alt_color="#00ff00")
        state = build([Node(id=0, channel_id=ch.id)], [ch], {ch.id: True})
        assert state.nodes[0].alt_color == RGB(0, 255, 0)

    def test_no_alt_colour_stays_none(self) -> None:
        ch = channel()
        state = build([Node(id=0, channel_id=ch.id)], [ch], {ch.id: True})
        assert state.nodes[0].alt_color is None

    def test_node_params_are_carried_through(self) -> None:
        ch = channel()
        state = build([Node(id=0, channel_id=ch.id, params={"period": 4.0})], [ch], {ch.id: True})
        assert state.nodes[0].params == {"period": 4.0}


class TestAmbientFallback:
    def test_ambient_takes_the_stand_when_nobody_is_live(self) -> None:
        """V1's good idea, kept: an idle stand shows a gradient, not darkness."""
        ch = channel()
        state = build([Node(id=0, channel_id=ch.id)], [ch], {ch.id: False})

        assert state.ambient_active is True
        assert state.nodes[0].effect == "gradient"

    def test_ambient_applies_with_no_channels_at_all(self) -> None:
        state = build([Node(id=0), Node(id=1)])
        assert all(node.effect == "gradient" for node in state.nodes)

    def test_a_single_live_channel_ends_ambient(self) -> None:
        live_ch, dark_ch = channel(platform_ref="1"), channel(platform_ref="2")
        state = build(
            [Node(id=0, channel_id=live_ch.id), Node(id=1, channel_id=dark_ch.id)],
            [live_ch, dark_ch],
            {live_ch.id: True, dark_ch.id: False},
        )
        assert state.ambient_active is False

    def test_a_live_channel_nobody_placed_does_not_end_ambient(self) -> None:
        """Only bound channels matter — an unplaced one is not on the stand."""
        unplaced = channel()
        state = build([Node(id=0)], [unplaced], {unplaced.id: True})
        assert state.ambient_active is True

    def test_disabling_ambient_gives_darkness_instead(self) -> None:
        state = build([Node(id=0)], settings=FakeSettings(ambient_enabled=False))
        assert state.nodes[0].effect == "off"
        assert state.ambient_active is False

    def test_ambient_mode_ignores_live_status_entirely(self) -> None:
        """The 'no internet required' mode: effects only, nothing polled."""
        ch = channel()
        state = build(
            [Node(id=0, channel_id=ch.id)],
            [ch],
            {ch.id: True},
            FakeSettings(mode=DisplayMode.AMBIENT),
        )
        assert state.nodes[0].effect == "gradient"

    def test_the_ambient_effect_is_configurable(self) -> None:
        state = build([Node(id=0)], settings=FakeSettings(ambient_effect="rainbow"))
        assert state.nodes[0].effect == "rainbow"


class TestOffMode:
    def test_mode_is_carried_for_the_compositor_to_fade(self) -> None:
        """Off is a fade, not a resolution rule — cutting to black looks broken."""
        state = build([Node(id=0)], settings=FakeSettings(mode=DisplayMode.OFF))
        assert state.mode is DisplayMode.OFF


class TestMetadata:
    def test_brightness_is_carried_through(self) -> None:
        assert build([Node(id=0)], settings=FakeSettings(brightness=200)).brightness == 200

    def test_binding_and_live_flags_are_exposed_for_the_ui(self) -> None:
        ch = channel()
        state = build([Node(id=0, channel_id=ch.id), Node(id=1)], [ch], {ch.id: True})

        assert (state.nodes[0].is_bound, state.nodes[0].is_live) == (True, True)
        assert (state.nodes[1].is_bound, state.nodes[1].is_live) == (False, False)

    def test_any_live_summarises_the_stand(self) -> None:
        ch = channel()
        assert build([Node(id=0, channel_id=ch.id)], [ch], {ch.id: True}).any_live is True
        assert build([Node(id=0)]).any_live is False

    def test_a_binding_to_a_missing_channel_is_treated_as_unbound(self) -> None:
        """Guards against a stale snapshot taken mid-delete."""
        state = build([Node(id=0, channel_id=uuid4())])
        assert state.nodes[0].is_bound is False

    def test_the_snapshot_is_immutable(self) -> None:
        """The render thread holds it while the async side builds the next."""
        state = build([Node(id=0)])
        with pytest.raises(AttributeError):
            state.brightness = 1  # type: ignore[misc]
