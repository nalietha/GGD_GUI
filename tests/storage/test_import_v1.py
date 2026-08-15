"""V1 import.

The tests marked *real file* run against the actual ``save.json`` committed in
this repository, not a tidy fixture. It contains empty ``ColorHex`` strings,
empty ``DisplaySetting`` strings, null display names, and channels no node
references — every one of which V1 either crashed on or silently ignored.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from livepulse.core.errors import StorageError
from livepulse.core.models import DEFAULT_COLOR, DisplayMode
from livepulse.storage.import_v1 import apply_plan, build_plan, read_v1_files
from livepulse.storage.repositories import Repositories
from livepulse.storage.runtime import RuntimeSettingsStore

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_SAVE = REPO_ROOT / "GGD_Display" / "wwwroot" / "data" / "save.json"
REAL_APPSETTINGS = REPO_ROOT / "GGD_Display" / "appsettings.json"

IRONMOUSE_ID = "15b94807-02e6-4a26-bd89-8c5be697301e"
NYANNERS_ID = "b0df0b6d-2aef-4d52-b2f5-43ce4c00aefa"


def save_file(**overrides: Any) -> dict[str, Any]:
    """A minimal V1 save file, shaped exactly like the real one."""
    base: dict[str, Any] = {
        "metadata": {"Version": "0.0.4.3a"},
        "nodes": [
            {
                "NodeId": 0,
                "ColorHex": "#112233",
                "LinkedStreamerId": IRONMOUSE_ID,
                "DisplaySetting": "static",
            }
        ],
        "streamers": [
            {
                "privateId": IRONMOUSE_ID,
                "publicStreamerId": "175831187",
                "name": "IronMouse",
                "displayName": None,
                "platform": "Twitch",
                "streamerColor": "#e100ff",
                "altColorHex": None,
                "isLive": True,
            }
        ],
    }
    base.update(overrides)
    return base


class TestChannelMapping:
    def test_preserves_the_private_id_as_the_channel_id(self) -> None:
        """The single most important guarantee: existing node bindings survive."""
        plan = build_plan(save_file())
        assert plan.channels[0].id == UUID(IRONMOUSE_ID)

    def test_maps_the_platform_reference_and_provider(self) -> None:
        channel = build_plan(save_file()).channels[0]
        assert channel.provider == "twitch"
        assert channel.platform_ref == "175831187"

    def test_builds_a_channel_url(self) -> None:
        """V1 hardcoded https://twitch.tv/{name} in JavaScript, with a TODO."""
        assert build_plan(save_file()).channels[0].url == "https://twitch.tv/IronMouse"

    def test_null_display_name_becomes_none(self) -> None:
        assert build_plan(save_file()).channels[0].display_name is None

    def test_a_streamer_without_a_name_is_skipped_with_a_warning(self) -> None:
        data = save_file(streamers=[{"privateId": IRONMOUSE_ID, "name": ""}])
        plan = build_plan(data)
        assert plan.channels == []
        assert any("no name" in w for w in plan.warnings)

    def test_a_streamer_without_a_platform_id_is_kept_as_unresolved(self) -> None:
        """V1 left these unusable. Keep the channel; a provider can resolve it."""
        data = save_file(
            streamers=[{"privateId": IRONMOUSE_ID, "name": "Someone", "publicStreamerId": ""}]
        )
        plan = build_plan(data)
        assert plan.channels[0].platform_ref == "unresolved:someone"
        assert any("unresolved" in w for w in plan.warnings)

    def test_duplicates_are_dropped_with_a_warning(self) -> None:
        streamer = save_file()["streamers"][0]
        plan = build_plan(save_file(streamers=[streamer, dict(streamer)]))
        assert len(plan.channels) == 1
        assert any("duplicates" in w for w in plan.warnings)

    def test_an_unknown_platform_is_kept_rather_than_lost(self) -> None:
        data = save_file(streamers=[{**save_file()["streamers"][0], "platform": "Owncast"}])
        assert build_plan(data).channels[0].provider == "owncast"


