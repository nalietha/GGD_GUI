"""Raspberry Pi detection.

Paths are injected, so a laptop can pretend to be a Pi 5, a Pi 3, or nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from livepulse.hardware import pi_generation, pi_model, spi_devices, supports_pwm_ws281x


def model_file(tmp_path: Path, text: str) -> Path:
    """Write a device-tree model file, including the trailing NUL the kernel adds."""
    path = tmp_path / "model"
    path.write_bytes(text.encode() + b"\x00")
    return path


class TestPiModel:
    def test_reads_and_strips_the_trailing_nul(self, tmp_path: Path) -> None:
        source = model_file(tmp_path, "Raspberry Pi 5 Model B Rev 1.0")
        assert pi_model(source) == "Raspberry Pi 5 Model B Rev 1.0"

    def test_missing_file_means_not_a_pi(self, tmp_path: Path) -> None:
        assert pi_model(tmp_path / "absent") is None

    def test_a_directory_does_not_raise(self, tmp_path: Path) -> None:
        assert pi_model(tmp_path) is None

    def test_empty_file_means_unknown(self, tmp_path: Path) -> None:
        assert pi_model(model_file(tmp_path, "")) is None


class TestPiGeneration:
    @pytest.mark.parametrize(
        ("model", "expected"),
        [
            ("Raspberry Pi 5 Model B Rev 1.0", 5),
            ("Raspberry Pi 4 Model B Rev 1.4", 4),
            ("Raspberry Pi 3 Model B Plus Rev 1.3", 3),
        ],
    )
    def test_parses_numbered_generations(self, tmp_path: Path, model: str, expected: int) -> None:
        assert pi_generation(model_file(tmp_path, model)) == expected

    @pytest.mark.parametrize(
        "model",
        [
            "Raspberry Pi Zero 2 W Rev 1.0",  # the 2 is a revision, not a generation
            "Raspberry Pi Compute Module 4 Rev 1.1",
            "Raspberry Pi Model B Plus Rev 1.2",
        ],
    )
    def test_unnumbered_models_are_unknown(self, tmp_path: Path, model: str) -> None:
        """Unknown must never be mistaken for a generation number."""
        assert pi_generation(model_file(tmp_path, model)) is None

    def test_not_a_pi_is_unknown(self, tmp_path: Path) -> None:
        assert pi_generation(tmp_path / "absent") is None


class TestPwmSupport:
    def test_pi_5_is_rejected(self, tmp_path: Path) -> None:
        """rpi_ws281x cannot reach the PWM peripherals behind RP1.

        Reporting it as available would turn a diagnosable error into silent
        darkness — the worst possible failure for a light.
        """
        assert supports_pwm_ws281x(model_file(tmp_path, "Raspberry Pi 5 Model B")) is False

    @pytest.mark.parametrize("model", ["Raspberry Pi 4 Model B", "Raspberry Pi 3 Model B"])
    def test_older_pis_are_accepted(self, tmp_path: Path, model: str) -> None:
        assert supports_pwm_ws281x(model_file(tmp_path, model)) is True

    def test_unknown_hardware_is_allowed_through(self, tmp_path: Path) -> None:
        """Zero 2 W works, and refusing to run on a board we merely failed to
        recognise would be worse than letting the driver report a real error."""
        assert supports_pwm_ws281x(model_file(tmp_path, "Raspberry Pi Zero 2 W")) is True
        assert supports_pwm_ws281x(tmp_path / "absent") is True


class TestSpiDevices:
    def test_finds_device_nodes(self, tmp_path: Path) -> None:
        (tmp_path / "spidev0.0").touch()
        (tmp_path / "spidev0.1").touch()
        assert [p.name for p in spi_devices(tmp_path)] == ["spidev0.0", "spidev0.1"]

    def test_none_when_spi_is_disabled(self, tmp_path: Path) -> None:
        """No device node means SPI is off in config.txt, so the strip is not on GPIO10."""
        (tmp_path / "ttyS0").touch()
        assert spi_devices(tmp_path) == []

    def test_missing_directory_does_not_raise(self, tmp_path: Path) -> None:
        assert spi_devices(tmp_path / "absent") == []
