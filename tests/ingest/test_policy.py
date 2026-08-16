"""Polling cadence and failure isolation."""

from __future__ import annotations

import random

import pytest

from livepulse.config.settings import IngestSettings
from livepulse.core.clock import ManualClock
from livepulse.ingest.policy import BACKOFF_AFTER, BreakerState, CircuitBreaker, IntervalPolicy


def policy(**overrides: object) -> IntervalPolicy:
    settings = IngestSettings(**overrides)  # type: ignore[arg-type]
    # Seeded so every scheduling assertion is deterministic; the module-level
    # random would make them flaky.
    return IntervalPolicy(settings, random.Random(1))


class TestIntervalPolicy:
    def test_a_live_channel_polls_at_the_base_rate(self) -> None:
        """Going offline should show up promptly."""
        assert policy().interval_for(is_live=True, consecutive_offline=0) == 30.0

    def test_a_briefly_offline_channel_still_polls_promptly(self) -> None:
        """A quick restart between streams must be picked up fast."""
        assert policy().interval_for(is_live=False, consecutive_offline=1) == 30.0

    def test_a_long_offline_channel_backs_off(self) -> None:
        """Someone who has not streamed in a week does not need checking every
        30 seconds; the saved budget goes to channels that might change."""
        short = policy().interval_for(is_live=False, consecutive_offline=BACKOFF_AFTER)
        longer = policy().interval_for(is_live=False, consecutive_offline=BACKOFF_AFTER + 20)
        assert short > 30.0
        assert longer > short

    def test_backoff_is_capped(self) -> None:
        assert policy().interval_for(is_live=False, consecutive_offline=10_000) == 300.0

    def test_going_live_resets_to_the_base_rate(self) -> None:
        """The count is cleared by the scheduler; confirm the policy honours it."""
        assert policy().interval_for(is_live=True, consecutive_offline=0) == 30.0

    def test_a_pushed_channel_drops_to_reconciliation(self) -> None:
        """Not zero — a silently dead socket must not freeze that node."""
        assert policy().interval_for(is_live=True, consecutive_offline=0, pushed=True) == 600.0

    def test_a_provider_minimum_always_wins(self) -> None:
        """Configuration may ask for faster than a platform permits; it does not get it."""
        interval = policy(base_poll_interval=5.0).interval_for(
            is_live=True, consecutive_offline=0, provider_minimum=120.0
        )
        assert interval == 120.0

    def test_jitter_only_ever_delays(self) -> None:
        """Pulling a poll earlier could breach a provider's declared minimum."""
        subject = policy()
        for _ in range(50):
            due = subject.next_due(now=100.0, interval=30.0)
            assert 130.0 <= due <= 130.0 + 30.0 * 0.15

    def test_jitter_spreads_channels_apart(self) -> None:
        """Otherwise N channels stampede the API in the same tick."""
        subject = policy()
        times = {subject.next_due(now=0.0, interval=30.0) for _ in range(20)}
        assert len(times) > 1

    def test_zero_jitter_is_exact(self) -> None:
        assert policy(jitter=0.0).next_due(now=100.0, interval=30.0) == 130.0


class TestCircuitBreaker:
    def test_starts_closed(self) -> None:
        breaker = CircuitBreaker()
        assert breaker.state is BreakerState.CLOSED
        assert breaker.allow() is True

    def test_opens_at_the_threshold(self) -> None:
        breaker = CircuitBreaker(threshold=3)
        for _ in range(2):
            breaker.record_failure()
        assert breaker.allow() is True

        breaker.record_failure()
        assert breaker.state is BreakerState.OPEN
        assert breaker.allow() is False

    def test_success_resets_the_count(self) -> None:
        breaker = CircuitBreaker(threshold=3)
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_success()
        breaker.record_failure()

        assert breaker.allow() is True

    def test_half_opens_after_the_recovery_window(self) -> None:
        clock = ManualClock()
        breaker = CircuitBreaker(threshold=1, recovery=60.0, clock=clock)
        breaker.record_failure()
        assert breaker.allow() is False

        clock.advance(60.0)
        assert breaker.state is BreakerState.HALF_OPEN
        assert breaker.allow() is True, "one probe should be let through"

    def test_a_failed_probe_reopens_immediately(self) -> None:
        """The probe was the test. Failing it does not earn another four tries."""
        clock = ManualClock()
        breaker = CircuitBreaker(threshold=5, recovery=60.0, clock=clock)
        breaker.trip()
        clock.advance(60.0)
        assert breaker.state is BreakerState.HALF_OPEN

        breaker.record_failure()
        assert breaker.state is BreakerState.OPEN

    def test_a_successful_probe_closes_it(self) -> None:
        clock = ManualClock()
        breaker = CircuitBreaker(threshold=1, recovery=60.0, clock=clock)
        breaker.record_failure()
        clock.advance(60.0)
        breaker.record_success()

        assert breaker.state is BreakerState.CLOSED
        assert breaker.failures == 0

    def test_trip_opens_at_once(self) -> None:
        """A rejected credential is not transient; four more tries help nobody."""
        breaker = CircuitBreaker(threshold=5)
        breaker.trip()
        assert breaker.allow() is False

    def test_rejects_a_zero_threshold(self) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            CircuitBreaker(threshold=0)
