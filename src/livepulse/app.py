"""The running service.

This is the composition that turns the pieces built so far — providers,
scheduler, supervisor, storage, renderer — into one long-running process. V1
needed two processes in two languages sharing a JSON file to do what
``livepulse run`` does here in one process tree with one source of truth.

Two responsibilities, kept separate on purpose:

* :class:`Coordinator` — the async glue between ingestion and rendering. It
  subscribes to the bus, persists observations, resolves domain state into
  render snapshots, and schedules the go-live flash. Its unit of work is
  :meth:`Coordinator.handle`, which the tests drive directly; :meth:`run` only
  adds iteration, mirroring the renderer's ``tick``/thread split.
* :func:`run_service` — the composition root for the daemon. The one place
  that knows which concrete providers, driver, and database exist. Everything
  it builds communicates through the bus or through injected collaborators, so
  no subsystem imports another.

The web layer, when it lands, becomes a third subscriber on the same bus — the
coordinator will not change.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

import httpx
import structlog

from livepulse.config.settings import Settings, provider_secrets
from livepulse.core.bus import EventBus
from livepulse.core.clock import Clock, SystemClock
from livepulse.core.errors import LivePulseError, StorageError
from livepulse.core.events import (
    ChannelStateChanged,
    DisplayModeChanged,
    Event,
    NodeConfigChanged,
    SettingChanged,
)
from livepulse.core.models import Channel, DisplayMode, LiveState, Node
from livepulse.ingest.scheduler import IngestScheduler
from livepulse.ingest.supervisor import PushSupervisor
from livepulse.leds.color import GammaTable
from livepulse.leds.compositor import Compositor
from livepulse.leds.driver.registry import create_driver
from livepulse.leds.layout import Layout
from livepulse.leds.renderer import Renderer
from livepulse.leds.state import build_render_state
from livepulse.providers import Provider, ProviderConfig, PushProvider, create_provider
from livepulse.providers.twitch import PROVIDER_ID as TWITCH_ID
from livepulse.providers.twitch import TwitchProvider, UserTokenSource
from livepulse.storage.db import Database
from livepulse.storage.repositories import Repositories
from livepulse.storage.runtime import RuntimeSettingsStore

__all__ = ["Coordinator", "run_service"]

log = structlog.get_logger(__name__)

#: The transient fired when a bound channel goes live while someone could see it.
FLASH_EFFECT = "flash"


class Coordinator:
    """Connects what the platforms report to what the strip shows.

    Holds the in-memory working set — channels, nodes, latest observations —
    and rebuilds the immutable render snapshot whenever any of it changes.
    This is the single writer of :meth:`Renderer.submit`, so there is exactly
    one place where domain state becomes pixels.
    """

    __slots__ = (
        "_bus",
        "_channels",
        "_clock",
        "_live",
        "_nodes",
        "_renderer",
        "_repos",
        "_scheduler",
        "_stale_after",
        "_store",
        "_tracked",
    )

    def __init__(
        self,
        bus: EventBus,
        repos: Repositories,
        store: RuntimeSettingsStore,
        renderer: Renderer,
        scheduler: IngestScheduler,
        *,
        clock: Clock | None = None,
        state_stale_after: float = 900.0,
    ) -> None:
        self._bus = bus
        self._repos = repos
        self._store = store
        self._renderer = renderer
        self._scheduler = scheduler
        self._clock = clock or SystemClock()
        self._stale_after = timedelta(seconds=state_stale_after)

        self._channels: dict[UUID, Channel] = {}
        self._nodes: list[Node] = []
        self._live: dict[UUID, LiveState] = {}
        self._tracked: set[UUID] = set()

    # ----------------------------------------------------------------- #
    # Lifecycle
    # ----------------------------------------------------------------- #

    async def start(self) -> None:
        """Seed the working set from storage and publish the first snapshot.

        Observations older than the staleness threshold are dropped: after
        hours off, the database still holds what was true at shutdown, and
        trusting it would light stale colours and — worse — make the first
        real poll look like a burst of fresh go-live transitions, flashing the
        whole stand at once. Dropping them makes resumption a cold start,
        which is already the quiet path: no previous state, no flash.
        """
        stored = await self._repos.live_state.all()
        now = self._clock.now()
        self._live = {
            channel_id: state
            for channel_id, state in stored.items()
            if now - state.observed_at <= self._stale_after
        }

        if dropped := len(stored) - len(self._live):
            log.info("ignoring stale stored observations", count=dropped)

        await self.reload()

    async def reload(self) -> None:
        """Re-read channels and nodes, resync polling, publish a snapshot."""
        channels = await self._repos.channels.list()
        self._channels = {channel.id: channel for channel in channels}
        self._nodes = await self._repos.nodes.list()

        # A deleted channel's last observation must not linger: its nodes are
        # already unbound by the schema, and keeping the entry would leak.
        self._live = {
            channel_id: state
            for channel_id, state in self._live.items()
            if channel_id in self._channels
        }

        self._sync_tracking()
        self.submit()

    def submit(self) -> None:
        """Rebuild the render snapshot from the working set and publish it."""
        state = build_render_state(self._nodes, self._channels, self._live, self._store.current)
        self._renderer.submit(state)

    # ----------------------------------------------------------------- #
    # Event handling
    # ----------------------------------------------------------------- #

    async def run(self) -> None:
        """Apply bus events until cancelled.

        A failure handling one event must not stop the stream: the display
        would silently freeze on its last good state, which is V1's signature
        failure mode.
        """
        with self._bus.subscribe(
            ChannelStateChanged, NodeConfigChanged, DisplayModeChanged, SettingChanged
        ) as events:
            log.info("coordinator started")
            async for event in events:
                try:
                    await self.handle(event)
                except Exception:
                    log.exception("error applying event", event=type(event).__name__)

    async def handle(self, event: Event) -> None:
        """Apply one event. The unit the tests drive; :meth:`run` only iterates."""
        if isinstance(event, ChannelStateChanged):
            await self._on_state_changed(event)
        elif isinstance(event, NodeConfigChanged):
            # Bindings, colours, or effects moved under us; the database is
            # the authority, so re-read rather than patching the cache.
            await self.reload()
        elif isinstance(event, DisplayModeChanged | SettingChanged):
            # The settings store has already updated its cache; only the
            # snapshot needs rebuilding. Mode OFF included — the compositor's
            # master fade handles darkness, so state keeps flowing underneath.
            self.submit()

    async def _on_state_changed(self, event: ChannelStateChanged) -> None:
        if event.channel_id not in self._channels:
            # A poll that was in flight when the channel was deleted.
            log.warning("observation for an unknown channel", channel_id=str(event.channel_id))
            return

        self._live[event.channel_id] = event.current
        try:
            await self._repos.live_state.record(event.channel_id, event.current)
        except StorageError as exc:
            # Persistence is a nicety here — the in-memory state drives the
            # lights either way, and the row will be rewritten next poll.
            log.warning("could not persist observation", error=str(exc))

        self.submit()

        if event.went_live and self._store.current.mode is DisplayMode.STREAMER:
            self._flash(event.channel_id)

    def _flash(self, channel_id: UUID) -> None:
        """Fire the go-live transient on every node bound to this channel.

        Only reached in STREAMER mode: AMBIENT ignores live status by design,
        and OFF must stay dark — a go-live during quiet hours does not wake
        the stand.
        """
        nodes = {node.id for node in self._nodes if node.channel_id == channel_id}
        if nodes:
            self._renderer.compositor.add_transient(nodes, FLASH_EFFECT, now=self._renderer.elapsed)

    # ----------------------------------------------------------------- #
    # Scheduler bookkeeping
    # ----------------------------------------------------------------- #

    def _sync_tracking(self) -> None:
        """Make the scheduler poll exactly the channels that matter.

        A channel earns polling by being enabled and bound to at least one
        node; a configured-but-unplaced channel costs no API budget. Existing
        entries are left alone — re-tracking would reset the channel's
        schedule and its remembered previous state.
        """
        bound = {node.channel_id for node in self._nodes if node.channel_id is not None}
        wanted = {
            channel_id
            for channel_id in bound
            if (channel := self._channels.get(channel_id)) is not None and channel.enabled
        }

        for channel_id in wanted - self._tracked:
            self._scheduler.track(
                channel_id,
                self._channels[channel_id].ref,
                last=self._live.get(channel_id),
            )
        for channel_id in self._tracked - wanted:
            self._scheduler.untrack(channel_id)

        self._tracked = wanted


# --------------------------------------------------------------------------- #
# Composition root
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _BuiltProvider:
    """A constructed provider plus what the composition root knows about it."""

    provider: Provider
    push_ready: bool = False

    shared_http: httpx.AsyncClient | None = None
    """A client this root created and must close — the provider does not own it."""


async def _build_twitch(config: ProviderConfig, repos: Repositories) -> _BuiltProvider:
    """Twitch, with push wired in when a user token exists.

    EventSub over WebSocket refuses an app token, and the user-token source
    needs the database — which the provider registry cannot reach. So this is
    the injection the registry cannot do, done in the one place allowed to
    know about both (see the note in ``providers.twitch.poll``).
    """
    if await repos.tokens.get(TWITCH_ID) is None:
        log.info(
            "twitch will poll only — run 'livepulse provider login twitch' to enable push",
        )
        return _BuiltProvider(provider=create_provider(TWITCH_ID, config))

    # Secrets first: failing here must not leak an opened client.
    client_id = config.require_secret("client_id")
    client_secret = config.require_secret("client_secret")

    http = httpx.AsyncClient(timeout=config.timeout)
    user_auth = UserTokenSource(client_id, client_secret, http, repos.tokens)
    provider = TwitchProvider(config, http=http, user_auth=user_auth)
    return _BuiltProvider(provider=provider, push_ready=True, shared_http=http)


async def _build_providers(
    provider_ids: set[str], settings: Settings, repos: Repositories
) -> dict[str, _BuiltProvider]:
    """Construct one provider per platform in use.

    A platform that cannot be built — missing credentials, usually — is
    logged and skipped rather than stopping the service: its nodes go dark
    and everything else keeps working. One broken integration must never
    take the display down.
    """
    built: dict[str, _BuiltProvider] = {}
    for provider_id in sorted(provider_ids):
        config = ProviderConfig(
            provider_id=provider_id,
            secrets=provider_secrets(provider_id),
            timeout=settings.ingest.request_timeout,
        )
        try:
            if provider_id == TWITCH_ID:
                built[provider_id] = await _build_twitch(config, repos)
            else:
                built[provider_id] = _BuiltProvider(provider=create_provider(provider_id, config))
        except LivePulseError as exc:
            log.error(
                "provider unavailable; its channels will not update",
                provider=provider_id,
                error=str(exc),
            )
    return built


async def run_service(
    settings: Settings,
    *,
    started: asyncio.Event | None = None,
    stop: asyncio.Event | None = None,
) -> None:
    """Run the whole service until told to stop.

    Args:
        started: Set once every subsystem is up. For tests and healthchecks.
        stop: Shutdown trigger. When omitted, SIGINT/SIGTERM set it.
    """
    stopping = stop or asyncio.Event()

    # Hardware first: if the strip cannot be driven there is nothing to run,
    # and the failure should arrive before the database is touched.
    layout = Layout(settings.layout)
    driver = create_driver(layout.pixel_count, settings.leds)
    renderer = Renderer(
        driver, Compositor(layout, GammaTable(settings.leds.gamma)), fps=settings.leds.fps
    )

    bus = EventBus()

    async with Database(settings.db_path) as db:
        repos = Repositories(db)
        store = RuntimeSettingsStore(repos.settings, bus)
        await store.load()

        if created := await repos.nodes.ensure(settings.layout.nodes):
            log.info("created nodes", count=created)

        scheduler = IngestScheduler(bus, settings.ingest)
        supervisor = PushSupervisor(scheduler)
        coordinator = Coordinator(
            bus,
            repos,
            store,
            renderer,
            scheduler,
            state_stale_after=settings.ingest.state_stale_after,
        )

        # Only platforms with an enabled, node-bound channel are built at all.
        channels = await repos.channels.list(enabled_only=True)
        bound_ids = await repos.nodes.bound_channel_ids()
        active = [channel for channel in channels if channel.id in bound_ids]

        built = await _build_providers({channel.provider for channel in active}, settings, repos)
        for provider_id, entry in built.items():
            scheduler.register(entry.provider)
            if entry.push_ready and isinstance(entry.provider, PushProvider):
                covered = {
                    channel.id: channel.ref for channel in active if channel.provider == provider_id
                }
                if covered:
                    supervisor.watch(entry.provider, provider_id, covered)

        await coordinator.start()
        renderer.start()

        tasks = [
            asyncio.create_task(coordinator.run(), name="livepulse-coordinator"),
            asyncio.create_task(scheduler.run(), name="livepulse-ingest"),
        ]
        await supervisor.start()

        _install_signal_handlers(stopping)
        log.info(
            "livepulse running",
            channels=len(active),
            nodes=len(await repos.nodes.list()),
            providers=sorted(built),
            mode=store.current.mode.value,
        )
        if started is not None:
            started.set()

        try:
            await stopping.wait()
        finally:
            log.info("shutting down")
            _remove_signal_handlers()

            # Ingestion first, so nothing publishes into a closing pipeline;
            # the renderer last of the consumers, so the strip blanks cleanly.
            await supervisor.stop()
            scheduler.stop()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

            renderer.stop()

            for entry in built.values():
                await entry.provider.aclose()
                if entry.shared_http is not None:
                    await entry.shared_http.aclose()


def _install_signal_handlers(stopping: asyncio.Event) -> None:
    """Turn SIGINT and SIGTERM into a clean shutdown.

    Clean matters: an abrupt exit leaves 112 LEDs holding their last frame,
    which is exactly what V1 did on any exit that was not a KeyboardInterrupt.
    """
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # non-Unix event loops
            loop.add_signal_handler(sig, stopping.set)


def _remove_signal_handlers() -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.remove_signal_handler(sig)
