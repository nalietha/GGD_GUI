"""Backend registration and ``auto`` selection."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from livepulse.config.settings import LedSettings
from livepulse.core.errors import DriverError
from livepulse.leds.color import RGB
from livepulse.leds.driver import registry
from livepulse.leds.driver.base import LedDriver, PixelBufferDriver
from livepulse.leds.driver.sim import SimulatedDriver

PIXELS = 8


@pytest.fixture
def isolated_registry() -> Iterator[None]:
    """Restore the built-in registrations after a test mutates them."""
    saved = dict(registry._BACKENDS)
    try:
        yield
    finally:
        registry._BACKENDS.clear()
        registry._BACKENDS.update(saved)


class TestCreateDriver:
    def test_builds_the_named_backend(self) -> None:
        driver = registry.create_driver(PIXELS, LedSettings(backend="sim"))
        assert isinstance(driver, SimulatedDriver)
        assert driver.pixel_count == PIXELS
        driver.close()

    def test_passes_color_order_through(self) -> None:
        driver = registry.create_driver(PIXELS, LedSettings(backend="sim", color_order="BGR"))
        assert isinstance(driver, PixelBufferDriver)
        assert driver.color_order == "BGR"
        driver.close()

    def test_unknown_backend_raises_and_names_the_alternatives(
        self, isolated_registry: None
    ) -> None:
        registry._BACKENDS.pop("sim")
        with pytest.raises(DriverError, match="unknown LED backend 'sim'") as caught:
            registry.create_driver(PIXELS, LedSettings(backend="sim"))
        assert "ws281x" in str(caught.value), "the error should list what is available"


class TestAutoSelection:
    def test_falls_back_to_sim_with_no_hardware(self) -> None:
        """A laptop must still start the application, loudly but successfully."""
        driver = registry.create_driver(PIXELS, LedSettings(backend="auto"))
        assert isinstance(driver, SimulatedDriver)
        driver.close()

    def test_prefers_an_available_hardware_backend(self, isolated_registry: None) -> None:
        built: list[str] = []

        def fake_factory(pixel_count: int, settings: LedSettings) -> LedDriver:
            built.append("spi")
            return SimulatedDriver(pixel_count, color_order=settings.color_order)

        registry.register_backend("spi", fake_factory, probe=lambda: True)

        driver = registry.create_driver(PIXELS, LedSettings(backend="auto"))
        assert built == ["spi"], "auto must prefer SPI over the simulated fallback"
        driver.close()

    def test_skips_a_backend_whose_probe_fails(self, isolated_registry: None) -> None:
        registry.register_backend("spi", _unreachable_factory, probe=lambda: False)
        registry.register_backend("ws281x", _unreachable_factory, probe=lambda: False)

        driver = registry.create_driver(PIXELS, LedSettings(backend="auto"))
        assert isinstance(driver, SimulatedDriver)
        driver.close()


class TestRegistration:
    def test_a_new_backend_needs_no_core_changes(self, isolated_registry: None) -> None:
        """Open/closed, demonstrated: register and it is selectable.

        No dispatch table, no ``if backend ==`` chain, no caller edited.
        """

        def build_double(pixel_count: int, settings: LedSettings) -> LedDriver:
            return SimulatedDriver(pixel_count * 2, color_order=settings.color_order)

        registry.register_backend("doubler", build_double)

        driver = registry.create_driver(PIXELS, LedSettings(backend="doubler"))
        assert driver.pixel_count == PIXELS * 2
        driver.close()

    def test_available_backends_reports_probe_results(self) -> None:
        available = registry.available_backends()
        assert available["sim"] is True
        assert set(available) >= {"sim", "spi", "ws281x"}


def _unreachable_factory(pixel_count: int, settings: LedSettings) -> LedDriver:
    raise AssertionError("this backend should not have been selected")


class TestHardwareBackendsDegradeGracefully:
    """Importing a hardware module on a laptop must not explode.

    V1's LED modules built a ``PixelStrip`` at import time, so nothing in the
    LED package could even be imported off-Pi — which is why none of it was
    ever tested.
    """

    def test_ws281x_reports_unavailable_without_the_library(self) -> None:
        from livepulse.leds.driver import ws281x

        assert isinstance(ws281x.is_available(), bool)

    def test_spi_reports_unavailable_without_the_library(self) -> None:
        from livepulse.leds.driver import spi

        assert isinstance(spi.is_available(), bool)

    def test_unavailable_backend_explains_the_remedy(self) -> None:
        from livepulse.core.errors import DriverUnavailableError
        from livepulse.leds.driver import ws281x

        if ws281x.is_available():  # pragma: no cover - on a Pi with the lib present
            pytest.skip("rpi_ws281x is installed here")

        with pytest.raises(DriverUnavailableError) as caught:
            ws281x.Ws281xDriver(PIXELS)

        assert caught.value.remedy is not None
        assert "spi" in str(caught.value)


class TestPixelValuesReachTheDriver:
    def test_a_frame_survives_the_registry(self) -> None:
        driver = registry.create_driver(3, LedSettings(backend="sim"))
        assert isinstance(driver, SimulatedDriver)
        driver.set_pixel(1, RGB(9, 8, 7))
        driver.show()
        assert driver.frame[1] == RGB(9, 8, 7)
        driver.close()
