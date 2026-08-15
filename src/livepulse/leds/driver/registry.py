"""Backend registry and selection.

The one place that maps a backend name to a constructor. Adding a backend means
registering it here and nowhere else — no caller learns a new name, and no
``if backend == ...`` chain appears in the renderer (open/closed).

``auto`` resolution exists because the correct backend depends on hardware the
user should not have to identify: ``rpi_ws281x`` cannot run on a Pi 5, and
neither hardware backend can run on a laptop.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

import structlog

from livepulse.config.settings import LedSettings
from livepulse.core.errors import DriverError, DriverUnavailableError
from livepulse.leds.driver import spi, ws281x
from livepulse.leds.driver.base import LedDriver
from livepulse.leds.driver.sim import SimulatedDriver

__all__ = ["available_backends", "create_driver", "register_backend"]

log = structlog.get_logger(__name__)

AUTO = "auto"
SIM = "sim"


class BackendFactory(Protocol):
    """Builds a driver for a pixel count and hardware settings."""

    def __call__(self, pixel_count: int, settings: LedSettings) -> LedDriver: ...


class _Backend:
    __slots__ = ("factory", "name", "probe")

    def __init__(
        self,
        name: str,
        factory: BackendFactory,
        probe: Callable[[], bool],
    ) -> None:
        self.name = name
        self.factory = factory
        self.probe = probe


_BACKENDS: dict[str, _Backend] = {}

#: Preference order for ``auto``. Most capable first; ``sim`` always works, so
#: the application starts even with no hardware and says so in the log.
_AUTO_ORDER = (spi.BACKEND, ws281x.BACKEND, SIM)


def register_backend(
    name: str,
    factory: BackendFactory,
    probe: Callable[[], bool] = lambda: True,
) -> None:
    """Make a backend selectable by name.

    Args:
        name: Value users put in ``leds.backend``.
        factory: Builds the driver.
        probe: Cheap availability check for ``auto`` selection. Must not raise
            and must not touch hardware.
    """
    _BACKENDS[name] = _Backend(name, factory, probe)


def available_backends() -> dict[str, bool]:
    """Every registered backend, mapped to whether it can run here."""
    return {name: backend.probe() for name, backend in sorted(_BACKENDS.items())}


def create_driver(pixel_count: int, settings: LedSettings) -> LedDriver:
    """Build the configured driver.

    Raises:
        DriverError: the configured backend is not a registered name.
        DriverUnavailableError: it is registered but cannot run here. Never
            raised for ``auto``, which falls back to ``sim``.
    """
    name = settings.backend
    resolved = _resolve_auto() if name == AUTO else name

    backend = _BACKENDS.get(resolved)
    if backend is None:
        raise DriverError(
            f"unknown LED backend {resolved!r}; expected one of "
            f"{', '.join(sorted(_BACKENDS))} or {AUTO!r}"
        )

    driver = backend.factory(pixel_count, settings)
    log.info("led backend ready", backend=resolved, pixels=pixel_count)
    return driver


def _resolve_auto() -> str:
    for candidate in _AUTO_ORDER:
        backend = _BACKENDS.get(candidate)
        if backend is not None and backend.probe():
            if candidate == SIM:
                log.warning(
                    "no LED hardware detected, running simulated",
                    hint="set leds.backend explicitly to fail loudly instead",
                )
            return candidate
    raise DriverUnavailableError(  # pragma: no cover - sim always probes true
        AUTO, "no backend is available", remedy="set leds.backend = 'sim'"
    )


# --------------------------------------------------------------------------- #
# Built-in backends
# --------------------------------------------------------------------------- #


def _build_sim(pixel_count: int, settings: LedSettings) -> LedDriver:
    return SimulatedDriver(pixel_count, color_order=settings.color_order)


def _build_ws281x(pixel_count: int, settings: LedSettings) -> LedDriver:
    return ws281x.Ws281xDriver(
        pixel_count,
        color_order=settings.color_order,
        gpio_pin=settings.gpio_pin,
        freq_hz=settings.freq_hz,
        dma=settings.dma,
        invert=settings.invert,
        channel=settings.channel,
    )


def _build_spi(pixel_count: int, settings: LedSettings) -> LedDriver:
    return spi.SpiDriver(
        pixel_count,
        color_order=settings.color_order,
        freq_hz=settings.freq_hz,
    )


register_backend(SIM, _build_sim)
register_backend(ws281x.BACKEND, _build_ws281x, ws281x.is_available)
register_backend(spi.BACKEND, _build_spi, spi.is_available)
