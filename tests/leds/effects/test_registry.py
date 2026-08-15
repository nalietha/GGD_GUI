"""Effect registry and context."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from livepulse.core.errors import ConfigError
from livepulse.leds.color import RGB, WHITE
from livepulse.leds.effects import available_effects, create_effect, describe_effects
from livepulse.leds.effects import base as effects_base
from livepulse.leds.effects.base import Effect, EffectContext, register_effect


@pytest.fixture
def isolated_registry() -> Iterator[None]:
    saved = dict(effects_base._EFFECTS)
    try:
        yield
    finally:
        effects_base._EFFECTS.clear()
        effects_base._EFFECTS.update(saved)


class TestRegistry:
    @pytest.mark.parametrize(
        "name",
        [
            "off",
            "static",
            "pulse",
            "breathe",
            "blink",
            "flash",
            "rainbow",
            "gradient",
            "wave",
            "sparkle",
        ],
    )
    def test_builtin_effects_are_registered(self, name: str) -> None:
        assert isinstance(create_effect(name), Effect)

    def test_lookup_is_case_and_whitespace_insensitive(self) -> None:
        """V1 stored ' Static' and 'static' interchangeably in save.json."""
        assert create_effect("  STATIC  ") is create_effect("static")

    def test_unknown_effect_names_the_alternatives(self) -> None:
        """V1 printed 'Unknown display setting' and silently fell back."""
        with pytest.raises(ConfigError, match="unknown effect 'kaleidoscope'") as caught:
            create_effect("kaleidoscope")
        assert "static" in str(caught.value)

    def test_available_effects_is_sorted(self) -> None:
        names = available_effects()
        assert names == sorted(names)

    def test_descriptions_are_supplied_for_the_ui(self) -> None:
        assert describe_effects()["gradient"]

    def test_a_new_effect_needs_no_core_changes(self, isolated_registry: None) -> None:
        """Open/closed: register and it is selectable, with no compositor edit."""

        class AlwaysWhite:
            def render(self, ctx: EffectContext, t: float) -> RGB:
                return WHITE

        register_effect("always-white", AlwaysWhite(), "test effect")

        effect = create_effect("always-white")
        assert effect.render(EffectContext(0, RGB(1, 2, 3), None, 16), 0.0) == WHITE
        assert "always-white" in available_effects()

    def test_registration_is_idempotent(self, isolated_registry: None) -> None:
        """Re-importing the builtins must not duplicate or fail."""
        before = len(available_effects())
        from livepulse.leds.effects.builtin import register_builtin_effects

        register_builtin_effects()
        assert len(available_effects()) == before


class TestEffectContext:
    def test_number_reads_a_param(self) -> None:
        assert EffectContext(0, WHITE, None, 16, {"period": 4.0}).number("period", 2.0) == 4.0

    def test_number_coerces_a_numeric_string(self) -> None:
        """JSON round-trips can widen a type; do not punish the node for it."""
        assert EffectContext(0, WHITE, None, 16, {"period": "4"}).number("period", 2.0) == 4.0

    @pytest.mark.parametrize("value", [None, "fast", [], {}])
    def test_number_falls_back_on_an_unusable_value(self, value: object) -> None:
        """A bad row must not raise inside the render loop."""
        ctx = EffectContext(0, WHITE, None, 16, {"period": value})
        assert ctx.number("period", 2.0) == 2.0

    def test_number_falls_back_when_absent(self) -> None:
        assert EffectContext(0, WHITE, None, 16).number("period", 2.0) == 2.0

    def test_phase_spreads_nodes_across_the_stand(self) -> None:
        assert EffectContext(0, WHITE, None, 4).phase == 0.0
        assert EffectContext(2, WHITE, None, 4).phase == 0.5

    def test_phase_survives_a_zero_node_count(self) -> None:
        """An empty stand is a valid, if pointless, configuration."""
        assert EffectContext(0, WHITE, None, 0).phase == 0.0
