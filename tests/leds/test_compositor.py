"""Frame composition: layers, transients, master fade, and output."""

from __future__ import annotations

import pytest

from livepulse.config.settings import LayoutSettings
from livepulse.core.errors import ConfigError
from livepulse.core.models import DisplayMode
from livepulse.leds.color import BLACK, RGB, WHITE, IdentityGamma
from livepulse.leds.compositor import (
    MASTER_FADE_SECONDS,
    Compositor,
    Transient,
)
from livepulse.leds.effects.builtin import StaticEffect
from livepulse.leds.layout import Layout
from livepulse.leds.state import NodeView, RenderState

RED = RGB(255, 0, 0)


def layout(nodes: int = 4, leds_per_node: int = 2) -> Layout:
    return Layout(LayoutSettings(nodes=nodes, leds_per_node=leds_per_node, order="linear", rows=1))


def view(
    node_id: int, effect: str = "static", color: RGB = RED, alt: RGB | None = None
) -> NodeView:
    return NodeView(
        node_id=node_id,
        effect=effect,
        color=color,
        alt_color=alt,
        params={},
        is_bound=True,
        is_live=True,
    )


def state(
    *views: NodeView,
    mode: DisplayMode = DisplayMode.STREAMER,
    brightness: int = 255,
) -> RenderState:
    return RenderState(nodes=views, mode=mode, brightness=brightness, ambient_active=False)


def compositor(nodes: int = 4, leds_per_node: int = 2) -> Compositor:
    return Compositor(layout(nodes, leds_per_node), IdentityGamma())


def settled(comp: Compositor, snapshot: RenderState, t: float = 0.0) -> list[RGB]:
    """Render past the start-up fade so the master level is at full.

    The compositor deliberately starts dark and ramps up, so a service restart
    does not slam the strip to full brightness.
    """
    comp.render(snapshot, t)
    return list(comp.render(snapshot, t + MASTER_FADE_SECONDS * 2))


class TestBaseLayer:
    def test_each_node_paints_its_own_pixels(self) -> None:
        comp = compositor(nodes=2, leds_per_node=3)
        frame = settled(comp, state(view(0, color=RED), view(1, color=WHITE)))

        assert frame == [RED, RED, RED, WHITE, WHITE, WHITE]

    def test_the_layout_decides_which_pixels(self) -> None:
        """Serpentine wiring must be honoured — node 0 is not pixels 0..6."""
        comp = Compositor(Layout(LayoutSettings(nodes=16, leds_per_node=7)), IdentityGamma())
        frame = settled(comp, state(*(view(n, color=RED if n == 0 else BLACK) for n in range(16))))

        # Node 0 sits at physical position 15 on this stand.
        assert frame[105:112] == [RED] * 7
        assert frame[0:7] == [BLACK] * 7

    def test_the_off_effect_darkens_a_node(self) -> None:
        comp = compositor(nodes=2, leds_per_node=1)
        frame = settled(comp, state(view(0, effect="off"), view(1, color=WHITE)))
        assert frame == [BLACK, WHITE]

    def test_an_unknown_effect_darkens_only_that_node(self) -> None:
        """A hand-edited row must not stop the whole display."""
        comp = compositor(nodes=2, leds_per_node=1)
        frame = settled(comp, state(view(0, effect="nonsense"), view(1, color=WHITE)))

        assert frame == [BLACK, WHITE]
        assert comp.stats.unknown_effects["nonsense"] > 0

    def test_an_effect_that_raises_darkens_only_that_node(self) -> None:
        """A third-party effect must not be able to kill the render thread."""

        class Exploding:
            def render(self, ctx: object, t: float) -> RGB:
                raise RuntimeError("boom")

        comp = compositor(nodes=2, leds_per_node=1)
        comp._effects["boom"] = Exploding()  # type: ignore[assignment]

        frame = settled(comp, state(view(0, effect="boom"), view(1, color=WHITE)))
        assert frame == [BLACK, WHITE]

    def test_the_buffer_is_reused_across_frames(self) -> None:
        """6720 list allocations a second at 60 fps would be pure waste."""
        comp = compositor()
        snapshot = state(*(view(n) for n in range(4)))
        assert comp.render(snapshot, 0.0) is comp.render(snapshot, 0.1)


class TestOutputStage:
    def test_brightness_scales_the_frame(self) -> None:
        comp = compositor(nodes=1, leds_per_node=1)
        frame = settled(comp, state(view(0, color=WHITE), brightness=128))
        assert frame[0].r == pytest.approx(128, abs=2)

    def test_gamma_is_applied(self) -> None:
        from livepulse.leds.color import GammaTable

        comp = Compositor(layout(1, 1), GammaTable(2.8))
        frame = settled(comp, state(view(0, color=RGB(128, 128, 128))))
        assert frame[0].r < 128, "mid-tones should be pulled down"

    def test_zero_brightness_is_black(self) -> None:
        comp = compositor(nodes=1, leds_per_node=1)
        assert settled(comp, state(view(0, color=WHITE), brightness=0)) == [BLACK]


