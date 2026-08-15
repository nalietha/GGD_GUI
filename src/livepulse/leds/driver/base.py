"""LED driver contract.

Drivers are deliberately dumb: address a pixel, push the buffer, clean up.
They know about wire colour order and nothing else.

Brightness and gamma live in the compositor, not here, for two reasons.
Every backend then produces byte-identical output for the same state — which
is what makes the simulated backend a valid stand-in under test (Liskov) — and
there is exactly one place where brightness is applied, rather than V1's split
between a constructor argument, a ``setBrightness`` call, and a no-op re-read
loop that re-set every pixel to the value it already had.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from types import TracebackType
from typing import Protocol, runtime_checkable

from livepulse.core.errors import DriverError
from livepulse.leds.color import BLACK, RGB

__all__ = ["COLOR_ORDERS", "LedDriver", "PixelBufferDriver"]

#: Wire orders WS281x parts are manufactured in. Backends translate the name
#: into whatever constant their underlying library uses; the buffer above this
#: layer is always logical RGB.
COLOR_ORDERS = frozenset({"RGB", "GRB", "BRG", "RBG", "GBR", "BGR"})


@runtime_checkable
class LedDriver(Protocol):
    """What the renderer needs from any LED backend.

    Four methods. Anything larger tempts implementations into diverging.
    """

    @property
    def pixel_count(self) -> int:
        """Number of addressable pixels. Fixed for the driver's lifetime."""
        ...

    def set_pixel(self, index: int, color: RGB) -> None:
        """Stage a colour. Does not transmit."""
        ...

    def fill(self, color: RGB) -> None:
        """Stage a colour for every pixel."""
        ...

    def show(self) -> None:
        """Transmit the staged buffer to the strip."""
        ...

    def close(self) -> None:
        """Blank the strip and release the hardware. Must be idempotent."""
        ...


class PixelBufferDriver(ABC):
    """Base for backends that stage into a local buffer before transmitting.

    Holds the bounds checking, the wire-order permutation, and the close
    semantics that every real backend needs, so a new backend only implements
    :meth:`_transmit`. Adding one touches no existing code (open/closed).
    """

    def __init__(self, pixel_count: int, *, color_order: str = "GRB") -> None:
        if pixel_count <= 0:
            raise DriverError(f"pixel_count must be positive, got {pixel_count}")

        normalised = color_order.upper()
        if normalised not in COLOR_ORDERS:
            raise DriverError(
                f"unknown color order {color_order!r}; expected one of "
                f"{', '.join(sorted(COLOR_ORDERS))}"
            )

        self._color_order = normalised
        self._pixel_count = pixel_count
        self._buffer: list[RGB] = [BLACK] * pixel_count
        self._closed = False

    @property
    def pixel_count(self) -> int:
        return self._pixel_count

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def color_order(self) -> str:
        """Wire order name. Backends translate this to their library's constant."""
        return self._color_order

    def set_pixel(self, index: int, color: RGB) -> None:
        if not 0 <= index < self._pixel_count:
            raise DriverError(f"pixel {index} out of range (0..{self._pixel_count - 1})")
        self._buffer[index] = color

    def fill(self, color: RGB) -> None:
        self._buffer = [color] * self._pixel_count

    def show(self) -> None:
        if self._closed:
            raise DriverError("cannot show on a closed driver")
        self._transmit(self._buffer)

    def close(self) -> None:
        """Blank, transmit, then release. Idempotent.

        The blank matters: leaving 112 LEDs lit after the service stops is what
        V1 did on any exit that was not a clean KeyboardInterrupt.
        """
        if self._closed:
            return
        try:
            self.fill(BLACK)
            self._transmit(self._buffer)
        finally:
            self._closed = True
            self._release()

    @abstractmethod
    def _transmit(self, buffer: list[RGB]) -> None:
        """Push the staged buffer to the hardware."""

    def _release(self) -> None:  # noqa: B027 - optional hook, not every backend holds a handle
        """Free hardware handles. Override where there is something to free."""

    def __enter__(self) -> PixelBufferDriver:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
