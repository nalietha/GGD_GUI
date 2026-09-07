"""The coordinator: the glue between what platforms report and what the strip shows.

The central questions: does an observation actually reach the pixels, does a
restart resume quietly, and does a config edit take effect without a restart?
V1 failed all three — its settings never reached the LED process at all.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

import pytest

from livepulse.app import Coordinator
from livepulse.config.settings import IngestSettings, LayoutSettings
from livepulse.core.bus import EventBus
from livepulse.core.clock import ManualClock
from livepulse.core.events import ChannelStateChanged, NodeConfigChanged, SettingChanged
from livepulse.core.models import Channel, DisplayMode, LiveState, StateSource
from livepulse.ingest.scheduler import IngestScheduler
from livepulse.leds.color import RGB, GammaTable, parse_hex
from livepulse.leds.compositor import Compositor
from livepulse.leds.driver.sim import SimulatedDriver
from livepulse.leds.layout import Layout
from livepulse.leds.renderer import Renderer
from livepulse.providers.manual import ManualProvider
from livepulse.storage.db import Database
from livepulse.storage.repositories import Repositories
from livepulse.storage.runtime import RuntimeSettingsStore

BLACK = RGB(0, 0, 0)

#: One pixel per node, so frame[n] is node n — no layout arithmetic in asserts.
LAYOUT = LayoutSettings(nodes=4, leds_per_node=1, order="linear", rows=1)


@dataclass
class Env:
    """Everything a coordinator test needs, built over an in-memory database."""

    repos: Repositories
    bus: EventBus
    store: RuntimeSettingsStore
    clock: ManualClock
    scheduler: IngestScheduler
    renderer: Renderer
    driver: SimulatedDriver
    coordinator: Coordinator

    def frame(self) -> tuple[RGB, ...]:
        """Render past the master fade-up and return the transmitted frame."""
        self.renderer.tick()
        self.clock.advance(1.0)
        self.renderer.tick()
        return self.driver.frame

    async def add_channel(
        self, handle: str, *, color: str = "#ff0000", node: int | None = 0
    ) -> Channel:
        channel = Channel(provider="manual", platform_ref=handle, handle=handle, color=color)
        await self.repos.channels.add(channel)
        if node is not None:
            await self.repos.nodes.bind(node, channel.id)
        return channel

    def live(self, *, ago: float = 0.0) -> LiveState:
        return LiveState(
            is_live=True,
            source=StateSource.MANUAL,
            observed_at=self.clock.now() - timedelta(seconds=ago),
        )


@pytest.fixture
async def env() -> AsyncIterator[Env]:
    clock = ManualClock()
    async with Database(":memory:") as db:
        repos = Repositories(db)
        bus = EventBus()
        store = RuntimeSettingsStore(repos.settings, bus)
        await store.load()
        # Identity output: gamma 1.0 and full brightness make frame pixels
        # equal the configured colours, so asserts read as intent.
        await store.update(brightness=255)
        await repos.nodes.ensure(LAYOUT.nodes)

        layout = Layout(LAYOUT)
        driver = SimulatedDriver(layout.pixel_count)
        renderer = Renderer(driver, Compositor(layout, GammaTable(1.0)), fps=60, clock=clock)

        scheduler = IngestScheduler(bus, IngestSettings(), clock=clock)
        scheduler.register(ManualProvider())

        coordinator = Coordinator(
            bus, repos, store, renderer, scheduler, clock=clock, state_stale_after=900.0
        )
        yield Env(repos, bus, store, clock, scheduler, renderer, driver, coordinator)


class TestStartup:
    async def test_fresh_observation_lights_its_node(self, env: Env) -> None:
        channel = await env.add_channel("alice", color="#ff0000", node=0)
        await env.repos.live_state.record(channel.id, env.live())

        await env.coordinator.start()
        frame = env.frame()

        assert frame[0] == parse_hex("#ff0000")
        assert frame[1] == BLACK  # unbound nodes stay dark while someone is live
        assert env.scheduler.tracked_count == 1

    async def test_stale_observation_is_distrusted(self, env: Env) -> None:
        """State older than the threshold renders as 'never observed'.

        This is the quiet-hours resume rule: after eight hours off, whoever
        was live at 22:00 must not light up (or flash) on stored say-so — the
        immediate reconciliation poll establishes the truth instead.
        """
        fresh = await env.add_channel("alice", color="#ff0000", node=0)
        stale = await env.add_channel("bob", color="#00ff00", node=1)
        await env.repos.live_state.record(fresh.id, env.live())
        await env.repos.live_state.record(stale.id, env.live(ago=2 * 3600))

        await env.coordinator.start()
        frame = env.frame()

        assert frame[0] == parse_hex("#ff0000")
        assert frame[1] == BLACK
        # Still polled — distrusting the stored state is not untracking.
        assert env.scheduler.tracked_count == 2

    async def test_unbound_channel_is_not_tracked(self, env: Env) -> None:
        await env.add_channel("alice", node=None)

        await env.coordinator.start()

        assert env.scheduler.tracked_count == 0


class TestStateChanges:
    async def test_observation_persists_and_lights_the_node(self, env: Env) -> None:
        channel = await env.add_channel("alice", color="#ff0000", node=0)
        await env.coordinator.start()

        await env.coordinator.handle(
            ChannelStateChanged(channel_id=channel.id, current=env.live(), previous=None)
        )

        stored = await env.repos.live_state.get(channel.id)
        assert stored is not None and stored.is_live
        assert env.frame()[0] == parse_hex("#ff0000")

    async def test_going_live_fires_the_flash(self, env: Env) -> None:
        channel = await env.add_channel("alice", node=0)
        await env.coordinator.start()

        await env.coordinator.handle(
            ChannelStateChanged(
                channel_id=channel.id, current=env.live(), previous=LiveState.offline()
            )
        )

        assert env.renderer.compositor.active_transients == 1

    async def test_first_observation_does_not_flash(self, env: Env) -> None:
        """No previous state means no transition — a cold start settles silently."""
        channel = await env.add_channel("alice", node=0)
        await env.coordinator.start()

        await env.coordinator.handle(
            ChannelStateChanged(channel_id=channel.id, current=env.live(), previous=None)
        )

        assert env.renderer.compositor.active_transients == 0

    async def test_quiet_hours_track_state_but_stay_dark(self, env: Env) -> None:
        """OFF means dark-but-running: no flash, no light, nothing missed."""
        channel = await env.add_channel("alice", node=0)
        await env.coordinator.start()
        await env.store.update(mode=DisplayMode.OFF)

        await env.coordinator.handle(
            ChannelStateChanged(
                channel_id=channel.id, current=env.live(), previous=LiveState.offline()
            )
        )

        assert env.renderer.compositor.active_transients == 0
        assert all(pixel == BLACK for pixel in env.frame())
        stored = await env.repos.live_state.get(channel.id)
        assert stored is not None and stored.is_live  # correct the instant lights return

    async def test_unknown_channel_is_ignored(self, env: Env) -> None:
        """A poll that lands after its channel was deleted must not crash or write."""
        await env.coordinator.start()

        await env.coordinator.handle(
            ChannelStateChanged(channel_id=uuid4(), current=env.live(), previous=None)
        )

        assert await env.repos.live_state.all() == {}


class TestConfigChanges:
    async def test_new_binding_is_rendered_and_polled(self, env: Env) -> None:
        channel = await env.add_channel("alice", color="#0000ff", node=None)
        await env.coordinator.start()
        assert env.scheduler.tracked_count == 0

        await env.repos.nodes.bind(2, channel.id)
        await env.coordinator.handle(NodeConfigChanged(node_id=2))
        await env.coordinator.handle(
            ChannelStateChanged(channel_id=channel.id, current=env.live(), previous=None)
        )

        assert env.scheduler.tracked_count == 1
        assert env.frame()[2] == parse_hex("#0000ff")

    async def test_deleting_a_channel_stops_polling_it(self, env: Env) -> None:
        channel = await env.add_channel("alice", node=0)
        await env.coordinator.start()
        assert env.scheduler.tracked_count == 1

        await env.repos.channels.delete(channel.id)
        await env.coordinator.handle(NodeConfigChanged(node_id=0))

        assert env.scheduler.tracked_count == 0

    async def test_setting_change_takes_effect_without_restart(self, env: Env) -> None:
        channel = await env.add_channel("alice", color="#ff0000", node=0)
        await env.repos.live_state.record(channel.id, env.live())
        await env.coordinator.start()
        assert env.frame()[0] == parse_hex("#ff0000")

        await env.store.update(brightness=0)
        await env.coordinator.handle(SettingChanged(key="brightness"))

        assert env.frame()[0] == BLACK


class TestProviderProvisioning:
    """A platform is built the first time a channel actually needs it.

    This is what lets someone on a fresh install add a channel through the
    web UI and see it polled without restarting the service.
    """

    async def test_bound_channel_provisions_its_platform(self, env: Env) -> None:
        provisioned: list[str] = []

        async def ensure(provider_id: str) -> None:
            provisioned.append(provider_id)

        coordinator = Coordinator(
            env.bus,
            env.repos,
            env.store,
            env.renderer,
            env.scheduler,
            clock=env.clock,
            ensure_provider=ensure,
        )
        await env.add_channel("alice", node=0)
        await coordinator.start()

        assert provisioned == ["manual"]

    async def test_unbound_channel_provisions_nothing(self, env: Env) -> None:
        provisioned: list[str] = []

        async def ensure(provider_id: str) -> None:
            provisioned.append(provider_id)

        coordinator = Coordinator(
            env.bus,
            env.repos,
            env.store,
            env.renderer,
            env.scheduler,
            clock=env.clock,
            ensure_provider=ensure,
        )
        await env.add_channel("alice", node=None)
        await coordinator.start()

        assert provisioned == []


class TestRun:
    async def test_dispatches_events_from_the_bus(self, env: Env) -> None:
        channel = await env.add_channel("alice", node=0)
        await env.coordinator.start()
        task = asyncio.create_task(env.coordinator.run())

        try:
            # Let the task reach its subscription before publishing, or the
            # event would be delivered to nobody.
            await asyncio.sleep(0)
            env.bus.publish(
                ChannelStateChanged(channel_id=channel.id, current=env.live(), previous=None)
            )

            async def observed() -> LiveState:
                while True:
                    state = await env.repos.live_state.get(channel.id)
                    if state is not None:
                        return state
                    await asyncio.sleep(0.01)

            state = await asyncio.wait_for(observed(), timeout=2.0)
            assert state.is_live
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
