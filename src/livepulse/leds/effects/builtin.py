"""The effects that ship with LivePulse.

Each is a small, stateless class registered at import. All animation comes from
``t``; all per-node variation comes from :class:`~livepulse.leds.effects.base.EffectContext`.

Timings match V1's where V1 had an equivalent, so a migrated stand looks the
same — but computed rather than slept through.
"""

from __future__ import annotations

import math

from livepulse.leds.color import BLACK, RGB, from_hsv
from livepulse.leds.effects.base import EffectContext, register_effect

__all__ = [
    "BlinkEffect",
    "FlashEffect",
    "GradientEffect",
    "OffEffect",
    "PulseEffect",
    "RainbowEffect",
    "SparkleEffect",
    "StaticEffect",
    "WaveEffect",
]

#: Deterministic pseudo-randomness. Sparkle must look random but render
#: identically for the same inputs, or it could not be tested with a golden frame.
_SPARKLE_SALT = 0x9E3779B9


class OffEffect:
    """Dark. Distinct from a node that is merely bound to an offline channel."""

    __slots__ = ()

    def render(self, ctx: EffectContext, t: float) -> RGB:
        return BLACK


class StaticEffect:
    """The node's colour, unchanging. The default, and the cheapest."""

    __slots__ = ()

    def render(self, ctx: EffectContext, t: float) -> RGB:
        return ctx.color


class PulseEffect:
    """Smooth sinusoidal breathing between a floor and full brightness.

    A cosine rather than a triangle wave: it lingers at the extremes, which
    reads as breathing instead of a ramp. V1 stepped brightness in units of 25
    with a sleep between each, which both stuttered and blocked.
    """

    __slots__ = ("_floor", "_period")

    def __init__(self, period: float = 2.0, floor: float = 0.15) -> None:
        self._period = period
        self._floor = floor

    def render(self, ctx: EffectContext, t: float) -> RGB:
        period = max(0.05, ctx.number("period", self._period))
        floor = min(max(ctx.number("floor", self._floor), 0.0), 1.0)

        # cos maps to 1.0 at t=0, so a node starts bright rather than dark.
        wave = (math.cos(2.0 * math.pi * t / period) + 1.0) / 2.0
        return ctx.color.scaled(floor + (1.0 - floor) * wave)


class BlinkEffect:
    """Hard on/off square wave. Deliberately abrupt, for attention."""

    __slots__ = ("_duty", "_period")

    def __init__(self, period: float = 1.0, duty: float = 0.5) -> None:
        self._period = period
        self._duty = duty

    def render(self, ctx: EffectContext, t: float) -> RGB:
        period = max(0.05, ctx.number("period", self._period))
        duty = min(max(ctx.number("duty", self._duty), 0.0), 1.0)
        return ctx.color if (t % period) / period < duty else BLACK


class FlashEffect:
    """Alternates between the node's colour and an accent.

    This is V1's go-live animation, which alternated green and the streamer's
    colour twelve times at 0.125 s. Used as a transient overlay rather than a
    node's standing effect: the compositor runs it for a fixed duration and
    then it is gone.
    """

    __slots__ = ("_accent", "_period")

    def __init__(self, period: float = 0.25, accent: RGB = RGB(0, 255, 0)) -> None:
        self._period = period
        self._accent = accent

    def render(self, ctx: EffectContext, t: float) -> RGB:
        period = max(0.05, ctx.number("period", self._period))
        accent = ctx.alt_color or self._accent
        return accent if (t % period) < period / 2.0 else ctx.color


class RainbowEffect:
    """Sweeps hue over time, offset per node so the stand ripples."""

    __slots__ = ("_period", "_spread")

    def __init__(self, period: float = 6.0, spread: float = 1.0) -> None:
        self._period = period
        self._spread = spread

    def render(self, ctx: EffectContext, t: float) -> RGB:
        period = max(0.1, ctx.number("period", self._period))
        spread = ctx.number("spread", self._spread)
        return from_hsv(t / period + ctx.phase * spread)


class GradientEffect:
    """A still hue ramp across the stand.

    V1's ambient fallback for when nobody is live — the one part of its
    non-streamer path that actually worked. Kept at the same low value so a
    migrated stand looks unchanged.
    """

    __slots__ = ("_value",)

    def __init__(self, value: float = 0.2) -> None:
        self._value = value

    def render(self, ctx: EffectContext, t: float) -> RGB:
        return from_hsv(ctx.phase, 1.0, min(max(ctx.number("value", self._value), 0.0), 1.0))


class WaveEffect:
    """A brightness crest travelling along the stand.

    Like :class:`PulseEffect`, but each node's phase is offset by its position,
    so the peak moves rather than every node breathing in unison.
    """

    __slots__ = ("_floor", "_period")

    def __init__(self, period: float = 3.0, floor: float = 0.1) -> None:
        self._period = period
        self._floor = floor

    def render(self, ctx: EffectContext, t: float) -> RGB:
        period = max(0.05, ctx.number("period", self._period))
        floor = min(max(ctx.number("floor", self._floor), 0.0), 1.0)

        wave = (math.cos(2.0 * math.pi * (t / period - ctx.phase)) + 1.0) / 2.0
        return ctx.color.scaled(floor + (1.0 - floor) * wave)


class SparkleEffect:
    """Occasional bright twinkles over a dim base.

    The randomness is *deterministic*: which node twinkles in a given time slot
    comes from hashing the node id and slot index, so the same inputs always
    produce the same frame. That is what lets a golden-frame test cover it —
    a ``random.choice`` could not be asserted on.
    """

    __slots__ = ("_base", "_chance", "_slot")

    def __init__(self, slot: float = 0.12, chance: float = 0.18, base: float = 0.12) -> None:
        self._slot = slot
        self._chance = chance
        self._base = base

    def render(self, ctx: EffectContext, t: float) -> RGB:
        slot = max(0.02, ctx.number("slot", self._slot))
        chance = min(max(ctx.number("chance", self._chance), 0.0), 1.0)
        base = min(max(ctx.number("base", self._base), 0.0), 1.0)

        index = int(t / slot)
        # Cheap integer hash; hash() itself is salted per process and would
        # make the same frame differ between runs.
        mixed = (index * _SPARKLE_SALT) ^ ((ctx.node_id + 1) * 0x85EBCA6B)
        roll = ((mixed >> 13) & 0xFFFF) / 0xFFFF

        return ctx.color if roll < chance else ctx.color.scaled(base)


def register_builtin_effects() -> None:
    """Register everything above.

    Called at package import. Idempotent, because registration is a dict write.
    """
    register_effect("off", OffEffect(), "Dark")
    register_effect("static", StaticEffect(), "Solid colour")
    register_effect("pulse", PulseEffect(), "Smooth breathing")
    register_effect("breathe", PulseEffect(period=5.0, floor=0.25), "Slow, gentle breathing")
    register_effect("blink", BlinkEffect(), "Hard on/off")
    register_effect("flash", FlashEffect(), "Alternates with an accent colour")
    register_effect("rainbow", RainbowEffect(), "Hue sweep across the stand")
    register_effect("gradient", GradientEffect(), "Still hue ramp — the ambient default")
    register_effect("wave", WaveEffect(), "Brightness crest travelling along the stand")
    register_effect("sparkle", SparkleEffect(), "Occasional twinkles over a dim base")
