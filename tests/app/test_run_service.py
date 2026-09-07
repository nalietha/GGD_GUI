"""The composition root, end to end.

These run the real service — real database file, real scheduler, real render
thread — with the simulated LED backend and the manual provider, so the whole
process tree is exercised exactly as it runs on a Pi, minus hardware and
network. What V1 could never do: prove the system works before deploying it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from livepulse.app import run_service
from livepulse.config.settings import LayoutSettings, LedSettings, Settings
from livepulse.core.models import Channel, LiveState, StateSource
from livepulse.storage.db import Database
from livepulse.storage.repositories import Repositories

STARTUP_TIMEOUT = 5.0


@pytest.fixture
def service_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        leds=LedSettings(backend="sim"),
        layout=LayoutSettings(nodes=4, leds_per_node=1, order="linear", rows=1),
    )


async def test_starts_and_stops_cleanly(service_settings: Settings) -> None:
    started = asyncio.Event()
    stop = asyncio.Event()
    task = asyncio.create_task(run_service(service_settings, started=started, stop=stop))

    await asyncio.wait_for(started.wait(), timeout=STARTUP_TIMEOUT)
    stop.set()
    await asyncio.wait_for(task, timeout=STARTUP_TIMEOUT)

    # The database was created and migrated on the way up.
    assert service_settings.db_path.exists()


async def test_ingests_a_bound_channel_end_to_end(service_settings: Settings) -> None:
    """A configured channel is polled and its observation persisted.

    The manual provider reports offline for a never-set channel — and an
    explicit offline row proves the whole path ran: scheduler → provider →
    coordinator → database. 'Observed offline' versus 'never checked' is the
    distinction V1 lost.
    """
    async with Database(service_settings.db_path) as db:
        repos = Repositories(db)
        await repos.nodes.ensure(4)
        channel = Channel(provider="manual", platform_ref="alice", handle="alice")
        await repos.channels.add(channel)
        await repos.nodes.bind(0, channel.id)

    started = asyncio.Event()
    stop = asyncio.Event()
    task = asyncio.create_task(run_service(service_settings, started=started, stop=stop))

    try:
        await asyncio.wait_for(started.wait(), timeout=STARTUP_TIMEOUT)

        async def observed() -> LiveState:
            while True:
                # A second connection, like the CLI would open; WAL allows it.
                async with Database(service_settings.db_path) as db:
                    state = await Repositories(db).live_state.get(channel.id)
                if state is not None:
                    return state
                await asyncio.sleep(0.05)

        state = await asyncio.wait_for(observed(), timeout=STARTUP_TIMEOUT)
        assert state.is_live is False
        assert state.source is StateSource.MANUAL
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=STARTUP_TIMEOUT)


async def test_missing_nodes_are_created_at_startup(service_settings: Settings) -> None:
    started = asyncio.Event()
    stop = asyncio.Event()
    task = asyncio.create_task(run_service(service_settings, started=started, stop=stop))

    try:
        await asyncio.wait_for(started.wait(), timeout=STARTUP_TIMEOUT)
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=STARTUP_TIMEOUT)

    async with Database(service_settings.db_path) as db:
        nodes = await Repositories(db).nodes.list()
    assert [node.id for node in nodes] == [0, 1, 2, 3]
