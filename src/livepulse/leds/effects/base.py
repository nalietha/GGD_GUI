"""Effect contract and registry.

An effect is a **pure function of time**: given a node's context and a
timestamp, return the colour that node should be. It does not sleep, does not
call ``show()``, and does not know a strip exists.

That is the whole point. V1's effects each owned the CPU — ``pulse_node`` slept
about six seconds and ``flash_node`` three, calling ``strip.show()`` themselves
from inside the polling loop. Sixteen nodes going live together blocked
ingestion for over a minute. Here the compositor samples every effect once per
frame and sixteen simultaneous animations cost the same as one.

Purity also makes them testable: render frame 0, frame 1000, and frame 60000
instantly, with no sleeping and no hardware.

One colour per node, not per pixel
----------------------------------

:meth:`Effect.render` returns a single :class:`~livepulse.leds.color.RGB` for
the whole node. The nodes are 7-LED jewels behind frosted acrylic, so variation
*within* one node is not visible — V1 animated a rainbow across each jewel's
seven LEDs and the diffuser averaged it away. Spending allocations on detail
the hardware cannot show would be waste.

If a build ever uses bare, undiffused nodes, this is where a second
``render_pixels`` protocol would go. Not before.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, NamedTuple, Protocol, runtime_checkable

from livepulse.core.errors import ConfigError
from livepulse.leds.color import RGB

__all__ = [
    "Effect",
    "EffectContext",
    "available_effects",
    "create_effect",
    "describe_effects",
    "register_effect",
]


class EffectContext(NamedTuple):
    """Everything an effect may know about the node it is drawing.

    Deliberately small: an effect that could see the whole application would
    end up depending on it.
    """

    node_id: int
    """Logical node. Effects use it to offset phase so a row does not pulse in unison."""

    color: RGB
    """The node's resolved primary colour — its channel's, or its own when unbound."""

    alt_color: RGB | None
    """Optional second colour, for effects that alternate."""

    node_count: int
    """Total nodes, so position-dependent effects can normalise across the stand."""

    params: Mapping[str, Any] = {}
    """Per-node tuning from the database. Opaque to everything but the effect."""

    def number(self, key: str, default: float) -> float:
        """Read a numeric parameter, falling back when absent or unusable.

        A bad value in one node's params must not take the render loop down,
        so this never raises — the node just animates with the default.
        """
        try:
            return float(self.params[key])
        except (KeyError, TypeError, ValueError):
            return default

    @property
    def phase(self) -> float:
        """This node's position across the stand, 0.0 to 1.0."""
        return self.node_id / self.node_count if self.node_count else 0.0


@runtime_checkable
class Effect(Protocol):
    """A colour as a function of time."""

    def render(self, ctx: EffectContext, t: float) -> RGB:
        """The node's colour at ``t`` seconds.

        Must be pure: same inputs, same output, no side effects, no sleeping.
        ``t`` is monotonic and shared by every node in the frame, so effects
        that should move together do.
        """
        ...


class _Registration(NamedTuple):
    effect: Effect
    description: str


_EFFECTS: dict[str, _Registration] = {}


def register_effect(name: str, effect: Effect, description: str = "") -> None:
    """Make an effect selectable by name.

    Adding one touches nothing else — no compositor branch, no enum, no config
    type. Instances are shared across every node because effects are stateless;
    all per-node variation arrives through :class:`EffectContext`.

    Args:
        name: Value stored in ``nodes.effect``. Lowercase.
        effect: The stateless renderer.
        description: One line, shown in the UI's effect picker.
    """
    _EFFECTS[name.strip().lower()] = _Registration(effect, description)


def create_effect(name: str) -> Effect:
    """Look up an effect by name.

    Raises:
        ConfigError: no such effect, listing the registered names. V1 printed
            "Unknown display setting" to stdout and silently fell back, so a
            typo in a node's config was invisible.
    """
    try:
        return _EFFECTS[name.strip().lower()].effect
    except KeyError:
        raise ConfigError(
            f"unknown effect {name!r}; expected one of {', '.join(sorted(_EFFECTS))}"
        ) from None


def available_effects() -> list[str]:
    """Registered effect names, sorted."""
    return sorted(_EFFECTS)


def describe_effects() -> dict[str, str]:
    """Effect names mapped to their descriptions, for the UI picker."""
    return {name: reg.description for name, reg in sorted(_EFFECTS.items())}
