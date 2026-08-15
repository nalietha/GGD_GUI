"""Shared fixtures.

Nothing here touches hardware, the network, or a real config file. Every test
in the suite must run on a laptop with neither GPIO nor internet.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from livepulse.config.settings import Settings
from livepulse.core.bus import EventBus
from livepulse.core.clock import ManualClock


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop the host's environment leaking into settings tests.

    Without this, a developer with ``LIVEPULSE_LOG_LEVEL`` exported gets
    different results from CI — the exact class of environment-dependent
    behaviour that made V1 hard to reason about.
    """
    for key in list(__import__("os").environ):
        if key.startswith("LIVEPULSE_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Default settings pointed at a throwaway data directory."""
    return Settings(data_dir=tmp_path)


@pytest.fixture
def config_file(tmp_path: Path) -> Iterator[Path]:
    """A writable config path; the test writes the content it needs."""
    path = tmp_path / "livepulse.toml"
    yield path
