"""Runtime settings — the ones the user changes while it is running.

The single read path for brightness and display mode, and the direct answer to
V1's worst failure: the web app wrote ``appsettings.json`` in one place under
one casing, and the LED process read a *different* ``appsettings.json`` under a
different key, so nothing the user toggled ever reached the lights.

Here there is one store, held in memory and backed by one table. The renderer
reads the cached value every frame without touching the database; a write
updates the cache and the database together and announces the change on the
bus, so nothing has to poll a file to notice.
"""

from __future__ import annotations

from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from livepulse.core.bus import EventBus
from livepulse.core.errors import StorageError
from livepulse.core.events import DisplayModeChanged, SettingChanged
from livepulse.core.models import DisplayMode
from livepulse.storage.repositories import SettingsRepository

__all__ = ["RuntimeSettings", "RuntimeSettingsStore"]

log = structlog.get_logger(__name__)


class RuntimeSettings(BaseModel):
    """User-adjustable state.

    Anything here is editable from the web UI. Anything that needs a restart or
    holds a credential belongs in ``config.toml`` or the secrets file instead.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    brightness: int = Field(default=64, ge=0, le=255)
    """Master scale applied by the compositor, above the driver."""

    mode: DisplayMode = DisplayMode.STREAMER

    ambient_effect: str = "gradient"
    """What runs when no bound channel is live, and in AMBIENT mode."""

    ambient_enabled: bool = True
    """When false, no live channels means dark rather than the ambient effect."""


class RuntimeSettingsStore:
    """Cached, validated access to :class:`RuntimeSettings`.

    Load once at startup, then read :attr:`current` freely — it is a plain
    attribute read, safe to call at 60 fps.
    """

    __slots__ = ("_bus", "_current", "_repo")

    def __init__(self, repo: SettingsRepository, bus: EventBus | None = None) -> None:
        self._repo = repo
        self._bus = bus
        self._current = RuntimeSettings()

    @property
    def current(self) -> RuntimeSettings:
        """The cached settings. Never blocks, never raises."""
        return self._current

    async def load(self) -> RuntimeSettings:
        """Read from the database into the cache.

        A stored value that no longer validates — an effect that was renamed,
        say — is dropped with a warning and its default used, rather than
        preventing startup. Refusing to boot because one setting went stale
        would leave a headless appliance dark with no way in.
        """
        stored = await self._repo.all()
        known = {key: value for key, value in stored.items() if key in RuntimeSettings.model_fields}

        try:
            self._current = RuntimeSettings(**known)
        except ValidationError as exc:
            log.warning("stored settings failed validation, using defaults", errors=exc.errors())
            self._current = RuntimeSettings()

        # Keys starting with "_" are internal state other services keep in the
        # same table (the schedule's boundary memory, say) — expected, not a
        # misconfiguration worth a warning.
        unknown = {
            key
            for key in set(stored) - set(RuntimeSettings.model_fields)
            if not key.startswith("_")
        }
        if unknown:
            log.warning("ignoring unrecognised settings", keys=sorted(unknown))

        return self._current

    async def update(self, **changes: Any) -> RuntimeSettings:
        """Validate, persist, cache, and announce a change.

        Args:
            changes: Field names and new values.

        Returns:
            The full settings after the change.

        Raises:
            StorageError: an unknown field, or a value that fails validation.
        """
        unknown = set(changes) - set(RuntimeSettings.model_fields)
        if unknown:
            raise StorageError(
                f"unknown setting(s): {', '.join(sorted(unknown))}; "
                f"expected one of {', '.join(sorted(RuntimeSettings.model_fields))}"
            )

        # Merge onto a dump of the *current* (already valid) settings and validate
        # the result. model_copy(update=...) would write the raw value onto the
        # frozen model without validating it, so a plain string where an enum
        # belongs — exactly what the V1 importer supplies — would survive as a
        # string and only surface later, during serialisation.
        try:
            updated = RuntimeSettings.model_validate({**self._current.model_dump(), **changes})
        except ValidationError as exc:
            raise StorageError(f"invalid setting value: {exc}") from exc

        previous = self._current
        for key in changes:
            stored = getattr(updated, key)
            await self._repo.set(key, stored.value if isinstance(stored, DisplayMode) else stored)

        self._current = updated
        self._announce(previous, updated, changes)
        return updated

    def _announce(
        self,
        previous: RuntimeSettings,
        updated: RuntimeSettings,
        changes: dict[str, Any],
    ) -> None:
        if self._bus is None:
            return
        for key in changes:
            self._bus.publish(SettingChanged(key=key))
        # A mode change is worth its own event: the renderer reacts to it
        # differently from a brightness tweak, and should not have to diff.
        if updated.mode is not previous.mode:
            self._bus.publish(DisplayModeChanged(mode=updated.mode))
