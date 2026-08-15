"""Built-in effects.

Every effect is a pure function of time, so these render frame 0, frame 90, and
frame 3600 instantly with no sleeping and no hardware. V1's effects could not be
tested at all: they slept inside themselves and called ``strip.show()`` directly.
"""

from __future__ import annotations

import pytest

from livepulse.leds.color import BLACK, RGB, WHITE
from livepulse.leds.effects import create_effect
from livepulse.leds.effects.base import EffectContext
from livepulse.leds.effects.builtin import (
    BlinkEffect,
    FlashEffect,
    GradientEffect,
    PulseEffect,
    RainbowEffect,
    SparkleEffect,
    WaveEffect,
)

RED = RGB(255, 0, 0)
GREEN = RGB(0, 255, 0)


def ctx(
    node_id: int = 0,
    color: RGB = RED,
    alt: RGB | None = None,
    node_count: int = 16,
    **params: object,
) -> EffectContext:
    return EffectContext(
        node_id=node_id, color=color, alt_color=alt, node_count=node_count, params=params
    )


class TestPurity:
    """The property the whole design rests on."""

    @pytest.mark.parametrize("name", ["static", "pulse", "blink", "rainbow", "sparkle", "wave"])
    def test_same_inputs_give_the_same_output(self, name: str) -> None:
        effect = create_effect(name)
        assert effect.render(ctx(), 3.7) == effect.render(ctx(), 3.7)

    @pytest.mark.parametrize("name", ["off", "static", "pulse", "blink", "rainbow", "gradient"])
    def test_never_raises_across_a_long_timeline(self, name: str) -> None:
        """A render loop running for a week must not hit an edge case."""
        effect = create_effect(name)
        for frame in range(0, 600_000, 997):
            effect.render(ctx(node_id=frame % 16), frame / 60.0)


class TestOff:
    def test_is_always_black(self) -> None:
        assert create_effect("off").render(ctx(color=WHITE), 12.3) is BLACK


class TestStatic:
    def test_returns_the_node_colour_unchanged(self) -> None:
        assert create_effect("static").render(ctx(color=RED), 99.0) == RED

    def test_does_not_move(self) -> None:
        effect = create_effect("static")
        assert effect.render(ctx(), 0.0) == effect.render(ctx(), 1000.0)


class TestPulse:
    def test_starts_at_full_brightness(self) -> None:
        """A node that has just come on should be bright, not dark."""
        assert PulseEffect(period=2.0).render(ctx(color=RED), 0.0) == RED

    def test_reaches_the_floor_at_half_period(self) -> None:
        effect = PulseEffect(period=2.0, floor=0.0)
        assert effect.render(ctx(color=WHITE), 1.0).r <= 1

    def test_is_periodic(self) -> None:
        effect = PulseEffect(period=2.0)
        assert effect.render(ctx(), 0.5) == effect.render(ctx(), 2.5)

    def test_floor_keeps_it_visible(self) -> None:
        dimmest = PulseEffect(period=2.0, floor=0.5).render(ctx(color=WHITE), 1.0)
        assert dimmest.r == pytest.approx(127, abs=2)

    def test_params_override_the_default_period(self) -> None:
        effect = PulseEffect(period=2.0, floor=0.0)
        # With period=4 the floor arrives at t=2, not t=1.
        assert effect.render(ctx(color=WHITE, period=4.0), 2.0).r <= 1

    def test_a_nonsense_param_falls_back_rather_than_crashing(self) -> None:
        """One bad row must not take the render loop down."""
        assert PulseEffect().render(ctx(color=RED, period="fast"), 0.0) == RED

    def test_a_zero_period_is_clamped(self) -> None:
        """Division by zero in the render loop would be fatal."""
        assert PulseEffect().render(ctx(period=0.0), 1.0) is not None

    def test_breathe_is_a_slower_pulse(self) -> None:
        """Registered as the same class with different defaults, not a copy."""
        assert isinstance(create_effect("breathe"), PulseEffect)


class TestBlink:
    def test_is_on_for_the_first_half(self) -> None:
        effect = BlinkEffect(period=1.0, duty=0.5)
        assert effect.render(ctx(color=RED), 0.0) == RED
        assert effect.render(ctx(color=RED), 0.6) is BLACK

    def test_duty_shifts_the_transition(self) -> None:
        effect = BlinkEffect(period=1.0, duty=0.9)
        assert effect.render(ctx(color=RED), 0.8) == RED

    def test_is_periodic(self) -> None:
        effect = BlinkEffect(period=1.0)
        assert effect.render(ctx(), 0.1) == effect.render(ctx(), 5.1)


class TestFlash:
    def test_alternates_between_accent_and_node_colour(self) -> None:
        """V1's go-live animation: green against the streamer's colour."""
        effect = FlashEffect(period=0.25)
        assert effect.render(ctx(color=RED), 0.0) == GREEN
        assert effect.render(ctx(color=RED), 0.2) == RED

    def test_alt_colour_overrides_the_accent(self) -> None:
        effect = FlashEffect(period=0.25)
        assert effect.render(ctx(color=RED, alt=WHITE), 0.0) == WHITE