class TestMasterFade:
    def test_starts_dark_and_ramps_up(self) -> None:
        """A service restart must not slam the strip to full brightness."""
        comp = compositor(nodes=1, leds_per_node=1)
        snapshot = state(view(0, color=WHITE))

        first = list(comp.render(snapshot, 0.0))
        assert first == [BLACK]

        halfway = list(comp.render(snapshot, MASTER_FADE_SECONDS / 2))
        assert BLACK < halfway[0] < WHITE

    def test_reaches_full_after_the_fade_window(self) -> None:
        comp = compositor(nodes=1, leds_per_node=1)
        assert settled(comp, state(view(0, color=WHITE))) == [WHITE]

    def test_off_mode_fades_down_rather_than_cutting(self) -> None:
        """Cutting to black reads as a crash; fading reads as deliberate."""
        comp = compositor(nodes=1, leds_per_node=1)
        lit = state(view(0, color=WHITE))
        settled(comp, lit)

        dark = state(view(0, color=WHITE), mode=DisplayMode.OFF)
        partway = list(comp.render(dark, MASTER_FADE_SECONDS * 2 + MASTER_FADE_SECONDS / 2))
        assert BLACK < partway[0] < WHITE

    def test_off_mode_eventually_reaches_black(self) -> None:
        comp = compositor(nodes=1, leds_per_node=1)
        settled(comp, state(view(0, color=WHITE)))

        dark = state(view(0, color=WHITE), mode=DisplayMode.OFF)
        comp.render(dark, 10.0)
        assert list(comp.render(dark, 20.0)) == [BLACK]

    def test_the_fade_is_framerate_independent(self) -> None:
        """Same wall-clock duration at 30 fps as at 60."""
        slow, fast = compositor(1, 1), compositor(1, 1)
        snapshot = state(view(0, color=WHITE))

        slow.render(snapshot, 0.0)
        for frame in range(1, 11):
            slow.render(snapshot, frame * (MASTER_FADE_SECONDS / 10))

        fast.render(snapshot, 0.0)
        for frame in range(1, 41):
            fast.render(snapshot, frame * (MASTER_FADE_SECONDS / 40))

        assert slow.render(snapshot, 1.0) == fast.render(snapshot, 1.0)


class TestTransients:
    def test_overlays_the_base_layer(self) -> None:
        comp = compositor(nodes=2, leds_per_node=1)
        snapshot = state(view(0, color=RED), view(1, color=RED))
        settled(comp, snapshot)

        comp.add_transient({0}, "flash", now=1.0, duration=1.0)
        frame = list(comp.render(snapshot, 1.0))

        assert frame[0] != RED, "node 0 is flashing"
        assert frame[1] == RED, "node 1 is untouched"

    def test_expires_and_restores_the_base_layer(self) -> None:
        comp = compositor(nodes=1, leds_per_node=1)
        snapshot = state(view(0, color=RED))
        settled(comp, snapshot)

        comp.add_transient({0}, "flash", now=1.0, duration=1.0)
        comp.render(snapshot, 5.0)

        assert comp.active_transients == 0
        assert list(comp.render(snapshot, 5.1)) == [RED]

    def test_fades_out_near_the_end(self) -> None:
        """It should settle into the standing effect, not snap."""
        transient = Transient(frozenset({0}), StaticEffect(), started_at=0.0, duration=4.0)

        assert transient.alpha(0.0) == 1.0
        assert transient.alpha(2.0) == 1.0
        assert 0.0 < transient.alpha(3.5) < 1.0
        assert transient.alpha(4.0) == 0.0

    def test_re_flashing_replaces_rather_than_stacks(self) -> None:
        """A channel flapping offline/online should re-flash, not accumulate."""
        comp = compositor(nodes=1, leds_per_node=1)
        comp.add_transient({0}, "flash", now=1.0)
        comp.add_transient({0}, "flash", now=2.0)
        assert comp.active_transients == 1

    def test_transients_on_different_nodes_coexist(self) -> None:
        comp = compositor(nodes=4, leds_per_node=1)
        comp.add_transient({0}, "flash", now=1.0)
        comp.add_transient({1}, "flash", now=1.0)
        assert comp.active_transients == 2

    def test_sixteen_simultaneous_transients_are_cheap(self) -> None:
        """The V1 failure this design exists to prevent.

        V1's flash_node blocked ~3s per node inside the poll loop, so sixteen
        streamers going live together froze ingestion for the better part of a
        minute. Here it is sixteen blends in one frame.
        """
        comp = compositor(nodes=16, leds_per_node=7)
        snapshot = state(*(view(n) for n in range(16)))
        settled(comp, snapshot)

        for node_id in range(16):
            comp.add_transient({node_id}, "flash", now=1.0)

        assert comp.active_transients == 16
        assert len(comp.render(snapshot, 1.0)) == 112

    def test_an_unknown_transient_effect_is_refused_up_front(self) -> None:
        """Better to fail at the call site than silently draw nothing."""
        comp = compositor()
        with pytest.raises(ConfigError, match="unknown effect"):
            comp.add_transient({0}, "nonsense", now=0.0)

    def test_an_empty_node_set_is_ignored(self) -> None:
        comp = compositor()
        comp.add_transient(set(), "flash", now=0.0)
        assert comp.active_transients == 0

    def test_clear_removes_everything(self) -> None:
        comp = compositor()
        comp.add_transient({0}, "flash", now=0.0)
        comp.clear_transients()
        assert comp.active_transients == 0


class TestStats:
    def test_counts_frames(self) -> None:
        comp = compositor()
        snapshot = state(*(view(n) for n in range(4)))
        for frame in range(5):
            comp.render(snapshot, frame * 0.1)
        assert comp.stats.frames == 5
