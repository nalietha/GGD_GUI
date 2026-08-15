"""Colour representation and correction.

The only place that parses hex, mixes colours, or applies gamma. V1 had
``hex_to_color`` in the LED utilities, ``RGBToHex`` in the C# utilities, and
ad-hoc bit-shifting inside three separate effects.

:class:`RGB` is a ``NamedTuple`` rather than a pydantic model on purpose: it is
allocated once per pixel per frame — 112 pixels at 60 fps is 6720 allocations a
second — so it needs to stay a plain tuple.
"""

from __future__ import annotations

import colorsys
from typing import NamedTuple, Protocol, runtime_checkable

from livepulse.core.errors import ConfigError

__all__ = [
    "BLACK",
    "RGB",
    "WHITE",
    "Gamma",
    "GammaTable",
    "IdentityGamma",
    "from_hsv",
    "output_value",
    "parse_hex",
    "to_hex",
]

_MAX = 255


class RGB(NamedTuple):
    """An 8-bit-per-channel colour in logical RGB order.

    Wire order (GRB on most WS281x parts) is a driver concern and is applied at
    the very last moment, so nothing above the driver has to think about it.
    """

    r: int
    g: int
    b: int

    def scaled(self, factor: float) -> RGB:
        """Multiply every channel, clamped. Used for brightness and fades."""
        if factor <= 0.0:
            return BLACK
        if factor == 1.0:
            return self
        return RGB(
            min(_MAX, int(self.r * factor)),
            min(_MAX, int(self.g * factor)),
            min(_MAX, int(self.b * factor)),
        )

    def blend(self, other: RGB, t: float) -> RGB:
        """Linear interpolation toward ``other``. ``t=0`` is self, ``t=1`` is other."""
        if t <= 0.0:
            return self
        if t >= 1.0:
            return other
        inverse = 1.0 - t
        return RGB(
            int(self.r * inverse + other.r * t),
            int(self.g * inverse + other.g * t),
            int(self.b * inverse + other.b * t),
        )

    @property
    def is_black(self) -> bool:
        return self == BLACK


BLACK = RGB(0, 0, 0)
WHITE = RGB(_MAX, _MAX, _MAX)


def parse_hex(value: str) -> RGB:
    """Parse ``#rgb`` or ``#rrggbb`` (the leading ``#`` is optional).

    Raises:
        ConfigError: the string is not a colour. V1 crashed on the empty string
            it happily stored in ``ColorHex``; failing here with a readable
            message beats a ``ValueError`` from deep inside an effect.
    """
    text = value.strip().lstrip("#")

    if len(text) == 3:
        text = "".join(char * 2 for char in text)
    if len(text) != 6:
        raise ConfigError(f"not a hex colour: {value!r}")

    try:
        return RGB(int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError as exc:
        raise ConfigError(f"not a hex colour: {value!r}") from exc


def to_hex(color: RGB) -> str:
    """Render as ``#rrggbb`` (lowercase), the form the web UI and DB use."""
    return f"#{color.r:02x}{color.g:02x}{color.b:02x}"


def from_hsv(hue: float, saturation: float = 1.0, value: float = 1.0) -> RGB:
    """Build a colour from HSV. ``hue`` wraps, so 1.25 and 0.25 agree.

    Rainbow and gradient effects sweep hue; wrapping here means they do not
    each have to remember to modulo.
    """
    red, green, blue = colorsys.hsv_to_rgb(hue % 1.0, saturation, value)
    return RGB(int(red * _MAX), int(green * _MAX), int(blue * _MAX))


class GammaTable:
    """Perceptual brightness correction via a precomputed lookup table.

    LEDs are linear in power but eyes are not, so an uncorrected value of 128
    looks far brighter than half. Without this, dim colours crush to black and
    mixed colours shift hue — V1 applied no correction at all.

    The table is 256 entries built once at startup; correction in the render
    loop is then three list indexes.
    """

    __slots__ = ("_gamma", "_table")

    def __init__(self, gamma: float = 2.8) -> None:
        if gamma <= 0:
            raise ConfigError(f"gamma must be positive, got {gamma}")
        self._gamma = gamma
        self._table: tuple[int, ...] = tuple(
            round((level / _MAX) ** gamma * _MAX) for level in range(_MAX + 1)
        )

    @property
    def gamma(self) -> float:
        return self._gamma

    def apply(self, color: RGB) -> RGB:
        table = self._table
        return RGB(table[color.r], table[color.g], table[color.b])


class IdentityGamma:
    """No correction.

    Used by tests that assert on exact byte values, and available to users
    whose strips are already corrected in hardware.
    """

    __slots__ = ()
    gamma = 1.0

    def apply(self, color: RGB) -> RGB:
        return color


@runtime_checkable
class Gamma(Protocol):
    """Anything that can correct a colour for output.

    Two methods would be one too many: the compositor and the diagnostic
    commands only ever need :meth:`apply`, so that is all the protocol asks for.
    """

    def apply(self, color: RGB) -> RGB: ...


def output_value(color: RGB, brightness: int, gamma: Gamma) -> RGB:
    """The final transform before a colour reaches the driver.

    Brightness first, then gamma. Scaling is a perceptual instruction ("half as
    bright"), and gamma is what turns a perceptual value into the drive level
    the LED needs — so correcting first and scaling afterwards would apply the
    curve to the wrong number.

    This lives here rather than in the compositor because the diagnostic
    commands need the identical transform: what ``livepulse leds test`` puts on
    the strip has to be exactly what the render loop would.
    """
    return gamma.apply(color.scaled(brightness / _MAX))
