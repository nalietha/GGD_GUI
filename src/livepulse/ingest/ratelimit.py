"""Token-bucket rate limiting.

One bucket per provider. Platforms publish request budgets — Twitch Helix
allows 800 points a minute for an app token — and exceeding one earns a 429 and
a cooling-off period that hurts far more than the requests saved.

V1 had no limiting of any kind, and two processes polling the same API on
different intervals. It stayed under the limit by having few streamers, not by
design.

Time comes from an injected clock, so a test can exhaust a bucket and watch it
refill without sleeping.
"""

from __future__ import annotations

from livepulse.core.clock import Clock, SystemClock

__all__ = ["TokenBucket"]


class TokenBucket:
    """Allows ``rate`` requests per second, bursting up to ``capacity``.

    Bursting matters here: after an idle stretch the scheduler wants to poll
    every channel at once, and a strict rate would drip them out over minutes.
    """

    __slots__ = ("_capacity", "_clock", "_last", "_penalty_until", "_rate", "_tokens")

    def __init__(
        self,
        rate: float,
        capacity: float | None = None,
        clock: Clock | None = None,
    ) -> None:
        if rate <= 0:
            raise ValueError(f"rate must be positive, got {rate}")

        self._rate = rate
        self._capacity = capacity if capacity is not None else max(1.0, rate)
        if self._capacity < 1:
            raise ValueError(f"capacity must be at least 1, got {self._capacity}")

        self._clock = clock or SystemClock()
        self._tokens = self._capacity
        self._last = self._clock.monotonic()
        self._penalty_until = 0.0

    @property
    def tokens(self) -> float:
        """Currently available tokens, after refilling for elapsed time."""
        self._refill()
        return self._tokens

    @property
    def penalised(self) -> bool:
        return self._clock.monotonic() < self._penalty_until

    def try_acquire(self, tokens: float = 1.0) -> bool:
        """Take tokens if available. Never blocks, never sleeps."""
        if self.penalised:
            return False

        self._refill()
        if self._tokens < tokens:
            return False

        self._tokens -= tokens
        return True

    def time_until(self, tokens: float = 1.0) -> float:
        """Seconds until ``tokens`` would be available. Zero if already."""
        penalty = max(0.0, self._penalty_until - self._clock.monotonic())
        self._refill()

        shortfall = tokens - self._tokens
        wait = shortfall / self._rate if shortfall > 0 else 0.0
        return max(penalty, wait)

    def penalise(self, seconds: float) -> None:
        """Refuse everything for a while, after the platform said to back off.

        Driven by a ``Retry-After`` header, so we wait the amount actually
        asked for rather than a guess. Also drains the bucket, so the moment
        the penalty lifts we do not immediately burst into another 429.
        """
        if seconds <= 0:
            return
        self._penalty_until = self._clock.monotonic() + seconds
        self._tokens = 0.0

    def _refill(self) -> None:
        now = self._clock.monotonic()
        elapsed = now - self._last
        if elapsed <= 0:
            return
        self._last = now
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
