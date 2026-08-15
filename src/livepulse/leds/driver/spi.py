"""SPI backend via ``adafruit-circuitpython-neopixel-spi``.

The recommended backend for new builds. Clocks the WS281x waveform out of the
SPI peripheral instead of bit-banging PWM, which means:

* it works on **Raspberry Pi 5**, where ``rpi_ws281x`` cannot run at all;
* it needs **no root** — membership of the ``spi`` group is enough;
* it leaves the PWM peripheral (and therefore onboard audio) alone.

The cost is one wire: data moves from GPIO18 to **GPIO10 (SPI0 MOSI)**, and
``dtparam=spi=on`` must be set in ``/boot/firmware/config.txt``.
"""

from __future__ import annotations

from typing import Any

from livepulse.core.errors import DriverUnavailableError
from livepulse.leds.color import RGB
from livepulse.leds.driver.base import PixelBufferDriver

__all__ = ["SpiDriver"]

BACKEND = "spi"

SPI_DEVICE_GLOB = "/dev/spidev*"


class SpiDriver(PixelBufferDriver):
    """Drives a WS281x strip over the SPI peripheral."""

    def __init__(
        self,
        pixel_count: int,
        *,
        color_order: str = "GRB",
        freq_hz: int = 800_000,
    ) -> None:
        super().__init__(pixel_count, color_order=color_order)

        board, neopixel_spi = _import_libraries()
        pixel_order = getattr(neopixel_spi, self.color_order)

        try:
            self._spi: Any = board.SPI()
            self._pixels: Any = neopixel_spi.NeoPixel_SPI(
                self._spi,
                pixel_count,
                pixel_order=pixel_order,
                frequency=freq_hz,
                # The compositor owns brightness; scaling twice would double-dim.
                brightness=1.0,
                auto_write=False,
            )
        except (OSError, PermissionError) as exc:
            raise DriverUnavailableError(
                BACKEND,
                f"could not open the SPI device: {exc}",
                remedy=(
                    "enable SPI (dtparam=spi=on in /boot/firmware/config.txt, then reboot) "
                    "and add this user to the 'spi' group"
                ),
            ) from exc

    def _transmit(self, buffer: list[RGB]) -> None:
        pixels = self._pixels
        for index, color in enumerate(buffer):
            # The library re-orders channels itself from pixel_order.
            pixels[index] = (color.r, color.g, color.b)
        pixels.show()

    def _release(self) -> None:
        deinit = getattr(self._pixels, "deinit", None)
        if callable(deinit):
            deinit()
        self._pixels = None
        self._spi = None


def _import_libraries() -> tuple[Any, Any]:
    try:
        import board
        import neopixel_spi
    except (ImportError, NotImplementedError) as exc:
        # Blinka raises NotImplementedError, not ImportError, when it is installed
        # but cannot identify the board — a desktop, for instance.
        raise DriverUnavailableError(
            BACKEND,
            f"CircuitPython SPI support is unavailable: {exc}",
            remedy="pip install 'livepulse[spi]' on a Raspberry Pi",
        ) from exc
    return board, neopixel_spi


def is_available() -> bool:
    """Whether this backend could plausibly run here."""
    try:
        _import_libraries()
    except DriverUnavailableError:
        return False
    return True
