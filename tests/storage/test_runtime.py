"""Runtime settings store.

These are the regression tests for V1's headline failure: settings the user
changed in the web UI never reached the LED process, because the two sides read
and wrote different files under different key names.
"""

from __future__ import annotations

import pytest

from livepulse.core.bus import EventBus
from livepulse.core.errors import StorageError
from livepulse.core.events import DisplayModeChanged, Event, SettingChanged
from livepulse.core.models import DisplayMode
from livepulse.storage.repositories import Repositories
from livepulse.storage.runtime import RuntimeSettings, RuntimeSettingsStore


class TestDefaults:
    def test_are_usable_before_any_load(self, repos: Repositories) -> None:
        """The renderer must have something to read from the first frame."""
        store = RuntimeSettingsStore(repos.settings)
        assert store.current.mode is DisplayMode.STREAMER
        assert store.current.brightness == 64

    async def test_load_on_an_empty_database_gives_defaults(self, repos: Repositories) -> None:
        store = RuntimeSettingsStore(repos.settings)
        assert await store.load() == RuntimeSettings()


class TestPersistence:
    async def test_a_write_is_visible_to_a_fresh_store(self, repos: Repositories) -> None:
        """The V1 bug, inverted into a guarantee: what the UI writes, the renderer reads.

        V1 wrote appsettings.json camelCased and read it case-sensitively, so
        every setting reverted to its default on the next read.
        """
        writer = RuntimeSettingsStore(repos.settings)
        await writer.load()
        await writer.update(brightness=200, mode=DisplayMode.AMBIENT)

        reader = RuntimeSettingsStore(repos.settings)
        loaded = await reader.load()

        assert loaded.brightness == 200
        assert loaded.mode is DisplayMode.AMBIENT

    async def test_update_returns_the_full_settings(
        self, settings_store: RuntimeSettingsStore
    ) -> None:
        updated = await settings_store.update(brightness=10)
        assert updated.brightness == 10
        assert updated.mode is DisplayMode.STREAMER, "untouched fields are preserved"

    async def test_the_cache_updates_immediately(
        self, settings_store: RuntimeSettingsStore
    ) -> None:
        await settings_store.update(brightness=99)
        assert settings_store.current.brightness == 99

    async def test_partial_updates_do_not_reset_other_fields(self, repos: Repositories) -> None:
        """V1's Settings page wrote every field from possibly-unbound form values,
        so editing brightness silently switched streamer mode off."""
        store = RuntimeSettingsStore(repos.settings)
        await store.load()
        await store.update(mode=DisplayMode.AMBIENT)
        await store.update(brightness=5)

        assert store.current.mode is DisplayMode.AMBIENT


class TestValidation:
    @pytest.mark.parametrize("value", [-1, 256, 1000])
    async def test_out_of_range_brightness_is_refused(
        self, settings_store: RuntimeSettingsStore, value: int
    ) -> None:
        with pytest.raises(StorageError, match="invalid setting value"):
            await settings_store.update(brightness=value)

    async def test_a_rejected_write_leaves_the_cache_untouched(
        self, settings_store: RuntimeSettingsStore
    ) -> None:
        before = settings_store.current
        with pytest.raises(StorageError):
            await settings_store.update(brightness=9999)
        assert settings_store.current == before

    async def test_an_unknown_key_is_refused_and_names_the_valid_ones(
        self, settings_store: RuntimeSettingsStore
    ) -> None:
        """A typo must fail loudly — V1's silently did nothing for months."""
        with pytest.raises(StorageError, match="unknown setting"):
            await settings_store.update(brightnes=10)

    async def test_a_stale_stored_value_does_not_prevent_startup(self, repos: Repositories) -> None:
        """A headless appliance must boot even with a setting it no longer understands."""
        await repos.settings.set("brightness", 100_000)

        store = RuntimeSettingsStore(repos.settings)
        loaded = await store.load()

        assert loaded == RuntimeSettings(), "falls back to defaults rather than failing"

    async def test_unrecognised_stored_keys_are_ignored(self, repos: Repositories) -> None:
        await repos.settings.set("from_an_older_version", "whatever")
        await repos.settings.set("brightness", 12)

        store = RuntimeSettingsStore(repos.settings)
        assert (await store.load()).brightness == 12


class TestEvents:
    async def test_a_change_is_announced(self, repos: Repositories, bus: EventBus) -> None:
        """Nothing has to poll a file to notice — V1's LED loop re-read JSON every tick."""
        store = RuntimeSettingsStore(repos.settings, bus)
        await store.load()

        with bus.subscribe(SettingChanged) as events:
            await store.update(brightness=123)
            received = await _first(events)

        assert received.key == "brightness"

    async def test_a_mode_change_gets_its_own_event(
        self, repos: Repositories, bus: EventBus
    ) -> None:
        store = RuntimeSettingsStore(repos.settings, bus)
        await store.load()

        with bus.subscribe(DisplayModeChanged) as events:
            await store.update(mode=DisplayMode.OFF)
            received = await _first(events)

        assert received.mode is DisplayMode.OFF

    async def test_setting_the_same_mode_again_publishes_no_mode_event(
        self, repos: Repositories, bus: EventBus
    ) -> None:
        store = RuntimeSettingsStore(repos.settings, bus)
        await store.load()

        with bus.subscribe(DisplayModeChanged) as events:
            await store.update(mode=DisplayMode.STREAMER)
            assert events._queue.empty()

    async def test_works_without_a_bus(self, repos: Repositories) -> None:
        """The importer and the CLI use the store with no bus attached."""
        store = RuntimeSettingsStore(repos.settings)
        await store.load()
        assert (await store.update(brightness=1)).brightness == 1


async def _first(subscription: object) -> Event:
    async for event in subscription:  # type: ignore[attr-defined]
        return event  # type: ignore[no-any-return]
    raise AssertionError("no event was published")
