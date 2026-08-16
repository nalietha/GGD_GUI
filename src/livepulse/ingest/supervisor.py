"""Keeps push subscriptions alive, and makes them safe.

Push is fast but not reliable. Twitch EventSub over WebSocket has no delivery
retry, no acknowledgement, and no replay: a notification sent while your socket
is down is gone permanently. An unplanned disconnect also destroys the
subscriptions bound to that session, so after reconnecting you hold a *new*
session and must re-subscribe — and the interval between losing the socket and
finishing that re-subscription is another window where changes vanish.

So a supervisor that only reconnects would leave the display quietly wrong,
possibly for hours, with nothing in the logs. This one closes both gaps:

**On every subscribe, reconcile.** The moment a provider confirms its
subscriptions are live, the covered channels are made due immediately and the
ordinary poller fetches their true state. A missed go-live is corrected within
one poll pass rather than never.

**While disconnected, poll normally.** Losing the socket reverts those channels
from the slow reconciliation cadence to the ordinary base interval, so a dead
subscription degrades to *slower* updates, never to *no* updates.

The periodic reconciliation poll then remains as a third line of defence, for
the failure that has no signal at all: a socket that stays open while the
platform has silently dropped the subscription behind it.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from uuid import UUID

import structlog

from livepulse.core.clock import Clock, SystemClock
from livepulse.core.events import ProviderHealthChanged
from livepulse.core.models import ChannelRef, LiveState
from livepulse.ingest.scheduler import IngestScheduler
from livepulse.providers.base import PushProvider

__all__ = ["PushSupervisor", "WatchStats"]

log = structlog.get_logger(__name__)

#: First retry delay after a dropped subscription, doubling up to the ceiling.
#: Short, because a brief wifi blip should recover almost immediately.
BASE_BACKOFF = 1.0
MAX_BACKOFF = 60.0

#: Jitter fraction on the retry delay, so several providers reconnecting after
#: the same outage do not all hit the platform in the same instant.
BACKOFF_JITTER = 0.25

#: A subscription that survives this long is treated as healthy, resetting the
#: backoff. Without it, a connection that drops every few minutes would keep
#: escalating until it was retrying only once a minute.
STABLE_AFTER = 120.0


@dataclass(slots=True)
class WatchStats:
    """What the diagnostics page reports about a subscription."""

    connects: int = 0
    drops: int = 0
    events: int = 0
    reconciles: int = 0
    consecutive_failures: int = 0
    connected: bool = False
    last_error: str | None = None


@dataclass(slots=True)
class _Watch:
    """One provider's subscription and the channels it covers."""

    provider: PushProvider
    provider_id: str
    refs: tuple[ChannelRef, ...]
    channel_ids: dict[str, UUID]
    """platform_ref → channel id, so an emitted ref maps back to a channel."""

    stats: WatchStats = field(default_factory=WatchStats)
    task: asyncio.Task[None] | None = None


class _Session:
    """The :class:`~livepulse.providers.base.PushSession` given to a provider.

    Thin on purpose: providers should report facts, and every decision about
    what those facts mean belongs to the supervisor.
    """

    __slots__ = ("_supervisor", "_watch")

    def __init__(self, supervisor: PushSupervisor, watch: _Watch) -> None:
        self._supervisor = supervisor
        self._watch = watch

    async def emit(self, ref: ChannelRef, state: LiveState) -> None:
        self._supervisor._on_event(self._watch, ref, state)

    def subscribed(self, refs: Sequence[ChannelRef]) -> None:
        self._supervisor._on_subscribed(self._watch, refs)


