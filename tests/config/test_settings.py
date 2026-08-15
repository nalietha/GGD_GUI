"""Configuration layering and validation.

The V1 bugs these guard against: settings written in one casing and read in
another, a layout transform that disagreed with the wiring, and config keys
that silently resolved to a default when the section had been renamed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from livepulse.config.settings import LayoutSettings, Settings, load_settings
from livepulse.core.errors import ConfigError


class TestLayering:
    def test_defaults_apply_with_no_file(self) -> None:
        settings = load_settings(None)
        assert settings.layout.nodes == 16
        assert settings.leds.fps == 60

    def test_file_overrides_defaults(self, config_file: Path) -> None:
        config_file.write_text("[layout]\nnodes = 24\nrows = 3\n")
        assert load_settings(config_file).layout.nodes == 24

    def test_environment_beats_file(
        self, config_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An operator setting the systemd unit's environment must win.

        pydantic-settings defaults the other way; we invert it deliberately.
        """
        config_file.write_text('[leds]\nbackend = "ws281x"\n')
        monkeypatch.setenv("LIVEPULSE_LEDS__BACKEND", "sim")
        assert load_settings(config_file).leds.backend == "sim"

    def test_nested_env_delimiter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LIVEPULSE_WEB__PORT", "9999")
        assert load_settings(None).web.port == 9999

    def test_overrides_beat_the_file(self, config_file: Path) -> None:
        config_file.write_text('log_level = "ERROR"\n')
        assert load_settings(config_file, log_level="DEBUG").log_level == "DEBUG"


class TestErrors:
    def test_missing_file_is_a_config_error(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="not found"):
            load_settings(tmp_path / "absent.toml")

    def test_malformed_toml_is_a_config_error(self, config_file: Path) -> None:
        config_file.write_text("[layout\nnodes = ")
        with pytest.raises(ConfigError, match="invalid TOML"):
            load_settings(config_file)

    def test_unknown_key_is_rejected(self, config_file: Path) -> None:
        """Typos must fail loudly.

        V1 read ``GGD_Display.StreamerEnabled`` after the section had been
        renamed to ``GGDDisplay.StreamerModeEnabled``; the miss silently
        returned the default, so streamer mode could never be turned off.
        """
        config_file.write_text("[layout]\nnodez = 16\n")
        with pytest.raises(ConfigError, match="invalid configuration"):
            load_settings(config_file)

    def test_invalid_value_is_rejected(self, config_file: Path) -> None:
        config_file.write_text("[web]\nport = 70000\n")
        with pytest.raises(ConfigError, match="invalid configuration"):
            load_settings(config_file)


class TestLayoutSettings:
    def test_pixel_count_is_derived(self) -> None:
        assert LayoutSettings(nodes=16, leds_per_node=7).pixel_count == 112

    def test_serpentine_requires_divisible_rows(self) -> None:
        with pytest.raises(ValueError, match="divisible by rows"):
            LayoutSettings(nodes=15, rows=2, order="serpentine")

    def test_custom_order_requires_a_permutation(self) -> None:
        with pytest.raises(ValueError, match="permutation"):
            LayoutSettings(nodes=4, order="custom", custom_order=[0, 1, 2, 2])

    def test_custom_order_accepts_a_permutation(self) -> None:
        layout = LayoutSettings(nodes=4, order="custom", custom_order=[3, 2, 1, 0])
        assert layout.custom_order == [3, 2, 1, 0]

    def test_custom_order_is_required_when_selected(self) -> None:
        with pytest.raises(ValueError, match="requires custom_order"):
            LayoutSettings(nodes=4, order="custom")


class TestIngestSettings:
    def test_max_interval_must_not_be_below_base(self, config_file: Path) -> None:
        config_file.write_text("[ingest]\nbase_poll_interval = 60\nmax_poll_interval = 30\n")
        with pytest.raises(ConfigError, match="max_poll_interval"):
            load_settings(config_file)


class TestPaths:
    def test_db_path_derives_from_data_dir(self, tmp_path: Path) -> None:
        assert Settings(data_dir=tmp_path).db_path == tmp_path / "livepulse.db"

    def test_data_dir_expands_user(self) -> None:
        assert not str(Settings(data_dir=Path("~/lp")).data_dir).startswith("~")
