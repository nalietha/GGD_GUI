"""Effects: colour as a pure function of time.

Importing this package registers the built-in effects, so anything that needs
them only has to import :func:`~livepulse.leds.effects.base.create_effect`.
"""

from livepulse.leds.effects.base import (
    Effect,
    EffectContext,
    available_effects,
    create_effect,
    describe_effects,
    register_effect,
)
from livepulse.leds.effects.builtin import register_builtin_effects

register_builtin_effects()

__all__ = [
    "Effect",
    "EffectContext",
    "available_effects",
    "create_effect",
    "describe_effects",
    "register_effect",
]
