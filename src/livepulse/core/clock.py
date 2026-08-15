"""Injectable time source.

The renderer, the scheduler, and every effect are functions of time. If they
call :func:`time.monotonic` directly they cannot be tested without sleeping,
so time enters through this protocol instead (dependency inversion).

``ManualClock`` lets a test render frame 0, frame 1000, and frame 60000 of an
animation instantly and deterministically.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Time as a dependency."""

    def monotonic(self) -> float:
        """Seconds from an arbitrary origin. Never goes backwards. For durations."""
        ...

    def now(self) -> datetime:
        """Timezone-aware wall-clock time in UTC. For timestamps."""
        ...


class SystemClock:
    """Real time. The production implementation."""

    __slots__ = ()

    def monotonic(self) -> float:
        return time.monotonic()

    def now(self) -> datetime:
        return datetime.now(UTC)


class ManualClock:
    """Time under test control.

    Starts at a fixed epoch so timestamp assertions are stable across runs.
    """

    __slots__ = ("_monotonic", "_wall")

    def __init__(
        self,
        *,
        monotonic: float = 0.0,
        wall: datetime | None = None,
    ) -> None:
        self._monotonic = monotonic
        self._wall = wall or datetime(2026, 1, 1, tzinfo=UTC)

    def monotonic(self) -> float:
        return self._monotonic

    def now(self) -> datetime:
        return self._wall

    def advance(self, seconds: float) -> None:
        """Move both clocks forward together."""
        if seconds < 0:
            raise ValueError("cannot advance a monotonic clock backwards")
        self._monotonic += seconds
        self._wall = self._wall.fromtimestamp(self._wall.timestamp() + seconds, tz=UTC)
