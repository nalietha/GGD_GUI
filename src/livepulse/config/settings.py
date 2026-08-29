"""Configuration.

The single read path for static configuration. No other module opens a config
file — V1 had five different call sites reading ``appsettings.json`` by three
different path strategies, two of which resolved to a file the writer never
touched, which is why its settings silently did nothing.

Layering, highest priority first:

1. Environment variables — ``LIVEPULSE_LEDS__BACKEND=spi`` (``__`` nests)
2. ``.env`` in the working directory
3. The TOML config file
4. Defaults declared here

Runtime settings the user edits in the web UI (brightness, display mode) do
**not** live here. Those go in the database, because a web request must never
rewrite the file that also holds credentials — the V1 mistake that camelCased
``appsettings.json`` and erased every setting on the next read.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from livepulse.core.errors import ConfigError

__all__ = [
    "IngestSettings",
    "LayoutSettings",
    "LedSettings",
    "Settings",
    "WebSettings",
    "default_config_path",
    "load_settings",
    "provider_secrets",
]

ENV_PREFIX = "LIVEPULSE_"
ENV_CONFIG_PATH = "LIVEPULSE_CONFIG"

#: Credentials arrive as LIVEPULSE_SECRET_<PROVIDER>_<KEY>. Kept out of the
#: Settings model entirely so they can never be rendered by the config
#: command or the web UI — V1 kept the Twitch secret in the same file its
#: settings page rewrote.
ENV_SECRET_PREFIX = "LIVEPULSE_SECRET_"

NodeOrder = Literal["linear", "serpentine", "custom"]


class _StrictSection(BaseModel):
    """Base for every config section.

    ``extra="forbid"`` does not propagate from ``BaseSettings`` into nested
    models, so it is declared once here and inherited. Without it a typo like
    ``[layout] nodez = 24`` is silently discarded and the default is used —
    precisely the V1 failure where a renamed settings key made the LED process
    fall back to a hardcoded default for months without a single log line.
    """

    model_config = ConfigDict(extra="forbid")


class LedSettings(_StrictSection):
    """Hardware wiring and signal parameters.

    Brightness is deliberately absent: it is a runtime setting, and it is applied
    by the compositor rather than the driver so every backend — including ``sim``
    — produces byte-identical output for the same state.
    """

    backend: str = "auto"
    """Driver backend name, or ``auto`` to pick the best one this machine can run.

    Deliberately not a ``Literal``: the driver registry is open to new backends,
    and enumerating them here would mean a third-party backend could register
    successfully and then be rejected by config validation. Unknown names are
    caught at driver construction, which reports the registered alternatives.
    """

    gpio_pin: int = 18
    """PWM data pin for the ``ws281x`` backend. GPIO10 (MOSI) is used by ``spi``."""

    spi_bus: int = 0
    spi_device: int = 0

    freq_hz: int = 800_000
    dma: int = 10
    invert: bool = False
    channel: int = 0

    color_order: Literal["RGB", "GRB", "BRG", "RBG", "GBR", "BGR"] = "GRB"
    """WS2811/WS2812 parts are almost always GRB on the wire."""

    gamma: float = Field(default=2.8, gt=0, le=5.0)
    """Perceptual correction. V1 had none, which is why its mid-tones looked wrong."""

    fps: int = Field(default=60, ge=1, le=240)
    """Render loop target. Effects are sampled at this rate."""


class LayoutSettings(_StrictSection):
    """Physical arrangement of nodes on the strip.

    Everything the node→pixel mapping needs. Rewiring the stand or building a
    second one is a config change, not a code change.
    """

    nodes: int = Field(default=16, ge=1)
    leds_per_node: int = Field(default=7, ge=1)

    order: NodeOrder = "serpentine"
    rows: int = Field(default=2, ge=1)

    start_at_last_row: bool = True
    """Data line enters at the last row. True reproduces the V1 stand's wiring."""

    first_row_reversed: bool = False
    """Flips the direction of the first emitted row."""

    custom_order: list[int] | None = None
    """``order="custom"``: physical position *i* holds logical node ``custom_order[i]``."""

    @property
    def pixel_count(self) -> int:
        return self.nodes * self.leds_per_node

    @model_validator(mode="after")
    def _check_consistency(self) -> LayoutSettings:
        if self.order == "custom":
            if self.custom_order is None:
                raise ValueError("order='custom' requires custom_order")
            if sorted(self.custom_order) != list(range(self.nodes)):
                raise ValueError(
                    f"custom_order must be a permutation of 0..{self.nodes - 1}, "
                    f"got {len(self.custom_order)} entries"
                )
        elif self.order == "serpentine" and self.nodes % self.rows != 0:
            raise ValueError(
                f"serpentine layout needs nodes ({self.nodes}) divisible by rows ({self.rows})"
            )
        return self


