"""The web layer's environment: the real app over an in-memory database.

Shared by the API and page tests so both exercise the same composition — the
pages are a client of the API, and their tests should not get a friendlier
world than the API's do.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI

from livepulse.config.settings import IngestSettings, LayoutSettings
from livepulse.core.bus import EventBus
from livepulse.core.clock import ManualClock
from livepulse.core.models import Channel, LiveState
from livepulse.ingest.scheduler import IngestScheduler
from livepulse.ingest.supervisor import PushSupervisor
from livepulse.leds.color import GammaTable
from livepulse.leds.compositor import Compositor
from livepulse.leds.driver.sim import SimulatedDriver
from livepulse.leds.layout import Layout
from livepulse.leds.renderer import Renderer
from livepulse.leds.state import build_render_state
from livepulse.providers.manual import ManualProvider
from livepulse.storage.db import Database
from livepulse.storage.repositories import Repositories
from livepulse.storage.runtime import RuntimeSettingsStore
from livepulse.web.app import WebContext, create_app

LAYOUT = LayoutSettings(nodes=4, leds_per_node=1, order="linear", rows=1)


@dataclass
class WebEnv:
    app: FastAPI
    client: httpx.AsyncClient
    repos: Repositories
    bus: EventBus
    store: RuntimeSettingsStore
    renderer: Renderer
    manual: ManualProvider

    async def add_channel(self, handle: str = "alice") -> Channel:
        channel = Channel(provider="manual", platform_ref=handle, handle=handle)
        await self.repos.channels.add(channel)
        return channel

    async def render(self, live: dict[UUID, LiveState] | None = None) -> None:
        """Submit a snapshot built from storage, as the coordinator would."""
        channels = {channel.id: channel for channel in await self.repos.channels.list()}
        self.renderer.submit(
            build_render_state(
                await self.repos.nodes.list(),
                channels,
                live or {},
                self.store.current,
            )
        )


@pytest.fixture
async def env() -> AsyncIterator[WebEnv]:
    async with Database(":memory:") as db:
        repos = Repositories(db)
        bus = EventBus()
        store = RuntimeSettingsStore(repos.settings, bus)
        await store.load()
        await repos.nodes.ensure(LAYOUT.nodes)

        layout = Layout(LAYOUT)
        renderer = Renderer(
            SimulatedDriver(layout.pixel_count),
            Compositor(layout, GammaTable(1.0)),
            clock=ManualClock(),
        )
        scheduler = IngestScheduler(bus, IngestSettings(), clock=ManualClock())
        manual = ManualProvider()
        scheduler.register(manual)

        context = WebContext(
            repos=repos,
            store=store,
            bus=bus,
            scheduler=scheduler,
            supervisor=PushSupervisor(scheduler),
            renderer=renderer,
            providers={"manual": manual},
        )
        app = create_app(context)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://livepulse") as client:
            yield WebEnv(app, client, repos, bus, store, renderer, manual)