class TestRainbow:
    def test_sweeps_hue_over_time(self) -> None:
        effect = RainbowEffect(period=6.0)
        assert effect.render(ctx(), 0.0) != effect.render(ctx(), 2.0)

    def test_nodes_are_offset_from_each_other(self) -> None:
        """Otherwise the whole stand is one flat colour that changes together."""
        effect = RainbowEffect()
        assert effect.render(ctx(node_id=0), 0.0) != effect.render(ctx(node_id=8), 0.0)

    def test_wraps_after_one_period(self) -> None:
        effect = RainbowEffect(period=6.0)
        assert effect.render(ctx(), 0.0) == effect.render(ctx(), 6.0)

    def test_ignores_the_node_colour(self) -> None:
        effect = RainbowEffect()
        assert effect.render(ctx(color=RED), 1.0) == effect.render(ctx(color=WHITE), 1.0)


class TestGradient:
    def test_is_still(self) -> None:
        """V1's ambient fallback did not animate, and neither does this."""
        effect = GradientEffect()
        assert effect.render(ctx(), 0.0) == effect.render(ctx(), 500.0)

    def test_hue_varies_across_the_stand(self) -> None:
        effect = GradientEffect()
        colours = {effect.render(ctx(node_id=n), 0.0) for n in range(16)}
        assert len(colours) == 16

    def test_stays_dim_by_default(self) -> None:
        """It is ambient, not the main event. V1 used value=0.2; so do we."""
        assert max(GradientEffect().render(ctx(node_id=0), 0.0)) <= 52


class TestWave:
    def test_the_crest_moves_along_the_stand(self) -> None:
        effect = WaveEffect(period=3.0, floor=0.0)
        first, later = effect.render(ctx(node_id=0), 0.0), effect.render(ctx(node_id=4), 0.0)
        assert first != later

    def test_differs_from_pulse_by_being_offset(self) -> None:
        """Pulse breathes in unison; wave does not."""
        wave, pulse = WaveEffect(period=3.0), PulseEffect(period=3.0)
        assert wave.render(ctx(node_id=4), 0.0) != pulse.render(ctx(node_id=4), 0.0)


class TestSparkle:
    def test_is_deterministic_across_processes(self) -> None:
        """Uses an integer hash, not hash(), which is salted per process.

        Without this the same frame would differ between runs and no golden
        test could cover it.
        """
        effect = SparkleEffect()
        assert effect.render(ctx(node_id=3), 1.0) == effect.render(ctx(node_id=3), 1.0)

    def test_some_nodes_are_lit_and_some_are_dim(self) -> None:
        effect = SparkleEffect(chance=0.5)
        values = {effect.render(ctx(node_id=n, color=WHITE), 1.0) for n in range(16)}
        assert len(values) == 2, "expected exactly the lit and dim variants"

    def test_zero_chance_leaves_everything_dim(self) -> None:
        effect = SparkleEffect(chance=0.0, base=0.1)
        assert all(effect.render(ctx(node_id=n, color=WHITE), 1.0) != WHITE for n in range(16))

    def test_certain_chance_lights_everything(self) -> None:
        effect = SparkleEffect(chance=1.0)
        assert all(effect.render(ctx(node_id=n, color=WHITE), 1.0) == WHITE for n in range(16))

    def test_changes_between_time_slots(self) -> None:
        effect = SparkleEffect(slot=0.1, chance=0.5)
        frames = {
            tuple(effect.render(ctx(node_id=n, color=WHITE), s * 0.1) for n in range(16))
            for s in range(20)
        }
        assert len(frames) > 1, "sparkle should not be static"


class TestGoldenFrames:
    """Whole-stand snapshots, pinned.

    These catch the change nobody meant to make — a tweaked constant or a
    reordered expression that silently alters how the stand looks.
    """

    @pytest.mark.parametrize(
        ("name", "t", "expected"),
        [
            ("static", 0.0, [RED] * 4),
            ("off", 1.0, [BLACK] * 4),
            (
                "pulse",
                0.0,
                [RED, RED, RED, RED],
            ),
            # A full hue ramp — red, yellow-green, cyan, violet — at value 0.2,
            # so the brightest channel is 51. Matches V1's ambient gradient.
            (
                "gradient",
                0.0,
                [RGB(51, 0, 0), RGB(25, 51, 0), RGB(0, 51, 51), RGB(25, 0, 51)],
            ),
        ],
    )
    def test_four_node_stand(self, name: str, t: float, expected: list[RGB]) -> None:
        effect = create_effect(name)
        actual = [effect.render(ctx(node_id=n, color=RED, node_count=4), t) for n in range(4)]
        assert actual == expected
