"""The frame loop.

``tick()`` renders exactly one frame, so most of this drives it directly with a
ManualClock — deterministic, instant, no sleeping. The thread tests are kept
few and are about lifecycle, not timing.
"""

from __future__ import annotations

import pytest

from livepulse.config.settings import LayoutSettings
from livepulse.core.clock import ManualClock
from livepulse.core.errors import DriverError
from livepulse.core.models import DisplayMode
from livepulse.leds.color import BLACK, RGB, WHITE, IdentityGamma
from livepulse.leds.compositor import MASTER_FADE_SECONDS, Compositor
from livepulse.leds.driver.sim import SimulatedDriver
from livepulse.leds.layout import Layout
from livepulse.leds.renderer import MAX_CONSECUTIVE_ERRORS, Renderer
from livepulse.leds.state import NodeView, RenderState

RED = RGB(255, 0, 0)


def build(nodes: int = 2, leds_per_node: int = 1, fps: int = 60):
    layout = Layout(
        LayoutSettings(nodes=nodes, leds_per_node=leds_per_node, order="linear", rows=1)
    )
    driver = SimulatedDriver(layout.pixel_count)
    clock = ManualClock()
    renderer = Renderer(driver, Compositor(layout, IdentityGamma()), fps=fps, clock=clock)
    return renderer, driver, clock


def snapshot(*, color: RGB = WHITE, mode: DisplayMode = DisplayMode.STREAMER) -> RenderState:
    return RenderState(
        nodes=tuple(
            NodeView(
                node_id=n,
                effect="static",
                color=color,
                alt_color=None,
                params={},
                is_bound=True,
                is_live=True,
            )
            for n in range(2)
        ),
        mode=mode,
        brightness=255,
        ambient_active=False,
    )


class TestTick:
    def test_does_nothing_before_a_snapshot_is_submitted(self) -> None:
        """Startup order must not matter — the thread may run first."""
        renderer, driver, _ = build()
        assert renderer.tick() is False
        assert driver.show_count == 0

    def test_renders_a_submitted_snapshot(self) -> None:
        renderer, driver, _ = build()
        renderer.submit(snapshot())

        assert renderer.tick() is True
        assert driver.show_count == 1

    def test_the_frame_reaches_the_driver(
        self,
    ) -> None:
        renderer, driver, clock = build()
        renderer.submit(snapshot(color=RED))

        renderer.tick()
        clock.advance(MASTER_FADE_SECONDS * 2)
        renderer.tick()

        assert driver.frame == (RED, RED)

    def test_counts_frames(self) -> None:
        renderer, _, clock = build()
        renderer.submit(snapshot())
        for _ in range(5):
            renderer.tick()
            clock.advance(1 / 60)
        assert renderer.stats.frames == 5

    def test_animation_advances_with_the_clock(self) -> None:
        """Effects are functions of time, so the clock is what moves them."""
        renderer, driver, clock = build()
        renderer.submit(
            RenderState(
                nodes=(
                    NodeView(0, "pulse", WHITE, None, {"period": 2.0}, True, True),
                    NodeView(1, "static", WHITE, None, {}, True, True),
                ),
                mode=DisplayMode.STREAMER,
                brightness=255,
                ambient_active=False,
            )
        )
        renderer.tick()
        clock.advance(MASTER_FADE_SECONDS * 2)
        renderer.tick()
        first = driver.frame

        # Half a period later the pulse must be somewhere else...
        clock.advance(1.0)
        renderer.tick()
        assert driver.frame[0] != first[0]
        assert driver.frame[1] == first[1], "the static node should not move"

        # ...and a full period after that, back where it was.
        clock.advance(1.0)
        renderer.tick()
        assert driver.frame[0] == first[0]


