"""Simulated backend.

Not a stub — a first-class backend that records what would have been sent. It
is what makes the whole application runnable and testable on a laptop, and it
backs the web UI's live preview so the browser grid mirrors the real strip
instead of guessing (V1 re-implemented the preview in Razor and the two drifted).

Because brightness and gamma are applied above the driver, the frames captured
here are byte-identical to what a real strip receives.
"""

from __future__ import annotations

from collections import deque

from livepulse.leds.color import RGB
from livepulse.leds.driver.base import PixelBufferDriver

__all__ = ["SimulatedDriver"]

DEFAULT_HISTORY = 8


class SimulatedDriver(PixelBufferDriver):
    """Captures frames in memory."""

    def __init__(
        self,
        pixel_count: int,
        *,
        color_order: str = "GRB",
        history: int = DEFAULT_HISTORY,
    ) -> None:
        super().__init__(pixel_count, color_order=color_order)
        # Bounded: a 60 fps loop left running for a week must not grow forever.
        self._frames: deque[tuple[RGB, ...]] = deque(maxlen=max(1, history))
        self._show_count = 0

    @property
    def show_count(self) -> int:
        """How many frames have been transmitted. Lets tests assert on frame rate."""
        return self._show_count

    @property
    def frame(self) -> tuple[RGB, ...]:
        """The most recently transmitted frame, or all-black before the first show."""
        if not self._frames:
            return tuple(self._buffer)
        return self._frames[-1]

    @property
    def frames(self) -> tuple[tuple[RGB, ...], ...]:
        """Recent frames, oldest first. For asserting on animation over time."""
        return tuple(self._frames)

    def _transmit(self, buffer: list[RGB]) -> None:
        self._frames.append(tuple(buffer))
        self._show_count += 1
