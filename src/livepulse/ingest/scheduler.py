"""The polling loop.

Holds a schedule per tracked channel, groups the ones that are due by provider,
batches them into requests, and publishes what changed. It does not persist
anything and does not touch the renderer — it emits
:class:`~livepulse.core.events.ChannelStateChanged` and something downstream
decides what that means.

What V1 did instead: two processes polled the same API on different timers, one
writing results into a JSON file the other read, with no batching beyond a
single request, no rate limiting, no backoff, and no way to notice a provider
had stopped working.

Testing shape mirrors the renderer: :meth:`IngestScheduler.poll_once` performs
exactly one pass and is what the tests drive with a ManualClock. Only
:meth:`run` sleeps.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
from collections.abc import Iterable
from dataclasses import dataclass, field
from uuid import UUID

import structlog

from livepulse.config.settings import IngestSettings
from livepulse.core.bus import EventBus
from livepulse.core.clock import Clock, SystemClock
from livepulse.core.errors import ProviderAuthError, ProviderError, ProviderRateLimited
from livepulse.core.events import ChannelStateChanged, ProviderHealthChanged
from livepulse.core.models import ChannelRef, LiveState
from livepulse.ingest.policy import CircuitBreaker, IntervalPolicy
from livepulse.ingest.ratelimit import TokenBucket
from livepulse.providers.base import Provider, batched

__all__ = ["IngestScheduler", "TrackedChannel"]

log = structlog.get_logger(__name__)

#: Requests per second allowed to one provider by default. Deliberately modest:
#: sixteen channels in one batched request every 30s is nowhere near this, and a
#: provider that needs more can be given its own bucket.
DEFAULT_RATE = 2.0

#: How long the loop sleeps when nothing is due. Short enough that a newly
#: tracked channel is picked up promptly, long enough to cost nothing.
IDLE_SLEEP = 1.0


@dataclass(slots=True)
class TrackedChannel:
    """One channel's polling state."""

    channel_id: UUID
    ref: ChannelRef
    next_due: float = 0.0
    """Monotonic time. Zero means due immediately, so a newly tracked channel
    is polled on the next pass rather than after a full interval."""

    consecutive_offline: int = 0
    pushed: bool = False
    """Covered by a live subscription, so polled only as a backstop."""

    last: LiveState | None = None
    """The previous observation. ``None`` means never observed, which is what
    keeps a cold start from firing go-live animations for channels that were
    already broadcasting."""


@dataclass(slots=True)
class _ProviderRuntime:
    """Per-provider machinery, kept together so nothing is looked up twice."""

    provider: Provider
    bucket: TokenBucket
    breaker: CircuitBreaker
    healthy: bool = True
    channels: dict[UUID, TrackedChannel] = field(default_factory=dict)


