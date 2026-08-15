"""Backend selection during the PWM → SPI phase-out.

Both backends are supported at once, and ``auto`` must never move an existing
build's data pin out from under it. Which GPIO pin the strip is soldered to is
a physical fact with no software signal, so these tests pin down what ``auto``
is allowed to infer and what it must not.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from livepulse.config.settings import LedSettings
from livepulse.leds.driver import registry
from livepulse.leds.driver.base import LedDriver
from livepulse.leds.driver.sim import SimulatedDriver

PIXELS = 8


@pytest.fixture
def isolated_registry() -> Iterator[None]:
    saved = dict(registry._BACKENDS)
    try:
        yield
    finally:
        registry._BACKENDS.clear()
        registry._BACKENDS.update(saved)


def _record(chosen: list[str], name: str) -> registry.BackendFactory:
    def factory(pixel_count: int, settings: LedSettings) -> LedDriver:
        chosen.append(name)
        return SimulatedDriver(pixel_count, color_order=settings.color_order)

    return factory


class TestAutoProtectsExistingWiring:
    def test_pwm_wins_when_both_are_available(self, isolated_registry: None) -> None:
        """The one that matters for an existing GPIO18 stand.

        rpi_ws281x is an optional extra, so its presence means somebody
        deliberately installed the PWM backend — which is what a working
        soldered-to-GPIO18 build looks like. Upgrading LivePulse must not
        silently start driving GPIO10 and leave the stand dark.
        """
        chosen: list[str] = []
        registry.register_backend("ws281x", _record(chosen, "ws281x"), lambda: True)
        registry.register_backend("spi", _record(chosen, "spi"), lambda: True)

        registry.create_driver(PIXELS, LedSettings(backend="auto")).close()

        assert chosen == ["ws281x"]

    def test_spi_wins_when_pwm_is_not_installed(self, isolated_registry: None) -> None:
        """A new build installs only livepulse[spi] and needs no configuration."""
        chosen: list[str] = []
        registry.register_backend("ws281x", _record(chosen, "ws281x"), lambda: False)
        registry.register_backend("spi", _record(chosen, "spi"), lambda: True)

        registry.create_driver(PIXELS, LedSettings(backend="auto")).close()

        assert chosen == ["spi"]

    def test_spi_wins_on_a_pi_5(self, isolated_registry: None) -> None:
        """The ws281x probe fails there, so no configuration is needed either."""
        chosen: list[str] = []
        # Mirrors ws281x.is_available() on a Pi 5: library present, board unusable.
        registry.register_backend("ws281x", _record(chosen, "ws281x"), lambda: False)
        registry.register_backend("spi", _record(chosen, "spi"), lambda: True)

        registry.create_driver(PIXELS, LedSettings(backend="auto")).close()

        assert chosen == ["spi"]

    def test_explicit_configuration_always_wins(self, isolated_registry: None) -> None:
        """After resoldering, leds.backend = 'spi' overrides the auto preference."""
        chosen: list[str] = []
        registry.register_backend("ws281x", _record(chosen, "ws281x"), lambda: True)
        registry.register_backend("spi", _record(chosen, "spi"), lambda: True)

        registry.create_driver(PIXELS, LedSettings(backend="spi")).close()

        assert chosen == ["spi"]

    def test_sim_is_the_last_resort(self, isolated_registry: None) -> None:
        chosen: list[str] = []
        registry.register_backend("ws281x", _record(chosen, "ws281x"), lambda: False)
        registry.register_backend("spi", _record(chosen, "spi"), lambda: False)

        driver = registry.create_driver(PIXELS, LedSettings(backend="auto"))
        assert isinstance(driver, SimulatedDriver)
        driver.close()


class TestDeprecation:
    def test_ws281x_is_marked_deprecated_with_migration_advice(self) -> None:
        retiring = registry.deprecated_backends()
        assert "ws281x" in retiring
        assert "GPIO10" in retiring["ws281x"], "the advice must name the destination pin"

    def test_spi_is_not_deprecated(self) -> None:
        assert "spi" not in registry.deprecated_backends()

    def test_deprecation_is_data_not_a_special_case(self, isolated_registry: None) -> None:
        """Any backend can be retired without the registry naming it.

        Open/closed: there is no ``if name == "ws281x"`` in the selection path.
        """
        registry.register_backend(
            "legacy", _record([], "legacy"), lambda: True, "move to something else"
        )
        assert registry.deprecated_backends()["legacy"] == "move to something else"

    def test_a_deprecated_backend_still_works(self, isolated_registry: None) -> None:
        """Phasing out is not breaking. It keeps running until the pin is moved."""
        chosen: list[str] = []
        registry.register_backend(
            "legacy", _record(chosen, "legacy"), lambda: True, "migration advice"
        )
        registry.create_driver(PIXELS, LedSettings(backend="legacy")).close()
        assert chosen == ["legacy"]


class TestProbesCheckRealCapability:
    def test_spi_probe_requires_a_device_node(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Library installed but SPI disabled in config.txt means the strip is not on GPIO10."""
        from livepulse.leds.driver import spi

        monkeypatch.setattr(spi, "_import_libraries", lambda: (object(), object()))
        monkeypatch.setattr(spi, "spi_devices", list)

        assert spi.is_available() is False

    def test_spi_probe_passes_with_a_device_node(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from pathlib import Path

        from livepulse.leds.driver import spi

        monkeypatch.setattr(spi, "_import_libraries", lambda: (object(), object()))
        monkeypatch.setattr(spi, "spi_devices", lambda: [Path("/dev/spidev0.0")])

        assert spi.is_available() is True

    def test_ws281x_probe_fails_on_a_pi_5(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from livepulse.leds.driver import ws281x

        monkeypatch.setattr(ws281x, "_import_library", lambda: object())
        monkeypatch.setattr(ws281x, "supports_pwm_ws281x", lambda: False)

        assert ws281x.is_available() is False

    def test_ws281x_probe_passes_on_a_pi_4(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from livepulse.leds.driver import ws281x

        monkeypatch.setattr(ws281x, "_import_library", lambda: object())
        monkeypatch.setattr(ws281x, "supports_pwm_ws281x", lambda: True)

        assert ws281x.is_available() is True