class PushSupervisor:
    """Runs and restarts push subscriptions, reconciling on every reconnect."""

    __slots__ = (
        "_base_backoff",
        "_bus",
        "_clock",
        "_rng",
        "_scheduler",
        "_stopping",
        "_watches",
    )

    def __init__(
        self,
        scheduler: IngestScheduler,
        *,
        clock: Clock | None = None,
        rng: random.Random | None = None,
        base_backoff: float = BASE_BACKOFF,
    ) -> None:
        self._scheduler = scheduler
        self._clock = clock or SystemClock()
        self._rng = rng or random.Random()
        self._base_backoff = base_backoff
        self._watches: dict[str, _Watch] = {}
        self._stopping = asyncio.Event()
        self._bus = scheduler.bus

    # ----------------------------------------------------------------- #
    # Registration
    # ----------------------------------------------------------------- #

    def watch(
        self,
        provider: PushProvider,
        provider_id: str,
        channels: dict[UUID, ChannelRef],
    ) -> None:
        """Register a subscription to keep alive.

        Channels start *unpushed*: they are only marked as covered once the
        provider confirms the subscription, because until then they are not.
        """
        self._watches[provider_id] = _Watch(
            provider=provider,
            provider_id=provider_id,
            refs=tuple(channels.values()),
            channel_ids={ref.platform_ref: channel_id for channel_id, ref in channels.items()},
        )

    def stats(self) -> dict[str, WatchStats]:
        return {name: watch.stats for name, watch in self._watches.items()}

    @property
    def connected_count(self) -> int:
        return sum(1 for watch in self._watches.values() if watch.stats.connected)

    # ----------------------------------------------------------------- #
    # Lifecycle
    # ----------------------------------------------------------------- #

    async def start(self) -> None:
        """Launch a supervising task per registered subscription."""
        self._stopping.clear()
        for watch in self._watches.values():
            if watch.task is None or watch.task.done():
                watch.task = asyncio.create_task(
                    self._supervise(watch), name=f"livepulse-push-{watch.provider_id}"
                )

    async def stop(self) -> None:
        """Cancel every subscription and revert its channels to polling."""
        self._stopping.set()

        tasks = [watch.task for watch in self._watches.values() if watch.task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        for watch in self._watches.values():
            watch.task = None
            self._mark_covered(watch, covered=False)

    async def _supervise(self, watch: _Watch) -> None:
        """Run one subscription, restarting it for as long as we are running."""
        while not self._stopping.is_set():
            started = self._clock.monotonic()

            try:
                await watch.provider.watch(watch.refs, _Session(self, watch))
                # Returning rather than raising is a clean end — but the
                # subscription is over either way, so it is still a drop.
                reason = "watch returned"
            except asyncio.CancelledError:
                self._on_disconnected(watch, "cancelled")
                raise
            except Exception as exc:
                reason = f"{type(exc).__name__}: {exc}"
                log.warning(
                    "push subscription failed",
                    provider=watch.provider_id,
                    error=reason,
                )

            self._on_disconnected(watch, reason)

            if self._clock.monotonic() - started >= STABLE_AFTER:
                # It ran long enough to count as working; do not punish a
                # long-lived connection for eventually dropping.
                watch.stats.consecutive_failures = 0

            delay = self._backoff(watch.stats.consecutive_failures)
            watch.stats.consecutive_failures += 1

            log.info(
                "retrying push subscription", provider=watch.provider_id, delay=round(delay, 1)
            )
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=delay)
            except TimeoutError:
                continue

    def _backoff(self, failures: int) -> float:
        # Bounded before exponentiating: a subscription failing for days
        # would otherwise overflow the float conversion.
        base: float = min(self._base_backoff * float(2 ** min(failures, 16)), MAX_BACKOFF)
        jitter: float = BACKOFF_JITTER * self._rng.random()
        return base * (1.0 + jitter)

    # ----------------------------------------------------------------- #
    # Provider callbacks
    # ----------------------------------------------------------------- #

    def _on_subscribed(self, watch: _Watch, refs: Sequence[ChannelRef]) -> None:
        """The subscription is live. Close the gap it left while it was not.

        This is the whole reason the protocol reports subscription rather than
        merely connection. Everything that changed while the socket was down
        went unreported and will never be resent, so the covered channels are
        made due immediately and the ordinary poller establishes the truth.
        """
        first_time = not watch.stats.connected
        watch.stats.connected = True
        watch.stats.connects += 1
        watch.stats.consecutive_failures = 0

        covered = [
            watch.channel_ids[ref.platform_ref]
            for ref in refs
            if ref.platform_ref in watch.channel_ids
        ]
        self._scheduler.set_pushed_many(covered, True)

        reconciled = self._scheduler.reconcile_now(covered)
        watch.stats.reconciles += 1

        log.info(
            "push subscription live, reconciling",
            provider=watch.provider_id,
            channels=len(covered),
            reconciling=reconciled,
            reconnect=not first_time,
        )
        self._announce_health(watch, healthy=True)

    def _on_event(self, watch: _Watch, ref: ChannelRef, state: LiveState) -> None:
        watch.stats.events += 1
        if not self._scheduler.apply_push(ref, state):
            log.warning(
                "push event for an untracked channel",
                provider=watch.provider_id,
                platform_ref=ref.platform_ref,
            )

    def _on_disconnected(self, watch: _Watch, reason: str) -> None:
        """The subscription is gone. Fall back to ordinary polling.

        Reverting to the base interval rather than stopping is the difference
        between a degraded display and a frozen one.
        """
        if watch.stats.connected:
            watch.stats.drops += 1
            log.warning("push subscription lost", provider=watch.provider_id, reason=reason)

        watch.stats.connected = False
        watch.stats.last_error = reason
        self._mark_covered(watch, covered=False)
        self._announce_health(watch, healthy=False, detail=reason)

    def _mark_covered(self, watch: _Watch, *, covered: bool) -> None:
        self._scheduler.set_pushed_many(watch.channel_ids.values(), covered)

    def _announce_health(self, watch: _Watch, *, healthy: bool, detail: str | None = None) -> None:
        self._bus.publish(
            ProviderHealthChanged(
                provider=f"{watch.provider_id}:push", healthy=healthy, detail=detail
            )
        )