class TestSubmit:
    def test_the_latest_snapshot_wins(self) -> None:
        """Frames are decoupled from submissions: the strip shows current state,
        not a backlog. Submitting twice between frames drops the older one."""
        renderer, driver, clock = build()
        renderer.submit(snapshot(color=RED))
        renderer.submit(snapshot(color=WHITE))

        renderer.tick()
        clock.advance(MASTER_FADE_SECONDS * 2)
        renderer.tick()

        assert driver.frame == (WHITE, WHITE)

    def test_resubmitting_is_cheap_and_does_not_render(self) -> None:
        """submit() is called from the event loop and must never block on I/O."""
        renderer, driver, _ = build()
        for _ in range(100):
            renderer.submit(snapshot())
        assert driver.show_count == 0


class TestDriverFailures:
    def test_a_failed_write_is_reported_not_raised(self) -> None:
        """One bad SPI write should not take down the loop."""
        renderer, driver, _ = build()
        renderer.submit(snapshot())
        driver.close()  # show() now raises

        assert renderer.tick() is False
        assert renderer.stats.driver_errors == 1
        assert renderer.stats.last_error is not None

    def test_gives_up_after_repeated_failures(self) -> None:
        """Spinning on dead hardware would just fill the journal."""
        renderer, driver, _ = build()
        renderer.submit(snapshot())
        driver.close()

        for _ in range(MAX_CONSECUTIVE_ERRORS + 2):
            renderer.tick()

        assert renderer._stop.is_set()

    def test_the_error_streak_resets_after_a_good_frame(self) -> None:
        renderer, driver, _ = build()
        renderer.submit(snapshot())

        original = driver.show
        calls = {"n": 0}

        def flaky() -> None:
            calls["n"] += 1
            if calls["n"] <= 3:
                raise DriverError("transient")
            original()

        driver.show = flaky  # type: ignore[method-assign]

        for _ in range(6):
            renderer.tick()

        assert renderer._errors == 0, "a good frame should clear the streak"
        assert not renderer._stop.is_set()


class TestThreadLifecycle:
    def test_start_and_stop(self) -> None:
        renderer, _, _ = build(fps=240)
        renderer.submit(snapshot())

        renderer.start()
        try:
            assert renderer.wait_for_frames(3), "renderer produced no frames"
            assert renderer.is_running
        finally:
            renderer.stop()

        assert not renderer.is_running

    def test_start_is_idempotent(self) -> None:
        renderer, _, _ = build(fps=240)
        renderer.submit(snapshot())
        renderer.start()
        try:
            first = renderer._thread
            renderer.start()
            assert renderer._thread is first
        finally:
            renderer.stop()

    def test_stop_is_idempotent(self) -> None:
        renderer, _, _ = build()
        renderer.stop()
        renderer.stop()

    def test_stop_blanks_the_strip(self) -> None:
        """V1 left 112 LEDs lit on any exit that was not a clean interrupt."""
        renderer, driver, _ = build(fps=240)
        renderer.submit(snapshot())
        renderer.start()
        renderer.wait_for_frames(2)
        renderer.stop()

        assert driver.frame == (BLACK, BLACK)
        assert driver.closed

    def test_context_manager_starts_and_stops(self) -> None:
        renderer, driver, _ = build(fps=240)
        renderer.submit(snapshot())

        with renderer:
            assert renderer.wait_for_frames(2)

        assert driver.closed

    def test_the_thread_is_a_daemon(self) -> None:
        """A stuck render thread must not keep the process alive at shutdown."""
        renderer, _, _ = build(fps=240)
        renderer.submit(snapshot())
        renderer.start()
        try:
            assert renderer._thread is not None
            assert renderer._thread.daemon
        finally:
            renderer.stop()


class TestConstruction:
    @pytest.mark.parametrize("fps", [0, -1])
    def test_rejects_a_non_positive_frame_rate(self, fps: int) -> None:
        layout = Layout(LayoutSettings(nodes=1, leds_per_node=1, order="linear", rows=1))
        with pytest.raises(ValueError, match="fps must be positive"):
            Renderer(SimulatedDriver(1), Compositor(layout, IdentityGamma()), fps=fps)

    def test_exposes_the_compositor_for_transients(self) -> None:
        """The coordinator schedules go-live flashes through this."""
        renderer, _, _ = build()
        renderer.compositor.add_transient({0}, "flash", now=0.0)
        assert renderer.compositor.active_transients == 1
