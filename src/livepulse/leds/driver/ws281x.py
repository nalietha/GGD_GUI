"""PWM backend via ``rpi_ws281x``.

The V1 driver. Kept so existing builds keep working without rewiring.

**Does not work on Raspberry Pi 5.** The RP1 southbridge moved the PWM and DMA
peripherals the library drives directly, and it needs ``/dev/mem``, so the
process must run as root. New builds should prefer :mod:`~livepulse.leds.driver.spi`.

The import is deferred to construction time so this module is importable — and
the rest of the application testable — on any machine.
"""

from __future__ import annotations

from typing import Any

from livepulse.core.errors import DriverUnavailableError
from livepulse.leds.color import RGB
from livepulse.leds.driver.base import PixelBufferDriver

__all__ = ["Ws281xDriver"]

BACKEND = "ws281x"

#: Library strip-type constant per wire order. The library packs the bytes, so
#: we hand it logical RGB and let it order them.
_STRIP_TYPE_SUFFIX = {
    "RGB": "WS2811_STRIP_RGB",
    "GRB": "WS2811_STRIP_GRB",
    "BRG": "WS2811_STRIP_BRG",
    "RBG": "WS2811_STRIP_RBG",
    "GBR": "WS2811_STRIP_GBR",
    "BGR": "WS2811_STRIP_BGR",
}


class Ws281xDriver(PixelBufferDriver):
    """Drives a WS281x strip over hardware PWM."""

    def __init__(
        self,
        pixel_count: int,
        *,
        color_order: str = "GRB",
        gpio_pin: int = 18,
        freq_hz: int = 800_000,
        dma: int = 10,
        invert: bool = False,
        channel: int = 0,
    ) -> None:
        super().__init__(pixel_count, color_order=color_order)

        rpi_ws281x = _import_library()
        strip_type = getattr(rpi_ws281x, _STRIP_TYPE_SUFFIX[self.color_order])

        try:
            # Brightness is fixed at full: the compositor has already applied it,
            # so letting the library scale again would double-dim.
            self._strip: Any = rpi_ws281x.PixelStrip(
                pixel_count, gpio_pin, freq_hz, dma, invert, 255, channel, strip_type
            )
            self._strip.begin()
        except RuntimeError as exc:
            raise DriverUnavailableError(
                BACKEND,
                f"could not initialise GPIO {gpio_pin}: {exc}",
                remedy="run as root, or switch to the 'spi' backend which needs no privileges",
            ) from exc

        self._color = rpi_ws281x.Color

    def _transmit(self, buffer: list[RGB]) -> None:
        strip, pack = self._strip, self._color
        for index, color in enumerate(buffer):
            strip.setPixelColor(index, pack(color.r, color.g, color.b))
        strip.show()

    def _release(self) -> None:
        # The library has no explicit teardown; dropping the reference lets its
        # finaliser free the DMA channel.
        self._strip = None


def _import_library() -> Any:
    try:
        import rpi_ws281x
    except ImportError as exc:
        raise DriverUnavailableError(
            BACKEND,
            "the rpi_ws281x package is not installed",
            remedy=(
                "pip install 'livepulse[pwm]' — Raspberry Pi 4 or earlier only; "
                "on a Pi 5 use the 'spi' backend, which this library cannot replace"
            ),
        ) from exc
    return rpi_ws281x


def is_available() -> bool:
    """Whether this backend could plausibly run here.

    Cheap check used by ``auto`` selection and ``livepulse doctor``; it does not
    prove the process has permission, which only construction can establish.
    """
    try:
        _import_library()
    except DriverUnavailableError:
        return False
    return True
