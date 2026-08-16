"""Token-bucket rate limiting."""

from __future__ import annotations

import pytest

from livepulse.core.clock import ManualClock
from livepulse.ingest.ratelimit import TokenBucket


class TestAcquire:
    def test_starts_full_so_a_cold_start_can_burst(self) -> None:
        """After an idle stretch the scheduler wants every channel at once;
        a strict rate would drip them out over minutes."""
        bucket = TokenBucket(rate=2.0, capacity=10.0, clock=ManualClock())
        assert all(bucket.try_acquire() for _ in range(10))

    def test_refuses_when_empty(self) -> None:
        bucket = TokenBucket(rate=1.0, capacity=2.0, clock=ManualClock())
        bucket.try_acquire()
        bucket.try_acquire()
        assert bucket.try_acquire() is False

    def test_refills_over_time(self) -> None:
        clock = ManualClock()
        bucket = TokenBucket(rate=2.0, capacity=2.0, clock=clock)
        bucket.try_acquire()
        bucket.try_acquire()

        clock.advance(1.0)  # two tokens per second
        assert bucket.try_acquire() is True
        assert bucket.try_acquire() is True
        assert bucket.try_acquire() is False

    def test_never_refills_past_capacity(self) -> None:
        clock = ManualClock()
        bucket = TokenBucket(rate=1.0, capacity=3.0, clock=clock)
        clock.advance(3600.0)
        assert bucket.tokens == 3.0

    def test_never_blocks(self) -> None:
        """The scheduler calls this inline; sleeping here would stall the loop."""
        bucket = TokenBucket(rate=1.0, capacity=1.0, clock=ManualClock())
        bucket.try_acquire()
        assert bucket.try_acquire() is False


class TestTimeUntil:
    def test_is_zero_when_tokens_are_available(self) -> None:
        assert TokenBucket(rate=1.0, clock=ManualClock()).time_until() == 0.0

    def test_reports_the_wait_when_empty(self) -> None:
        bucket = TokenBucket(rate=2.0, capacity=1.0, clock=ManualClock())
        bucket.try_acquire()
        assert bucket.time_until() == pytest.approx(0.5)


class TestPenalty:
    def test_a_penalty_refuses_everything(self) -> None:
        """Driven by Retry-After, so we wait what was actually asked for."""
        clock = ManualClock()
        bucket = TokenBucket(rate=100.0, capacity=100.0, clock=clock)
        bucket.penalise(30.0)

        assert bucket.penalised is True
        assert bucket.try_acquire() is False

    def test_the_penalty_expires(self) -> None:
        clock = ManualClock()
        bucket = TokenBucket(rate=100.0, capacity=100.0, clock=clock)
        bucket.penalise(30.0)

        clock.advance(30.0)
        assert bucket.penalised is False
        assert bucket.try_acquire() is True

    def test_a_penalty_drains_the_bucket(self) -> None:
        """Otherwise the moment the penalty lifts we burst straight into another 429."""
        clock = ManualClock()
        bucket = TokenBucket(rate=1.0, capacity=100.0, clock=clock)
        bucket.penalise(10.0)
        clock.advance(10.0)

        assert bucket.tokens < 100.0

    def test_time_until_accounts_for_the_penalty(self) -> None:
        clock = ManualClock()
        bucket = TokenBucket(rate=100.0, capacity=100.0, clock=clock)
        bucket.penalise(30.0)
        assert bucket.time_until() == pytest.approx(30.0)

    def test_a_non_positive_penalty_is_ignored(self) -> None:
        bucket = TokenBucket(rate=1.0, clock=ManualClock())
        bucket.penalise(0.0)
        assert bucket.penalised is False


class TestConstruction:
    @pytest.mark.parametrize("rate", [0.0, -1.0])
    def test_rejects_a_non_positive_rate(self, rate: float) -> None:
        with pytest.raises(ValueError, match="rate must be positive"):
            TokenBucket(rate=rate)

    def test_rejects_a_capacity_below_one(self) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            TokenBucket(rate=1.0, capacity=0.5)

    def test_capacity_defaults_to_the_rate(self) -> None:
        assert TokenBucket(rate=5.0, clock=ManualClock()).tokens == 5.0
