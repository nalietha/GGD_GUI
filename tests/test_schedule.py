"""Quiet hours.

The requirement this feature exists for: V1 had no off switch, so it lived
switched off at the wall. These tests pin down the decided behaviour — off
means off, manual override wins until the next boundary, and the schedule
sets the mode without owning it — plus the restart cases that only work
because the acted-on boundary is persisted.

All times run through America/Chicago, deliberately not UTC: crossing
midnight and DST offsets are the normal case, not the edge case.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from livepulse.config.settings import ScheduleSettings
from livepulse.core.models import DisplayMode
from livepulse.schedule import ScheduleService
from livepulse.storage.db import Database
from livepulse.storage.repositories import Repositories
from livepulse.storage.runtime import RuntimeSettingsStore

TZ = ZoneInfo("America/Chicago")

SCHEDULE = ScheduleSettings(enabled=True, timezone="America/Chicago", off="22:00", on="06:00")


def chicago(day: int, hour: int, minute: int = 0, month: int = 1) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=TZ)


@dataclass
class Env:
    repos: Repositories
    store: RuntimeSettingsStore

    def service(self, settings: ScheduleSettings = SCHEDULE) -> ScheduleService:
        return ScheduleService(settings, self.store, self.repos.settings)


@pytest.fixture
async def env() -> AsyncIterator[Env]:
    async with Database(":memory:") as db:
        repos = Repositories(db)
        store = RuntimeSettingsStore(repos.settings)
        await store.load()
        yield Env(repos, store)


class TestBoundaries:
    async def test_evening_crossing_turns_off(self, env: Env) -> None:
        service = env.service()

        # 21:59 — the day's on-boundary is the latest crossing; with no
        # history the schedule records it and touches nothing.
        assert service.tick(chicago(1, 21, 59)) is None
        # 22:01 — quiet hours.
        assert service.tick(chicago(1, 22, 1)) is DisplayMode.OFF

    async def test_morning_crossing_restores_the_previous_mode(self, env: Env) -> None:
        await env.store.update(mode=DisplayMode.AMBIENT)
        service = env.service()

        assert service.tick(chicago(1, 22, 1)) is DisplayMode.OFF
        await env.store.update(mode=DisplayMode.OFF)

        # 06:00 next day — back to what the user actually ran, not a default.
        assert service.tick(chicago(2, 6, 1)) is DisplayMode.AMBIENT

    async def test_manual_override_wins_until_the_next_boundary(self, env: Env) -> None:
        service = env.service()

        assert service.tick(chicago(1, 22, 1)) is DisplayMode.OFF
        # The user turns it back on at 23:00...
        await env.store.update(mode=DisplayMode.STREAMER)

        # ...and every check for the rest of the night leaves it alone.
        assert service.tick(chicago(1, 23, 0)) is None
        assert service.tick(chicago(2, 3, 0)) is None
        # 06:00 re-asserts (a no-op here, but the schedule's turn again).
        assert service.tick(chicago(2, 6, 1)) is DisplayMode.STREAMER

    async def test_user_off_does_not_become_the_resume_mode(self, env: Env) -> None:
        service = env.service()
        assert service.tick(chicago(1, 12, 0)) is None  # record daytime boundary

        # The user switches off at 21:00, then the schedule's 22:00 arrives.
        await env.store.update(mode=DisplayMode.OFF)
        assert service.tick(chicago(1, 22, 1)) is DisplayMode.OFF

        # 06:00 restores STREAMER — not the OFF the user happened to be in.
        assert service.tick(chicago(2, 6, 1)) is DisplayMode.STREAMER

    async def test_next_boundary_crosses_midnight(self, env: Env) -> None:
        service = env.service()

        upcoming = service.next_boundary(chicago(1, 23, 0))

        assert upcoming == chicago(2, 6, 0)


class TestStartup:
    async def test_boot_during_quiet_hours_goes_dark(self, env: Env) -> None:
        """3 AM boot: the stand must not light the bedroom while people sleep."""
        service = env.service()

        assert service.tick(chicago(2, 3, 0)) is DisplayMode.OFF

    async def test_boot_during_the_day_changes_nothing(self, env: Env) -> None:
        """First enablement during the day: whatever mode is set, the user set it."""
        await env.store.update(mode=DisplayMode.OFF)  # a deliberate manual off
        service = env.service()

        assert service.tick(chicago(1, 10, 0)) is None
        assert env.store.current.mode is DisplayMode.OFF

    async def test_power_cut_overnight_still_resumes_in_the_morning(self, env: Env) -> None:
        """The restart case the persistence exists for.

        The schedule set OFF at 22:00 and the Pi lost power at midnight. It
        boots at 06:30 with the database saying OFF — and must know that OFF
        was the schedule's doing, so the morning boundary lifts it.
        """
        first = env.service()
        assert first.tick(chicago(1, 22, 1)) is DisplayMode.OFF
        await env.store.update(mode=DisplayMode.OFF)
        await first._persist()

        # A new process, fresh in-memory state, same database.
        rebooted = env.service()
        await rebooted.load()

        assert rebooted.tick(chicago(2, 6, 30)) is DisplayMode.STREAMER

    async def test_restart_does_not_stomp_a_manual_daytime_off(self, env: Env) -> None:
        first = env.service()
        assert first.tick(chicago(2, 6, 1)) is None  # no history: records, no change
        assert first.tick(chicago(2, 6, 2)) is None
        await first._persist()

        # 09:00: the user switches off. 10:00: the service restarts.
        await env.store.update(mode=DisplayMode.OFF)
        rebooted = env.service()
        await rebooted.load()

        assert rebooted.tick(chicago(2, 10, 0)) is None
        assert env.store.current.mode is DisplayMode.OFF


class TestTimekeeping:
    async def test_boundaries_follow_the_wall_clock_through_dst(self, env: Env) -> None:
        """22:00 means 22:00 on the wall, before and after the clocks change.

        Chicago springs forward on 2026-03-08: UTC offset moves from -6 to
        -5. The off boundary must stay at 22:00 local on both sides.
        """
        service = env.service()

        before = service.latest_boundary(chicago(7, 23, 0, month=3))
        after = service.latest_boundary(chicago(9, 23, 0, month=3))

        assert before.at.hour == 22 and before.quiet
        assert after.at.hour == 22 and after.quiet
        # Same wall time, different UTC instants — the offset really moved.
        assert before.at.astimezone(UTC).hour != after.at.astimezone(UTC).hour

    async def test_a_clock_jump_is_caught_by_the_bounded_recheck(self, env: Env) -> None:
        """NTP settling after boot can move time hours forward in one step."""
        service = env.service()
        assert service.tick(chicago(1, 21, 0)) is None  # booted before quiet hours

        # The clock jumps straight past the boundary.
        assert service.tick(chicago(2, 2, 0)) is DisplayMode.OFF
