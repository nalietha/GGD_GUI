"""Quiet hours — the schedule that turns the display off and on.

The requirement behind this is blunt: V1 had no off switch, so its Pi was
unplugged at the wall and never came back. Sixteen lit nodes in a room someone
sleeps in is not something you leave running.

Decided behaviour (recorded in the plan's operating model, §16):

* **Off means off.** A go-live during quiet hours does not wake the stand.
  ``OFF`` is dark-but-running — ingestion continues, so the lights are correct
  the instant they return, with nothing missed and no catch-up.
* **Manual override wins until the next boundary.** The schedule acts exactly
  once per boundary crossing. Someone turning the lights back on at 23:00
  keeps them on until 06:00 re-asserts; they are never fighting a timer that
  keeps switching them off every minute.
* **The schedule sets mode, it does not own it.** It writes the same
  ``RuntimeSettings.mode`` the UI and the CLI write, through the same store —
  one path, one source of truth, which is the lesson of V1's central bug.

The acted-on boundary and the resume mode are persisted, because restarts
interact with both rules: without that memory, a boot at 06:30 after a power
cut during quiet hours would stay dark until the next night, and a restart at
10:00 would stomp a mode the user chose at 09:00. With it, a boundary is acted
on exactly once *across* restarts.

Timekeeping is deliberately paranoid: the configured IANA timezone is used
(never system local — a Pi image ships set to UTC), boundaries are computed
per calendar date so DST transitions land where the wall clock says, and the
loop re-checks at a bounded interval so a clock jump — NTP settling after boot
on a Pi with no RTC — is noticed within minutes rather than slept through.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import structlog

from livepulse.config.settings import ScheduleSettings
from livepulse.core.clock import Clock, SystemClock
from livepulse.core.models import DisplayMode
from livepulse.storage.repositories import SettingsRepository
from livepulse.storage.runtime import RuntimeSettingsStore

__all__ = ["Boundary", "ScheduleService"]

log = structlog.get_logger(__name__)

#: The longest the loop sleeps between checks. Bounds how long a clock jump
#: can go unnoticed; costs one datetime comparison every few minutes.
MAX_SLEEP = 300.0

#: Margin after a boundary before the sleep ends, so a wake-up scheduled for
#: exactly 22:00:00.000 cannot land at 21:59:59.999 and miss it.
BOUNDARY_SLACK = 1.0

#: Settings-table keys for the schedule's own memory. The leading underscore
#: marks them as internal state, which the runtime settings store ignores.
APPLIED_KEY = "_schedule.applied"
RESUME_KEY = "_schedule.resume"


@dataclass(frozen=True, slots=True)
class Boundary:
    """One schedule crossing: at this moment, this state begins."""

    at: datetime
    quiet: bool
    """True for the off boundary, False for the on boundary."""


class ScheduleService:
    """Applies quiet hours by writing the display mode at boundaries.

    The decision logic is synchronous and driven by :meth:`tick`, which the
    tests call with a manual clock; :meth:`run` only adds sleeping and
    persistence — the same split the renderer and the scheduler use.
    """

    __slots__ = (
        "_applied",
        "_clock",
        "_resume",
        "_settings",
        "_state",
        "_stopping",
        "_store",
        "_tz",
    )

    def __init__(
        self,
        settings: ScheduleSettings,
        store: RuntimeSettingsStore,
        state: SettingsRepository,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._state = state
        self._clock = clock or SystemClock()
        self._tz = ZoneInfo(settings.timezone)
        self._stopping = asyncio.Event()

        self._applied: datetime | None = None
        """The boundary most recently acted on. Acting once per boundary is
        what makes a manual override stick until the next one."""

        self._resume: DisplayMode = DisplayMode.STREAMER
        """What the on boundary restores — the mode the user was in when
        quiet hours began."""

    async def load(self) -> None:
        """Restore the acted-boundary and resume mode from storage."""
        raw_applied = await self._state.get(APPLIED_KEY)
        if isinstance(raw_applied, str):
            with contextlib.suppress(ValueError):
                self._applied = datetime.fromisoformat(raw_applied)

        raw_resume = await self._state.get(RESUME_KEY)
        if isinstance(raw_resume, str):
            with contextlib.suppress(ValueError):
                self._resume = DisplayMode(raw_resume)

    # ----------------------------------------------------------------- #
    # Decision logic (pure, testable)
    # ----------------------------------------------------------------- #

    def latest_boundary(self, now: datetime) -> Boundary:
        """The most recent crossing at or before ``now``.

        Boundaries are anchored to wall-clock times per calendar date, so a
        DST shift moves them exactly as it would move an alarm clock.
        Searching today and yesterday always finds one, midnight-crossing
        included.
        """
        local = now.astimezone(self._tz)

        candidates = []
        for days_back in (0, 1):
            date = (local - timedelta(days=days_back)).date()
            for at, quiet in ((self._settings.off, True), (self._settings.on, False)):
                candidate = datetime.combine(date, at, tzinfo=self._tz)
                if candidate <= local:
                    candidates.append(Boundary(at=candidate, quiet=quiet))

        return max(candidates, key=lambda boundary: boundary.at)

    def next_boundary(self, now: datetime) -> datetime:
        """When the schedule next needs to act."""
        local = now.astimezone(self._tz)

        upcoming = [
            candidate
            for days_ahead in (0, 1)
            for at in (self._settings.off, self._settings.on)
            if (
                candidate := datetime.combine(
                    (local + timedelta(days=days_ahead)).date(), at, tzinfo=self._tz
                )
            )
            > local
        ]
        return min(upcoming)

    def tick(self, now: datetime) -> DisplayMode | None:
        """Decide whether a mode change is due, acting once per boundary.

        Returns:
            The mode to apply, or ``None`` when this boundary was already
            handled — which is also what leaves a manual override alone.
        """
        boundary = self.latest_boundary(now)
        if self._applied is not None and boundary.at <= self._applied:
            return None

        never_acted = self._applied is None
        self._applied = boundary.at

        if boundary.quiet:
            # Remember what to come back to — unless the user had already
            # switched off themselves, which must not become the resume state.
            if self._store.current.mode is not DisplayMode.OFF:
                self._resume = self._store.current.mode
            return DisplayMode.OFF

        if never_acted:
            # A schedule with no history reaching an *on* boundary — first
            # enablement, during the day. Whatever mode is set, the user set
            # it; asserting anything here would be the schedule owning the
            # mode rather than setting it. (Starting with history is the
            # power-cut-overnight case, and does re-assert.)
            return None
        return self._resume

    # ----------------------------------------------------------------- #
    # The loop
    # ----------------------------------------------------------------- #

    async def run(self) -> None:
        """Apply the schedule until stopped."""
        self._stopping.clear()
        log.info(
            "schedule active",
            off=self._settings.off.isoformat("minutes"),
            on=self._settings.on.isoformat("minutes"),
            timezone=self._settings.timezone,
        )

        while not self._stopping.is_set():
            now = self._clock.now()

            target = self.tick(now)
            if target is not None:
                if self._store.current.mode is not target:
                    log.info("schedule changing display mode", mode=target.value)
                    await self._store.update(mode=target)
                await self._persist()

            until_next = (self.next_boundary(now) - now).total_seconds() + BOUNDARY_SLACK
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stopping.wait(), timeout=max(1.0, min(until_next, MAX_SLEEP))
                )

        log.info("schedule stopped")

    def stop(self) -> None:
        self._stopping.set()

    async def _persist(self) -> None:
        """Remember what was acted on, so a restart does not act again."""
        if self._applied is not None:
            await self._state.set(APPLIED_KEY, self._applied.isoformat())
        await self._state.set(RESUME_KEY, self._resume.value)
