"""The frame loop.

Runs on its own thread at a fixed rate, reading whatever snapshot the async
side last published. It never awaits, never touches the database, and never
takes a lock the event loop holds — the only shared state is one reference to
an immutable :class:`~livepulse.leds.state.RenderState`, and rebinding a
reference is atomic in CPython.

That isolation is the point. ``rpi_ws281x`` and the SPI write are blocking C
calls; running them on the event loop would stall ingestion every frame. V1 had
the opposite arrangement — its effects blocked the *polling* loop — and sixteen
nodes going live at once froze everything for over a minute.

Pacing and drawing are separate. :meth:`Renderer.tick` renders exactly one
frame and is what the tests drive, with a :class:`~livepulse.core.clock.ManualClock`;
the thread only adds sleeping.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from types import TracebackType

import structlog

from livepulse.core.clock import Clock, SystemClock
from livepulse.core.errors import DriverError
from livepulse.leds.compositor import Compositor
from livepulse.leds.driver.base import LedDriver
from livepulse.leds.state import RenderState

__all__ = ["Renderer", "RendererStats"]

log = structlog.get_logger(__name__)

#: Consecutive driver failures tolerated before the loop gives up. A single
#: failed SPI write is worth retrying; a hundred means the hardware is gone and
#: spinning on it would just fill the journal.
MAX_CONSECUTIVE_ERRORS = 10


@dataclass(slots=True)
class RendererStats:
    """Observability for the diagnostics page."""

    frames: int = 0
    late_frames: int = 0
    """Frames whose work overran the budget. A few is normal; a steady stream
    means the target rate is unachievable and should be lowered."""

    driver_errors: int = 0
    last_error: str | None = None

    @property
    def late_fraction(self) -> float:
        return self.late_frames / self.frames if self.frames else 0.0


class Renderer:
    """Drives a :class:`Compositor` into an :class:`LedDriver` at a fixed rate."""

    __slots__ = (
        "_clock",
        "_compositor",
        "_driver",
        "_errors",
        "_frame_seconds",
        "_started_at",
        "_state",
        "_stats",
        "_stop",
        "_thread",
    )

    def __init__(
        self,
        driver: LedDriver,
        compositor: Compositor,
        *,
        fps: int = 60,
        clock: Clock | None = None,
    ) -> None:
        if fps <= 0:
            raise ValueError(f"fps must be positive, got {fps}")

        self._driver = driver
        self._compositor = compositor
        self._clock = clock or SystemClock()
        self._frame_seconds = 1.0 / fps

        self._state: RenderState | None = None
        self._stats = RendererStats()
        self._errors = 0

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._started_at = self._clock.monotonic()

    @property
    def stats(self) -> RendererStats:
        return self._stats

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def compositor(self) -> Compositor:
        """Exposed so the coordinator can schedule transients on go-live."""
        return self._compositor

    @property
    def state(self) -> RenderState | None:
        """The most recently submitted snapshot, or ``None`` before the first.

        Read by the web preview, so the browser reports what the strip is
        actually being asked to show — V1 re-implemented the preview in Razor
        and the two drifted apart.
        """
        return self._state

    @property
    def elapsed(self) -> float:
        """Seconds since construction, in the time base effects are sampled with.

        A transient scheduled from the async side must use the renderer's own
        idea of "now" — mixing time bases would start the animation mid-life or
        in the future.
        """
        return self._clock.monotonic() - self._started_at

    def submit(self, state: RenderState) -> None:
        """Publish the next snapshot.

        Called from the event loop. Non-blocking and lock-free: rebinding one
        attribute is atomic, so the render thread either sees the old snapshot
        or the new one, never a half-built mixture.

        Frames are decoupled from submissions. Submitting twice between frames
        simply means the older snapshot is never drawn, which is correct — the
        strip should show current state, not a backlog.
        """
        self._state = state

    def tick(self) -> bool:
        """Render exactly one frame.

        Returns:
            True if a frame reached the driver. False when nothing has been
            submitted yet, or the driver failed.
        """
        state = self._state
        if state is None:
            return False

        elapsed = self._clock.monotonic() - self._started_at
        buffer = self._compositor.render(state, elapsed)

        try:
            for index, color in enumerate(buffer):
                self._driver.set_pixel(index, color)
            self._driver.show()
        except DriverError as exc:
            return self._note_driver_error(exc)

        self._errors = 0
        self._stats.frames += 1
        return True

    # ----------------------------------------------------------------- #
    # Thread lifecycle
    # ----------------------------------------------------------------- #

    def start(self) -> None:
        """Begin rendering on a background thread. Idempotent."""
        if self.is_running:
            return

        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="livepulse-render", daemon=True)
        self._thread.start()
        log.info("renderer started", fps=round(1.0 / self._frame_seconds))

    def stop(self, timeout: float = 2.0) -> None:
        """Stop the thread and blank the strip. Idempotent.

        Blanking matters: leaving 112 LEDs lit after the service stops is what
        V1 did on any exit that was not a clean KeyboardInterrupt.
        """
        self._stop.set()
        thread, self._thread = self._thread, None

        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
            if thread.is_alive():
                log.warning("render thread did not stop in time", timeout=timeout)

        try:
            self._driver.close()
        except DriverError:
            log.exception("driver failed to close cleanly")

        log.info("renderer stopped", frames=self._stats.frames, late=self._stats.late_frames)

    def _run(self) -> None:
        """The thread body: tick, then sleep off whatever budget is left."""
        while not self._stop.is_set():
            frame_started = self._clock.monotonic()

            try:
                self.tick()
            except Exception:
                # A crashed render thread would leave the strip frozen on its
                # last frame with nothing to say why.
                log.exception("unhandled error in render loop")
                self._stats.driver_errors += 1
                if self._give_up():
                    break

            remaining = self._frame_seconds - (self._clock.monotonic() - frame_started)
            if remaining > 0:
                # Event.wait rather than sleep, so stop() is acted on promptly
                # instead of after the rest of the frame budget.
                self._stop.wait(remaining)
            else:
                self._stats.late_frames += 1

    def _note_driver_error(self, exc: DriverError) -> bool:
        self._errors += 1
        self._stats.driver_errors += 1
        self._stats.last_error = str(exc)

        if self._errors == 1 or self._errors == MAX_CONSECUTIVE_ERRORS:
            log.error("driver write failed", error=str(exc), consecutive=self._errors)
        if self._give_up():
            self._stop.set()
        return False

    def _give_up(self) -> bool:
        return self._errors >= MAX_CONSECUTIVE_ERRORS

    def __enter__(self) -> Renderer:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stop()

    def wait_for_frames(self, count: int, timeout: float = 2.0) -> bool:
        """Block until ``count`` frames have rendered. For tests and the CLI."""
        deadline = time.monotonic() + timeout
        target = self._stats.frames + count
        while self._stats.frames < target:
            if time.monotonic() > deadline:
                return False
            time.sleep(0.005)
        return True
