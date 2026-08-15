"""Colour parsing, mixing, and gamma."""

from __future__ import annotations

import pytest

from livepulse.core.errors import ConfigError
from livepulse.leds.color import (
    BLACK,
    RGB,
    WHITE,
    Gamma,
    GammaTable,
    IdentityGamma,
    from_hsv,
    output_value,
    parse_hex,
    to_hex,
)


class TestParseHex:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("#000000", BLACK),
            ("#ffffff", WHITE),
            ("#FFFFFF", WHITE),
            ("#9146FF", RGB(0x91, 0x46, 0xFF)),
            ("9146ff", RGB(0x91, 0x46, 0xFF)),  # '#' optional
            ("  #9146ff  ", RGB(0x91, 0x46, 0xFF)),
            ("#f00", RGB(255, 0, 0)),  # shorthand expands
            ("#abc", RGB(0xAA, 0xBB, 0xCC)),
        ],
    )
    def test_accepts_valid_forms(self, text: str, expected: RGB) -> None:
        assert parse_hex(text) == expected

    @pytest.mark.parametrize("text", ["", "#", "#ff", "#fffff", "#gggggg", "red", "#1234567"])
    def test_rejects_invalid_with_a_readable_error(self, text: str) -> None:
        """V1 stored ``""`` in ColorHex, and hex_to_color raised deep in an effect."""
        with pytest.raises(ConfigError, match="not a hex colour"):
            parse_hex(text)

    def test_round_trips_through_to_hex(self) -> None:
        assert to_hex(parse_hex("#9146ff")) == "#9146ff"


class TestRGB:
    def test_scaling_dims(self) -> None:
        assert RGB(200, 100, 50).scaled(0.5) == RGB(100, 50, 25)

    def test_scaling_by_zero_is_black(self) -> None:
        assert RGB(200, 100, 50).scaled(0.0) is BLACK

    def test_scaling_by_one_returns_self(self) -> None:
        """Identity is the common case in the render loop; avoid the allocation."""
        color = RGB(200, 100, 50)
        assert color.scaled(1.0) is color

    def test_scaling_clamps_at_full(self) -> None:
        assert RGB(200, 200, 200).scaled(4.0) == WHITE

    @pytest.mark.parametrize(
        ("t", "expected"),
        [(0.0, RGB(0, 0, 0)), (0.5, RGB(50, 50, 50)), (1.0, RGB(100, 100, 100))],
    )
    def test_blend_interpolates(self, t: float, expected: RGB) -> None:
        assert BLACK.blend(RGB(100, 100, 100), t) == expected

    def test_blend_clamps_outside_unit_range(self) -> None:
        assert BLACK.blend(WHITE, -1.0) is BLACK
        assert BLACK.blend(WHITE, 2.0) is WHITE

    def test_is_black(self) -> None:
        assert BLACK.is_black
        assert not RGB(0, 0, 1).is_black


class TestFromHsv:
    @pytest.mark.parametrize(("hue", "expected"), [(0.0, RGB(255, 0, 0)), (1 / 3, RGB(0, 255, 0))])
    def test_primary_hues(self, hue: float, expected: RGB) -> None:
        assert from_hsv(hue) == expected

    def test_hue_wraps(self) -> None:
        """Sweeping effects can advance hue without remembering to modulo."""
        assert from_hsv(1.25) == from_hsv(0.25)


class TestGamma:
    def test_endpoints_are_preserved(self) -> None:
        table = GammaTable(2.8)
        assert table.apply(BLACK) == BLACK
        assert table.apply(WHITE) == WHITE

    def test_midtones_are_pulled_down(self) -> None:
        """The whole point: 50% drive does not look like 50% brightness."""
        corrected = GammaTable(2.8).apply(RGB(128, 128, 128))
        assert corrected.r < 128
        assert corrected.r == pytest.approx(37, abs=2)

    def test_is_monotonic(self) -> None:
        """Never invert an ordering — a brighter input must not render darker."""
        table = GammaTable(2.8)
        values = [table.apply(RGB(level, 0, 0)).r for level in range(256)]
        assert values == sorted(values)

    def test_rejects_non_positive_gamma(self) -> None:
        with pytest.raises(ConfigError, match="gamma must be positive"):
            GammaTable(0)

    def test_identity_gamma_is_a_passthrough(self) -> None:
        color = RGB(128, 64, 32)
        assert IdentityGamma().apply(color) == color


class TestOutputValue:
    """The final transform before a colour reaches the driver.

    Shared between the compositor and the diagnostic commands so that what
    ``livepulse leds test`` puts on the strip is exactly what the render loop
    would put there.
    """

    def test_full_brightness_is_gamma_only(self) -> None:
        color = RGB(200, 100, 50)
        assert output_value(color, 255, IdentityGamma()) == color

    def test_zero_brightness_is_black(self) -> None:
        assert output_value(WHITE, 0, IdentityGamma()) is BLACK

    def test_brightness_is_applied_before_gamma(self) -> None:
        """Scaling is perceptual; gamma converts perceptual to drive level.

        Correcting first and scaling afterwards would apply the curve to the
        wrong number and wash out every dimmed colour.
        """
        gamma = GammaTable(2.0)
        expected = gamma.apply(WHITE.scaled(128 / 255))
        assert output_value(WHITE, 128, gamma) == expected
        assert output_value(WHITE, 128, gamma) != gamma.apply(WHITE).scaled(128 / 255)

    def test_gamma_table_satisfies_the_protocol(self) -> None:
        assert isinstance(GammaTable(), Gamma)
        assert isinstance(IdentityGamma(), Gamma)