class WebSettings(_StrictSection):
    """HTTP interface."""

    host: str = "0.0.0.0"  # a LAN appliance binding all interfaces is the point
    """V1 bound loopback only, so the UI was unreachable from any other device."""

    port: int = Field(default=8080, ge=1, le=65535)
    enabled: bool = True


class IngestSettings(_StrictSection):
    """Polling and push behaviour, shared across providers.

    Per-provider floors still win: a provider whose ``min_poll_interval`` is
    higher than ``base_poll_interval`` is polled at its own rate.
    """

    base_poll_interval: float = Field(default=30.0, ge=1.0)
    max_poll_interval: float = Field(default=300.0, ge=1.0)
    """Ceiling for the adaptive back-off on long-offline channels."""

    reconcile_interval: float = Field(default=600.0, ge=1.0)
    """Slow correctness poll for channels already covered by a push connection."""

    jitter: float = Field(default=0.15, ge=0.0, le=1.0)
    """Fraction of the interval added randomly, so N channels never stampede."""

    request_timeout: float = Field(default=10.0, gt=0)

    @model_validator(mode="after")
    def _check_bounds(self) -> IngestSettings:
        if self.max_poll_interval < self.base_poll_interval:
            raise ValueError("max_poll_interval must be >= base_poll_interval")
        return self


class Settings(BaseSettings):
    """Root configuration object."""

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_nested_delimiter="__",
        env_file=".env",
        extra="forbid",
    )

    data_dir: Path = Path("/var/lib/livepulse")
    """Database and any runtime state. Overridden to a tmpdir in tests."""

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = False
    """Structured JSON logs. Off by default because journald reads better as text."""

    leds: LedSettings = Field(default_factory=LedSettings)
    layout: LayoutSettings = Field(default_factory=LayoutSettings)
    web: WebSettings = Field(default_factory=WebSettings)
    ingest: IngestSettings = Field(default_factory=IngestSettings)

    @field_validator("data_dir")
    @classmethod
    def _expand(cls, value: Path) -> Path:
        return value.expanduser()

    @property
    def db_path(self) -> Path:
        return self.data_dir / "livepulse.db"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],  # noqa: ARG003 - required by the framework signature
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Environment beats the config file.

        pydantic-settings defaults to init-args-first; we invert that because
        ``init_settings`` is how the TOML file is injected, and an operator
        setting an env var on the systemd unit should not be overridden by a
        stale file on disk.
        """
        return (env_settings, dotenv_settings, init_settings, file_secret_settings)


def provider_secrets(provider_id: str, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Credentials for one provider, read from the environment.

    ``LIVEPULSE_SECRET_TWITCH_CLIENT_ID`` becomes ``{"client_id": ...}``.
    Deliberately not part of :class:`Settings`: a value that is never in the
    settings object cannot be leaked by something that dumps the settings.
    """
    env = os.environ if environ is None else environ
    prefix = f"{ENV_SECRET_PREFIX}{provider_id.upper()}_"

    return {
        key[len(prefix) :].lower(): value
        for key, value in env.items()
        if key.startswith(prefix) and value
    }


def default_config_path() -> Path | None:
    """First config file that exists, or ``None``.

    ``$LIVEPULSE_CONFIG`` → ``./livepulse.toml`` → ``/etc/livepulse/config.toml``.
    """
    override = os.environ.get(ENV_CONFIG_PATH)
    if override:
        return Path(override).expanduser()

    for candidate in (Path("livepulse.toml"), Path("/etc/livepulse/config.toml")):
        if candidate.is_file():
            return candidate
    return None


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc


def load_settings(config_path: Path | None = None, **overrides: Any) -> Settings:
    """Build settings from file, environment, and explicit overrides.

    Args:
        config_path: TOML file to read. When omitted, :func:`default_config_path`
            is consulted; when that finds nothing, defaults and environment are used.
        overrides: Applied last. Intended for tests and CLI flags.

    Raises:
        ConfigError: the file is unreadable, malformed, or fails validation.
    """
    path = config_path if config_path is not None else default_config_path()
    data: dict[str, Any] = _read_toml(path) if path is not None else {}
    data.update(overrides)

    try:
        return Settings(**data)
    except ValueError as exc:
        source = f" (from {path})" if path else ""
        raise ConfigError(f"invalid configuration{source}: {exc}") from exc
