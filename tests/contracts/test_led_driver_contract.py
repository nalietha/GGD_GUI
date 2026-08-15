"""Shared contract every LED backend must satisfy.

This is the Liskov guard. The renderer is written against :class:`LedDriver`
and must not care which backend it got, so every implementation is held to the
same behaviour here — including ``sim``, which is what makes it a legitimate
stand-in under test rather than a convenient fiction.

Hardware backends are skipped when their libraries are absent, so the suite
runs on a laptop and gets fuller coverage on a Pi.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest

from livepulse.core.errors import DriverError
from livepulse.leds.color import BLACK, RGB, WHITE
from livepulse.leds.driver import spi, ws281x
from livepulse.leds.driver.base import LedDriver, PixelBufferDriver
from livepulse.leds.driver.sim import SimulatedDriver

PIXELS = 12

DriverFactory = Callable[[], PixelBufferDriver]


def _sim() -> PixelBufferDriver:
    return SimulatedDriver(PIXELS)


def _ws281x() -> PixelBufferDriver:  # pragma: no cover - only runs on a Pi
    return ws281x.Ws281xDriver(PIXELS)


def _spi() -> PixelBufferDriver:  # pragma: no cover - only runs on a Pi
    return spi.SpiDriver(PIXELS)


ALL_BACKENDS = [
    pytest.param(_sim, id="sim"),
    pytest.param(
        _ws281x,
        id="ws281x",
        marks=pytest.mark.skipif(not ws281x.is_available(), reason="rpi_ws281x not installed"),
    ),
    pytest.param(
        _spi,
        id="spi",
        marks=pytest.mark.skipif(not spi.is_available(), reason="neopixel_spi not installed"),
    ),
]


@pytest.fixture(params=ALL_BACKENDS)
def driver(request: pytest.FixtureRequest) -> Iterator[PixelBufferDriver]:
    factory: DriverFactory = request.param
    instance = factory()
    try:
        yield instance
    finally:
        instance.close()


class TestContract:
    def test_satisfies_the_protocol(self, driver: PixelBufferDriver) -> None:
        assert isinstance(driver, LedDriver)

    def test_reports_its_pixel_count(self, driver: PixelBufferDriver) -> None:
        assert driver.pixel_count == PIXELS

    def test_accepts_every_valid_index(self, driver: PixelBufferDriver) -> None:
        for index in range(PIXELS):
            driver.set_pixel(index, WHITE)
        driver.show()

    @pytest.mark.parametrize("index", [-1, PIXELS, PIXELS + 100])
    def test_rejects_out_of_range_index(self, driver: PixelBufferDriver, index: int) -> None:
        """Silently ignoring a bad index hides layout bugs; raise instead."""
        with pytest.raises(DriverError, match="out of range"):
            driver.set_pixel(index, WHITE)

    def test_fill_then_show_is_accepted(self, driver: PixelBufferDriver) -> None:
        driver.fill(RGB(10, 20, 30))
        driver.show()

    def test_close_is_idempotent(self, driver: PixelBufferDriver) -> None:
        """systemd may stop a unit that already crashed mid-shutdown."""
        driver.close()
        driver.close()
        assert driver.closed

    def test_show_after_close_raises(self, driver: PixelBufferDriver) -> None:
        driver.close()
        with pytest.raises(DriverError, match="closed"):
            driver.show()

    def test_context_manager_closes(self, driver: PixelBufferDriver) -> None:
        with driver:
            driver.fill(WHITE)
            driver.show()
        assert driver.closed


class TestConstruction:
    @pytest.mark.parametrize("count", [0, -1])
    def test_rejects_non_positive_pixel_count(self, count: int) -> None:
        with pytest.raises(DriverError, match="must be positive"):
            SimulatedDriver(count)

    def test_rejects_unknown_color_order(self) -> None:
        with pytest.raises(DriverError, match="unknown color order"):
            SimulatedDriver(PIXELS, color_order="XYZ")

    @pytest.mark.parametrize("order", ["GRB", "grb", "RGB", "BGR"])
    def test_color_order_is_normalised(self, order: str) -> None:
        assert SimulatedDriver(PIXELS, color_order=order).color_order == order.upper()


class TestSimulatedSpecifics:
    """Behaviour only the simulated backend can be asked about."""

    def test_captures_the_transmitted_frame(self) -> None:
        driver = SimulatedDriver(4)
        driver.set_pixel(0, RGB(1, 2, 3))
        driver.set_pixel(3, RGB(4, 5, 6))
        driver.show()

        assert driver.frame == (RGB(1, 2, 3), BLACK, BLACK, RGB(4, 5, 6))

    def test_staged_changes_are_invisible_until_show(self) -> None:
        """A half-drawn frame must never reach the strip."""
        driver = SimulatedDriver(2)
        driver.show()
        driver.set_pixel(0, WHITE)
        assert driver.frame == (BLACK, BLACK)

    def test_counts_frames(self) -> None:
        driver = SimulatedDriver(2)
        for _ in range(5):
            driver.show()
        assert driver.show_count == 5

    def test_history_is_bounded(self) -> None:
        """A 60 fps loop left running for a week must not grow without limit."""
        driver = SimulatedDriver(2, history=3)
        for _ in range(50):
            driver.show()
        assert len(driver.frames) == 3

    def test_close_blanks_the_strip(self) -> None:
        """V1 left 112 LEDs lit on any exit that was not a clean interrupt."""
        driver = SimulatedDriver(3)
        driver.fill(WHITE)
        driver.show()
        driver.close()
        assert driver.frame == (BLACK, BLACK, BLACK)
