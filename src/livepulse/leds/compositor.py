"""Turns a :class:`~livepulse.leds.state.RenderState` into a pixel buffer.

One frame is built in layers:

1. **Base** — each node's assigned effect, sampled at the current time.
2. **Transients** — one-shot overlays such as the go-live flash, blended in by
   their own alpha and dropped when they expire.
3. **Master** — a smooth fade for on/off, so switching the display off ramps
   down rather than cutting to black.
4. **Output** — brightness and gamma, then expansion from nodes to pixels.

Everything above the driver, so every backend receives identical bytes for
identical state — which is what makes the simulated backend a valid stand-in.

Transients are why this exists as a separate object from the renderer. V1's
go-live animation was a blocking call that took over the whole poll loop for
three seconds per node; here it is an overlay with a lifetime, and sixteen at
once cost sixteen blends.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from livepulse.core.errors import ConfigError
from livepulse.core.models import DisplayMode
from livepulse.leds.color import BLACK, RGB, Gamma, output_value
from livepulse.leds.effects import Effect, EffectContext, create_effect
from livepulse.leds.layout import Layout
from livepulse.leds.state import RenderState

__all__ = ["Compositor", "Transient"]

log = structlog.get_logger(__name__)

#: Seconds for the master fade when switching the display on or off. Long
#: enough to read as deliberate, short enough not to feel unresponsive.
MASTER_FADE_SECONDS = 0.35

#: How long a go-live flash runs before settling to the node's normal effect.
#: V1 alternated twelve times at 0.125s, which is where the 3s comes from.
DEFAULT_TRANSIENT_SECONDS = 3.0

#: Fraction of a transient's life spent fading out, so it settles into the
#: standing effect instead of snapping.
TRANSIENT_FADE_OUT = 0.25


@dataclass(slots=True)
class Transient:
    """A time-boxed overlay on a set of nodes."""

    node_ids: frozenset[int]
    effect: Effect
    started_at: float
    duration: float = DEFAULT_TRANSIENT_SECONDS

    def elapsed(self, now: float) -> float:
        return now - self.started_at

    def is_expired(self, now: float) -> bool:
        return self.elapsed(now) >= self.duration

    def alpha(self, now: float) -> float:
        """How strongly this overlays the base layer, 0.0 to 1.0.

        Full strength, then a linear fade over the last quarter of its life.
        """
        remaining = self.duration - self.elapsed(now)
        fade_window = self.duration * TRANSIENT_FADE_OUT
        if remaining >= fade_window or fade_window <= 0:
            return 1.0
        return max(0.0, remaining / fade_window)


@dataclass(slots=True)
class FrameStats:
    """What the diagnostics page reports about rendering."""

    frames: int = 0
    unknown_effects: dict[str, int] = field(default_factory=dict)

    def note_unknown(self, name: str) -> None:
        self.unknown_effects[name] = self.unknown_effects.get(name, 0) + 1


class Compositor:
    """Builds pixel buffers. Stateful only in the master fade and transients."""

    __slots__ = (
        "_buffer",
        "_effects",
        "_gamma",
        "_last_t",
        "_layout",
        "_master",
        "_stats",
        "_transients",
    )

    def __init__(self, layout: Layout, gamma: Gamma) -> None:
        self._layout = layout
        self._gamma = gamma
        self._transients: list[Transient] = []
        self._stats = FrameStats()
        self._effects: dict[str, Effect | None] = {}

        # Start dark and fade up, so a service restart does not slam the strip
        # to full brightness.
        self._master = 0.0
        self._last_t: float | None = None

        # Reused across frames: at 60 fps a fresh 112-element list every frame
        # is 6720 allocations a second for no benefit.
        self._buffer: list[RGB] = [BLACK] * layout.pixel_count

    @property
    def stats(self) -> FrameStats:
        return self._stats

    @property
    def active_transients(self) -> int:
        return len(self._transients)

    def add_transient(
        self,
        node_ids: frozenset[int] | set[int],
        effect_name: str,
        now: float,
        duration: float = DEFAULT_TRANSIENT_SECONDS,
    ) -> None:
        """Overlay a one-shot animation on some nodes.

        Replaces any existing transient on the same nodes rather than stacking:
        a channel that flaps offline/online repeatedly should re-flash, not
        accumulate overlapping animations.

        Raises:
            ConfigError: no such effect.
        """
        wanted = frozenset(node_ids)
        if not wanted:
            return

        effect = create_effect(effect_name)
        self._transients = [
            existing for existing in self._transients if not (existing.node_ids & wanted)
        ]
        self._transients.append(
            Transient(node_ids=wanted, effect=effect, started_at=now, duration=duration)
        )

    def clear_transients(self) -> None:
        self._transients.clear()

    def render(self, state: RenderState, t: float) -> list[RGB]:
        """Build the pixel buffer for time ``t``.

        Returns the compositor's internal buffer. The caller must hand it
        straight to a driver and not retain it — the next frame overwrites it.
        """
        self._advance_master(state, t)
        self._expire_transients(t)

        node_count = len(state.nodes)
        for view in state.nodes:
            ctx = EffectContext(
                node_id=view.node_id,
                color=view.color,
                alt_color=view.alt_color,
                node_count=node_count,
                params=view.params,
            )

            color = self._sample(view.effect, ctx, t)
            color = self._overlay(view.node_id, ctx, color, t)
            color = output_value(color, state.brightness, self._gamma)
            if self._master < 1.0:
                color = color.scaled(self._master)

            self._paint(view.node_id, color)

        self._stats.frames += 1
        return self._buffer

    # ----------------------------------------------------------------- #
    # Internals
    # ----------------------------------------------------------------- #

    def _resolve(self, effect_name: str) -> Effect | None:
        """Look up an effect, memoising the result.

        ``create_effect`` normalises the name, which allocates two strings. At
        60 fps across 16 nodes that is ~2000 needless allocations a second, so
        the resolved instance is cached — including the ``None`` for an unknown
        name, so a bad row does not re-raise and re-log every frame.
        """
        try:
            return self._effects[effect_name]
        except KeyError:
            pass

        try:
            resolved: Effect | None = create_effect(effect_name)
        except ConfigError:
            log.warning("unknown effect, node will be dark", effect=effect_name)
            resolved = None

        self._effects[effect_name] = resolved
        return resolved

    def _sample(self, effect_name: str, ctx: EffectContext, t: float) -> RGB:
        """Evaluate a node's effect, degrading to darkness if it is unusable.

        An unknown effect name — a renamed effect, a hand-edited row — must
        darken one node, never stop the display. The occurrence is counted so
        the diagnostics page can surface it, instead of it being invisible the
        way V1's ``print()`` was on a headless Pi.
        """
        effect = self._resolve(effect_name)
        if effect is None:
            self._stats.note_unknown(effect_name)
            return BLACK

        try:
            return effect.render(ctx, t)
        except Exception:
            # A third-party effect must not be able to kill the render thread.
            log.exception("effect raised while rendering", effect=effect_name, node=ctx.node_id)
            self._stats.note_unknown(effect_name)
            return BLACK

    def _overlay(self, node_id: int, ctx: EffectContext, base: RGB, t: float) -> RGB:
        for transient in self._transients:
            if node_id not in transient.node_ids:
                continue
            overlay = transient.effect.render(ctx, transient.elapsed(t))
            return base.blend(overlay, transient.alpha(t))
        return base

    def _advance_master(self, state: RenderState, t: float) -> None:
        """Ease the master level toward its target.

        Framerate-independent: the step is derived from elapsed time, so the
        fade takes the same wall-clock duration at 30 fps as at 60.
        """
        target = 0.0 if state.mode is DisplayMode.OFF else 1.0

        if self._last_t is None:
            self._last_t = t
            # First frame after start: begin the ramp rather than jumping.
            return

        delta = max(0.0, t - self._last_t)
        self._last_t = t

        if MASTER_FADE_SECONDS <= 0:
            self._master = target
            return

        step = delta / MASTER_FADE_SECONDS
        if self._master < target:
            self._master = min(target, self._master + step)
        elif self._master > target:
            self._master = max(target, self._master - step)

    def _expire_transients(self, t: float) -> None:
        if self._transients:
            self._transients = [tr for tr in self._transients if not tr.is_expired(t)]

    def _paint(self, node_id: int, color: RGB) -> None:
        for pixel in self._layout.pixels(node_id):
            self._buffer[pixel] = color