class TestColourHandling:
    def test_empty_colour_falls_back_instead_of_crashing(self) -> None:
        """V1's hex_to_color("") raised. The deployed save.json has two of these."""
        data = save_file(nodes=[{"NodeId": 0, "ColorHex": "", "LinkedStreamerId": ""}])
        assert build_plan(data).nodes[0].color == DEFAULT_COLOR

    def test_invalid_colour_falls_back_and_warns(self) -> None:
        data = save_file(nodes=[{"NodeId": 0, "ColorHex": "not-a-colour"}])
        plan = build_plan(data)
        assert plan.nodes[0].color == DEFAULT_COLOR
        assert any("invalid colour" in w for w in plan.warnings)

    def test_missing_alt_colour_stays_none(self) -> None:
        assert build_plan(save_file()).channels[0].alt_color is None


class TestNodeMapping:
    def test_binds_the_node_to_its_channel(self) -> None:
        assert build_plan(save_file()).nodes[0].channel_id == UUID(IRONMOUSE_ID)

    def test_empty_display_setting_becomes_static(self) -> None:
        """V1 treated "" as static in one place and crashed on it in another."""
        data = save_file(nodes=[{"NodeId": 0, "ColorHex": "#fff", "DisplaySetting": ""}])
        assert build_plan(data).nodes[0].effect == "static"

    def test_unknown_display_setting_warns_and_defaults(self) -> None:
        data = save_file(nodes=[{"NodeId": 0, "DisplaySetting": "kaleidoscope"}])
        plan = build_plan(data)
        assert plan.nodes[0].effect == "static"
        assert any("kaleidoscope" in w for w in plan.warnings)

    def test_a_dangling_binding_is_imported_unbound(self) -> None:
        """V1 skipped these nodes entirely: never driven, never cleared."""
        data = save_file(nodes=[{"NodeId": 0, "LinkedStreamerId": "does-not-exist"}])
        plan = build_plan(data)
        assert plan.nodes[0].channel_id is None
        assert any("unknown streamer" in w for w in plan.warnings)

    def test_an_unbound_node_is_still_imported(self) -> None:
        data = save_file(nodes=[{"NodeId": 5, "LinkedStreamerId": ""}])
        assert build_plan(data).nodes[0].id == 5

    def test_a_malformed_node_id_is_skipped(self) -> None:
        plan = build_plan(save_file(nodes=[{"NodeId": "three"}]))
        assert plan.nodes == []
        assert any("invalid NodeId" in w for w in plan.warnings)


class TestSettingsMapping:
    def test_streamer_mode_true_becomes_streamer(self) -> None:
        """Resolves V1's three incompatible spellings of 'mode' into one."""
        app = {"GGDDisplay": {"StreamerModeEnabled": True, "Brightness": 32}}
        plan = build_plan(save_file(), app)
        assert plan.settings == {"brightness": 32, "mode": DisplayMode.STREAMER.value}

    def test_streamer_mode_false_becomes_ambient(self) -> None:
        app = {"GGDDisplay": {"StreamerModeEnabled": False}}
        assert build_plan(save_file(), app).settings["mode"] == DisplayMode.AMBIENT.value

    def test_reads_the_pre_rename_section_too(self) -> None:
        """Commit c4158bd renamed GGD_Display to GGDDisplay; accept both."""
        app = {"GGD_Display": {"Brightness": 99}}
        assert build_plan(save_file(), app).settings["brightness"] == 99

    def test_out_of_range_brightness_is_rejected_with_a_warning(self) -> None:
        app = {"GGDDisplay": {"Brightness": 9000}}
        plan = build_plan(save_file(), app)
        assert "brightness" not in plan.settings
        assert any("Brightness" in w for w in plan.warnings)

    def test_adult_check_flag_explains_what_replaced_it(self) -> None:
        app = {"GGDDisplay": {"AdultContentCheckEnabled": True}}
        plan = build_plan(save_file(), app)
        assert any("generic HTTP provider" in w for w in plan.warnings)