class IngestScheduler:
    """Polls tracked channels and announces what changed."""

    __slots__ = ("_bus", "_clock", "_policy", "_providers", "_settings", "_stopping")

    def __init__(
        self,
        bus: EventBus,
        settings: IngestSettings,
        *,
        clock: Clock | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._bus = bus
        self._settings = settings
        self._clock = clock or SystemClock()
        self._policy = IntervalPolicy(settings, rng)
        self._providers: dict[str, _ProviderRuntime] = {}
        self._stopping = asyncio.Event()

    # ----------------------------------------------------------------- #
    # Registration
    # ----------------------------------------------------------------- #

    def register(self, provider: Provider, rate: float = DEFAULT_RATE) -> None:
        """Make a provider available for polling."""
        self._providers[provider.id] = _ProviderRuntime(
            provider=provider,
            bucket=TokenBucket(rate, clock=self._clock),
            breaker=CircuitBreaker(clock=self._clock),
        )

    def track(
        self,
        channel_id: UUID,
        ref: ChannelRef,
        *,
        last: LiveState | None = None,
        pushed: bool = False,
    ) -> None:
        """Start polling a channel.

        Args:
            last: A known previous state, seeded from storage at startup so a
                restart does not re-announce every channel as a fresh
                transition and set the whole stand flashing.
        """
        runtime = self._providers.get(ref.provider)
        if runtime is None:
            log.warning(
                "ignoring channel for an unregistered provider",
                provider=ref.provider,
                handle=ref.handle,
            )
            return

        runtime.channels[channel_id] = TrackedChannel(
            channel_id=channel_id, ref=ref, last=last, pushed=pushed
        )

    def untrack(self, channel_id: UUID) -> bool:
        """Stop polling a channel. Returns whether it was being polled."""
        for runtime in self._providers.values():
            if runtime.channels.pop(channel_id, None) is not None:
                return True
        return False

    def set_pushed(self, channel_id: UUID, pushed: bool) -> None:
        """Mark a channel as covered (or no longer covered) by a subscription.

        Dropping to reconciliation rather than stopping is deliberate: a
        silently dead socket would otherwise freeze that node indefinitely.
        """
        for runtime in self._providers.values():
            tracked = runtime.channels.get(channel_id)
            if tracked is not None:
                tracked.pushed = pushed
                tracked.next_due = 0.0
                return

    def reconcile_now(self, channel_ids: Iterable[UUID] | None = None) -> int:
        """Make channels due immediately, so the next pass polls them.

        This is what closes the gap left by a push transport. Twitch EventSub
        over WebSocket never redelivers a missed notification, and an unplanned
        disconnect destroys the subscriptions bound to that session — so
        anything that changed while the socket was down is simply not reported.
        Forcing a poll the moment the subscription is re-established recovers
        that state in seconds rather than waiting for the reconciliation tick.

        Args:
            channel_ids: Which channels, or ``None`` for every tracked channel.

        Returns:
            How many channels were made due.
        """
        wanted = set(channel_ids) if channel_ids is not None else None
        affected = 0

        for runtime in self._providers.values():
            for tracked in runtime.channels.values():
                if wanted is None or tracked.channel_id in wanted:
                    tracked.next_due = 0.0
                    affected += 1

        return affected

    def apply_push(self, ref: ChannelRef, state: LiveState) -> bool:
        """Record a state change that arrived over a subscription.

        Goes through the same transition logic as a poll, so a pushed update
        and a polled one are indistinguishable downstream — there is one place
        that decides what "changed" means.

        Returns:
            Whether the channel was known and the state applied.
        """
        runtime = self._providers.get(ref.provider)
        if runtime is None:
            return False

        for tracked in runtime.channels.values():
            if tracked.ref.platform_ref == ref.platform_ref:
                self._apply(tracked, state, runtime, self._clock.monotonic())
                return True
        return False

    def set_pushed_many(self, channel_ids: Iterable[UUID], pushed: bool) -> None:
        """Mark several channels at once, for a subscription covering a batch."""
        wanted = set(channel_ids)
        for runtime in self._providers.values():
            for tracked in runtime.channels.values():
                if tracked.channel_id in wanted:
                    tracked.pushed = pushed
                    if not pushed:
                        # Reverting to ordinary polling: make it due now rather
                        # than leaving it parked on the reconciliation cadence.
                        tracked.next_due = 0.0

    @property
    def bus(self) -> EventBus:
        """Exposed so the push supervisor can announce subscription health."""
        return self._bus

    @property
    def tracked_count(self) -> int:
        return sum(len(runtime.channels) for runtime in self._providers.values())

    def health(self) -> dict[str, bool]:
        """Per-provider health, for the diagnostics page and the UI badge."""
        return {name: runtime.breaker.is_healthy for name, runtime in self._providers.items()}

    # ----------------------------------------------------------------- #
    # Polling
    # ----------------------------------------------------------------- #

    async def poll_once(self) -> int:
        """Poll everything currently due, once.

        Providers are polled concurrently — a slow platform must not delay a
        fast one — while batches within one provider go in sequence so its rate
        limit means something.

        Returns:
            How many channels were polled.
        """
        now = self._clock.monotonic()
        work = [
            self._poll_provider(runtime, now)
            for runtime in self._providers.values()
            if self._due(runtime, now)
        ]
        if not work:
            return 0

        results = await asyncio.gather(*work, return_exceptions=True)

        polled = 0
        for outcome in results:
            if isinstance(outcome, BaseException):
                # A provider must never be able to take the loop down with it.
                log.exception("provider pass failed", exc_info=outcome)
                continue
            polled += outcome
        return polled

    async def run(self) -> None:
        """Poll until stopped."""
        self._stopping.clear()
        log.info("ingest started", providers=sorted(self._providers), channels=self.tracked_count)

        while not self._stopping.is_set():
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("unhandled error in ingest loop")

            # wait_for raises on timeout, which is the ordinary case here:
            # it means nothing asked us to stop before the next poll was due.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), timeout=self._sleep_for())

        log.info("ingest stopped")

    def stop(self) -> None:
        self._stopping.set()

    # ----------------------------------------------------------------- #
    # Internals
    # ----------------------------------------------------------------- #

    def _due(self, runtime: _ProviderRuntime, now: float) -> bool:
        return any(tracked.next_due <= now for tracked in runtime.channels.values())

    def _sleep_for(self) -> float:
        """Seconds until the next channel is due, bounded."""
        now = self._clock.monotonic()
        due = [
            tracked.next_due
            for runtime in self._providers.values()
            for tracked in runtime.channels.values()
        ]
        if not due:
            return IDLE_SLEEP
        return max(0.0, min(min(due) - now, IDLE_SLEEP))

    async def _poll_provider(self, runtime: _ProviderRuntime, now: float) -> int:
        if not runtime.breaker.allow():
            # Still due, but the circuit is open. Push them out so the loop
            # does not spin re-checking a provider it has agreed not to call.
            self._defer(runtime, now, seconds=self._settings.base_poll_interval)
            return 0

        due = [tracked for tracked in runtime.channels.values() if tracked.next_due <= now]
        if not due:
            return 0

        caps = runtime.provider.caps
        size = caps.max_batch if caps.supports_batch else 1
        polled = 0

        for batch in batched(due, size):
            if not runtime.bucket.try_acquire():
                # Out of local budget. The rest stay due and the next pass
                # picks them up, rather than being dropped for this interval.
                log.debug("deferring batch, local rate limit", provider=runtime.provider.id)
                break

            if not await self._poll_batch(runtime, list(batch)):
                break
            polled += len(batch)

        return polled

    async def _poll_batch(self, runtime: _ProviderRuntime, tracked: list[TrackedChannel]) -> bool:
        """Poll one batch. Returns False when the provider should be left alone."""
        try:
            observed = await runtime.provider.poll([entry.ref for entry in tracked])
        except ProviderRateLimited as exc:
            # Wait exactly as long as the platform asked, rather than guessing.
            runtime.bucket.penalise(exc.retry_after)
            self._defer(runtime, self._clock.monotonic(), seconds=exc.retry_after)
            log.warning(
                "provider rate limited us",
                provider=runtime.provider.id,
                retry_after=exc.retry_after,
            )
            return False
        except ProviderAuthError as exc:
            # Not transient. Four more attempts would not fix a rejected key.
            runtime.breaker.trip()
            self._announce_health(runtime, healthy=False, detail=str(exc))
            return False
        except ProviderError as exc:
            runtime.breaker.record_failure()
            if not runtime.breaker.is_healthy:
                self._announce_health(runtime, healthy=False, detail=str(exc))
            log.warning("provider poll failed", provider=runtime.provider.id, error=str(exc))
            return False

        runtime.breaker.record_success()
        if not runtime.healthy:
            self._announce_health(runtime, healthy=True)

        now = self._clock.monotonic()
        for entry in tracked:
            self._apply(entry, observed.get(entry.ref.platform_ref), runtime, now)
        return True

    def _apply(
        self,
        tracked: TrackedChannel,
        state: LiveState | None,
        runtime: _ProviderRuntime,
        now: float,
    ) -> None:
        if state is None:
            # A provider that breaks the poll contract. Reschedule rather than
            # inventing an observation we did not make.
            log.warning(
                "provider omitted a channel from its result",
                provider=runtime.provider.id,
                platform_ref=tracked.ref.platform_ref,
            )
            self._reschedule(tracked, runtime, now)
            return

        previous = tracked.last
        changed = previous is None or previous.is_live != state.is_live

        tracked.last = state
        tracked.consecutive_offline = 0 if state.is_live else tracked.consecutive_offline + 1
        self._reschedule(tracked, runtime, now)

        if changed:
            self._bus.publish(
                ChannelStateChanged(channel_id=tracked.channel_id, current=state, previous=previous)
            )

    def _reschedule(self, tracked: TrackedChannel, runtime: _ProviderRuntime, now: float) -> None:
        interval = self._policy.interval_for(
            is_live=bool(tracked.last and tracked.last.is_live),
            consecutive_offline=tracked.consecutive_offline,
            pushed=tracked.pushed,
            provider_minimum=runtime.provider.caps.min_poll_interval,
        )
        tracked.next_due = self._policy.next_due(now, interval)

    def _defer(self, runtime: _ProviderRuntime, now: float, seconds: float) -> None:
        for tracked in runtime.channels.values():
            tracked.next_due = max(tracked.next_due, now + seconds)

    def _announce_health(
        self, runtime: _ProviderRuntime, *, healthy: bool, detail: str | None = None
    ) -> None:
        if runtime.healthy == healthy:
            return
        runtime.healthy = healthy
        self._bus.publish(
            ProviderHealthChanged(provider=runtime.provider.id, healthy=healthy, detail=detail)
        )
        log.info("provider health changed", provider=runtime.provider.id, healthy=healthy)
