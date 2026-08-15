"""Storage fixtures.

Tests run against a real ``:memory:`` SQLite database rather than a hand-written
fake. It is faster to create than a fake is to maintain, and it exercises the
actual schema — foreign keys, unique constraints, ``ON DELETE SET NULL`` — which
a fake would only pretend to have.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from livepulse.core.bus import EventBus
from livepulse.core.models import Channel
from livepulse.storage.db import IN_MEMORY, Database
from livepulse.storage.repositories import Repositories
from livepulse.storage.runtime import RuntimeSettingsStore


@pytest.fixture
async def db() -> AsyncIterator[Database]:
    """A migrated, empty database."""
    async with Database(IN_MEMORY) as database:
        yield database


@pytest.fixture
def repos(db: Database) -> Repositories:
    return Repositories(db)


@pytest.fixture
async def settings_store(repos: Repositories, bus: EventBus) -> RuntimeSettingsStore:
    store = RuntimeSettingsStore(repos.settings, bus)
    await store.load()
    return store


@pytest.fixture
def channel() -> Channel:
    """A representative channel. Values mirror a real entry from V1's save.json."""
    return Channel(
        provider="twitch",
        platform_ref="175831187",
        handle="IronMouse",
        color="#e100ff",
    )
