"""When may we poll, and should we bother?

Two policies, both pure enough to test without a clock running:

:class:`IntervalPolicy` decides how long until a channel is next due.
:class:`CircuitBreaker` decides whether a failing provider gets another chance.

Both exist because V1 had neither. It polled every streamer every ten seconds
forever, whether they had been offline for a month or not, and a provider that
started failing simply failed on every tick with a ``print()`` nobody saw on a
headless Pi.
"""

from __future__ import annotations

import random
from enum import StrEnum

from livepulse.config.settings import IngestSettings
from livepulse.core.clock import Clock, SystemClock

__all__ = ["BreakerState", "CircuitBreaker", "IntervalPolicy"]

#: Consecutive offline observations before the interval starts stretching.
#: Below this, a channel that just went offline is still polled promptly, so a
#: quick restart is picked up fast.
BACKOFF_AFTER = 4

#: Offline observations per doubling, once backoff starts.
BACKOFF_STEP = 6

#: Ceiling on doublings. Any value past this is already far above
#: max_poll_interval, and the exponent has to be bounded before it is
#: applied — a channel offline for months reaches thousands of doublings.
MAX_DOUBLINGS = 16


class IntervalPolicy:
    """How long until a channel should be polled again.

    Three regimes:

    * **Pushed** — a live subscription already covers it, so polling drops to a
      slow reconciliation tick. Not zero: a dropped socket must not silently
      freeze the display, so the poll remains as a correctness backstop.
    * **Live** — poll at the base rate. Going offline should show up promptly.
    * **Offline** — stretch toward the ceiling the longer it stays offline.
      Someone who has not streamed in a week does not need checking every 30
      seconds, and the saved budget goes to channels that might actually change.

    Jitter is applied on top of all three so N channels never stampede the API
    in the same tick.
    """

    __slots__ = ("_rng", "_settings")

    def __init__(self, settings: IngestSettings, rng: random.Random | None = None) -> None:
        self._settings = settings
        # Injected so tests are deterministic; the module-level random would
        # make every scheduling assertion flaky.
        self._rng = rng or random.Random()

    def interval_for(
        self,
        *,
        is_live: bool,
        consecutive_offline: int,
        pushed: bool = False,
        provider_minimum: float = 0.0,
    ) -> float:
        """Base interval before jitter, in seconds.

        ``provider_minimum`` is the provider's declared floor and always wins:
        the global configuration may ask for faster polling than a platform
        permits, and the platform's answer is the binding one.
        """
        if pushed:
            # Reconciliation is deliberately slower than max_poll_interval, so
            # it is not subject to that ceiling — capping it there would poll a
            # pushed channel far more often than intended.
            return max(self._settings.reconcile_interval, provider_minimum)

        if is_live or consecutive_offline < BACKOFF_AFTER:
            base = self._settings.base_poll_interval
        else:
            doublings = (consecutive_offline - BACKOFF_AFTER) // BACKOFF_STEP + 1
            # Capped before exponentiating. A channel offline for months reaches
            # thousands of doublings, and 2**1667 cannot be converted to a float
            # — it raises OverflowError inside the ingest loop.
            base = self._settings.base_poll_interval * (2 ** min(doublings, MAX_DOUBLINGS))

        capped = min(base, self._settings.max_poll_interval)
        return max(capped, provider_minimum)

    def next_due(self, now: float, interval: float) -> float:
        """When to poll next, with jitter applied.

        Jitter only ever *delays*. Pulling a poll earlier could breach a
        provider's declared minimum interval, which the floor above exists to
        respect.
        """
        spread = self._settings.jitter
        offset = interval * spread * self._rng.random() if spread > 0 else 0.0
        return now + interval + offset


class BreakerState(StrEnum):
    CLOSED = "closed"
    """Working. Requests flow."""

    OPEN = "open"
    """Failing. Requests are refused until the recovery window elapses."""

    HALF_OPEN = "half_open"
    """Probing. One request is allowed through to see whether it recovered."""


class CircuitBreaker:
    """Stops hammering a provider that is clearly broken.

    Without this, an expired credential means one failed request per channel
    per interval, forever — filling the journal, burning the rate limit, and
    telling nobody. The open state is also what the UI renders as a degraded
    badge, so a broken integration is visible rather than merely quiet.
    """

    __slots__ = ("_clock", "_failures", "_opened_at", "_recovery", "_state", "_threshold")

    def __init__(
        self,
        threshold: int = 5,
        recovery: float = 60.0,
        clock: Clock | None = None,
    ) -> None:
        if threshold < 1:
            raise ValueError(f"threshold must be at least 1, got {threshold}")

        self._threshold = threshold
        self._recovery = recovery
        self._clock = clock or SystemClock()
        self._state = BreakerState.CLOSED
        self._failures = 0
        self._opened_at = 0.0

    @property
    def state(self) -> BreakerState:
        """Current state, moving to half-open once the recovery window passes."""
        recovered = self._clock.monotonic() - self._opened_at >= self._recovery
        if self._state is BreakerState.OPEN and recovered:
            self._state = BreakerState.HALF_OPEN
        return self._state

    @property
    def failures(self) -> int:
        return self._failures

    @property
    def is_healthy(self) -> bool:
        return self.state is BreakerState.CLOSED

    def allow(self) -> bool:
        """Whether to attempt a request now."""
        return self.state is not BreakerState.OPEN

    def record_success(self) -> None:
        """Close the circuit and forget the failure history."""
        self._state = BreakerState.CLOSED
        self._failures = 0

    def record_failure(self) -> None:
        """Count a failure, opening the circuit at the threshold.

        A failure while half-open reopens immediately: the probe was the test,
        and it failed.
        """
        self._failures += 1

        if self._state is BreakerState.HALF_OPEN or self._failures >= self._threshold:
            self._state = BreakerState.OPEN
            self._opened_at = self._clock.monotonic()

    def trip(self) -> None:
        """Open the circuit at once, without waiting for the threshold.

        For failures that are certainly not transient — rejected credentials.
        Retrying those four more times helps nobody.
        """
        self._failures = max(self._failures, self._threshold)
        self._state = BreakerState.OPEN
        self._opened_at = self._clock.monotonic()