class TestReadingFiles:
    def test_missing_save_file_is_a_clear_error(self, tmp_path: Path) -> None:
        with pytest.raises(StorageError, match=r"save\.json not found"):
            read_v1_files(tmp_path / "absent.json")

    def test_malformed_json_is_a_clear_error(self, tmp_path: Path) -> None:
        path = tmp_path / "save.json"
        path.write_text("{not json")
        with pytest.raises(StorageError, match="not valid JSON"):
            read_v1_files(path)

    def test_tolerates_a_utf8_bom(self, tmp_path: Path) -> None:
        """V1's files were written by .NET, which emits a BOM."""
        path = tmp_path / "save.json"
        path.write_text(json.dumps(save_file()), encoding="utf-8-sig")
        save_data, _ = read_v1_files(path)
        assert save_data["streamers"][0]["name"] == "IronMouse"


class TestApply:
    async def test_writes_channels_nodes_and_settings(
        self, repos: Repositories, settings_store: RuntimeSettingsStore
    ) -> None:
        plan = build_plan(save_file(), {"GGDDisplay": {"Brightness": 40}})
        await apply_plan(plan, repos, settings_store)

        stored = await repos.channels.get(UUID(IRONMOUSE_ID))
        assert stored is not None
        assert stored.handle == "IronMouse"

        node = await repos.nodes.get(0)
        assert node is not None
        assert node.channel_id == UUID(IRONMOUSE_ID)
        assert settings_store.current.brightness == 40

    async def test_refuses_to_import_over_existing_data(
        self, repos: Repositories, channel: object
    ) -> None:
        """Importing twice would duplicate or clobber; make it an explicit refusal."""
        plan = build_plan(save_file())
        await apply_plan(plan, repos)

        with pytest.raises(StorageError, match="refusing to import"):
            await apply_plan(build_plan(save_file()), repos)

    async def test_creates_every_node_up_to_the_highest_id(self, repos: Repositories) -> None:
        data = save_file(nodes=[{"NodeId": 15, "ColorHex": "#ffffff"}])
        await apply_plan(build_plan(data), repos)
        assert len(await repos.nodes.list()) == 16


@pytest.mark.skipif(not REAL_SAVE.is_file(), reason="V1 save.json not present")
class TestAgainstTheRealV1Files:
    """Runs against the actual deployed configuration in this repository."""

    def test_imports_every_streamer_and_node(self) -> None:
        save_data, app_data = read_v1_files(REAL_SAVE, REAL_APPSETTINGS)
        plan = build_plan(save_data, app_data)

        assert len(plan.channels) == len(save_data["streamers"])
        assert len(plan.nodes) == len(save_data["nodes"])

    def test_every_binding_in_the_real_file_resolves(self) -> None:
        """Confirms no node loses its streamer in the move."""
        save_data, _ = read_v1_files(REAL_SAVE)
        plan = build_plan(save_data)

        expected = sum(1 for node in save_data["nodes"] if node.get("LinkedStreamerId"))
        assert sum(1 for node in plan.nodes if node.channel_id) == expected

    def test_the_empty_colours_in_the_real_file_are_handled(self) -> None:
        save_data, _ = read_v1_files(REAL_SAVE)
        blanks = [n for n in save_data["nodes"] if not (n.get("ColorHex") or "").strip()]
        assert blanks, "fixture assumption: the real file has blank ColorHex values"

        plan = build_plan(save_data)
        assert all(node.color for node in plan.nodes)

    def test_known_channels_survive_with_their_colours(self) -> None:
        save_data, _ = read_v1_files(REAL_SAVE)
        by_id = {channel.id: channel for channel in build_plan(save_data).channels}

        ironmouse = by_id[UUID(IRONMOUSE_ID)]
        assert ironmouse.handle == "IronMouse"
        assert ironmouse.color == "#e100ff"

        nyanners = by_id[UUID(NYANNERS_ID)]
        assert nyanners.alt_color == "#e00000"

    async def test_applies_cleanly(
        self, repos: Repositories, settings_store: RuntimeSettingsStore
    ) -> None:
        """End to end: the real V1 config lands in a real database."""
        save_data, app_data = read_v1_files(REAL_SAVE, REAL_APPSETTINGS)
        plan = build_plan(save_data, app_data)
        await apply_plan(plan, repos, settings_store)

        assert len(await repos.channels.list()) == len(save_data["streamers"])
        assert len(await repos.nodes.list()) == 16
        # Two nodes share JuniperActias in the real file.
        assert len(await repos.nodes.bound_channel_ids()) < 16
