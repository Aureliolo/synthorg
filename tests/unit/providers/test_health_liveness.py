"""Liveness: whether a provider is serving, as opposed to how it has been.

The two questions used to share one 24-hour average, and the answer was wrong
for both. A provider fixed a minute ago went on reporting DOWN because a day of
failures outvoted every call since, and the one control offered for that, a
manual recheck, could only add a single sample to the same losing arithmetic.
"""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.providers.health import (
    LIVENESS_SAMPLE_SIZE,
    ProviderHealthRecord,
    ProviderHealthStatus,
    ProviderReachability,
)
from synthorg.providers.health_tracker import ProviderHealthTracker

_NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


def _record(
    *,
    provider_name: str = "test-provider",
    at: datetime,
    success: bool = True,
) -> ProviderHealthRecord:
    """Build one outcome record.

    Returns:
        The record, with an error message supplied iff it failed.
    """
    return ProviderHealthRecord(
        provider_name=provider_name,
        timestamp=at,
        success=success,
        response_time_ms=100.0,
        error_message=None if success else "refused",
    )


async def _fill(
    tracker: ProviderHealthTracker,
    *,
    successes: int = 0,
    failures: int = 0,
    provider_name: str = "test-provider",
    start: datetime = _NOW - timedelta(hours=6),
) -> datetime:
    """Record failures then successes, one second apart, oldest first.

    Returns:
        The timestamp of the last record written.
    """
    at = start
    for _ in range(failures):
        await tracker.record(_record(provider_name=provider_name, at=at, success=False))
        at += timedelta(seconds=1)
    for _ in range(successes):
        await tracker.record(_record(provider_name=provider_name, at=at))
        at += timedelta(seconds=1)
    return at


@pytest.mark.unit
class TestLivenessVerdict:
    async def test_no_records_is_unknown(self) -> None:
        tracker = ProviderHealthTracker()
        summary = await tracker.get_summary("test-provider", now=_NOW)
        assert summary.health_status is ProviderHealthStatus.UNKNOWN

    async def test_only_the_newest_outcomes_decide(self) -> None:
        """A long tail of failures cannot outvote the recent record.

        The failures are still in the window and still in the 24h error rate;
        they simply stop being evidence about whether the provider is serving
        now, which is the question the badge claims to answer.
        """
        tracker = ProviderHealthTracker()
        await _fill(tracker, failures=50, successes=LIVENESS_SAMPLE_SIZE)

        summary = await tracker.get_summary("test-provider", now=_NOW)
        assert summary.health_status is ProviderHealthStatus.UP
        assert summary.liveness_calls == LIVENESS_SAMPLE_SIZE
        assert summary.calls_last_24h == 50 + LIVENESS_SAMPLE_SIZE
        assert summary.error_rate_percent_24h > 90.0

    async def test_recent_failures_read_down_despite_a_clean_day(self) -> None:
        tracker = ProviderHealthTracker()
        await _fill(tracker, successes=200)
        await _fill(
            tracker,
            failures=LIVENESS_SAMPLE_SIZE,
            start=_NOW - timedelta(minutes=1),
        )

        summary = await tracker.get_summary("test-provider", now=_NOW)
        assert summary.health_status is ProviderHealthStatus.DOWN
        assert summary.error_rate_percent_24h < 10.0

    async def test_one_failure_among_the_newest_is_degraded(self) -> None:
        tracker = ProviderHealthTracker()
        await _fill(tracker, failures=1)
        await _fill(
            tracker,
            successes=LIVENESS_SAMPLE_SIZE - 1,
            start=_NOW - timedelta(minutes=1),
        )

        summary = await tracker.get_summary("test-provider", now=_NOW)
        assert summary.health_status is ProviderHealthStatus.DEGRADED


@pytest.mark.unit
class TestSupersedeLiveness:
    async def test_a_superseded_provider_with_no_new_call_is_unknown(self) -> None:
        """Superseding alone claims nothing; it only retires the old evidence.

        Reporting UP here would be inventing a verdict from no observation,
        which is the failure mode in the opposite direction.
        """
        tracker = ProviderHealthTracker()
        last = await _fill(tracker, failures=6)
        await tracker.supersede_liveness("test-provider", at=last)

        summary = await tracker.get_summary("test-provider", now=_NOW)
        assert summary.health_status is ProviderHealthStatus.UNKNOWN
        assert summary.liveness_calls == 0

    async def test_one_success_after_superseding_reads_up(self) -> None:
        """The behaviour the operator presses Recheck for."""
        tracker = ProviderHealthTracker()
        last = await _fill(tracker, failures=20)
        assert (
            await tracker.get_summary("test-provider", now=_NOW)
        ).health_status is ProviderHealthStatus.DOWN

        await tracker.supersede_liveness("test-provider", at=last)
        await tracker.record(_record(at=last + timedelta(seconds=1)))

        summary = await tracker.get_summary("test-provider", now=_NOW)
        assert summary.health_status is ProviderHealthStatus.UP

    async def test_superseding_preserves_the_reliability_record(self) -> None:
        """Turning the badge green must not rewrite what happened.

        Deleting the failures would produce the same badge and cost the
        operator the only record that the outage occurred.
        """
        tracker = ProviderHealthTracker()
        last = await _fill(tracker, failures=20)
        await tracker.supersede_liveness("test-provider", at=last)
        await tracker.record(_record(at=last + timedelta(seconds=1)))

        summary = await tracker.get_summary("test-provider", now=_NOW)
        assert summary.calls_last_24h == 21
        assert summary.error_rate_percent_24h == pytest.approx(95.24, abs=0.01)

    async def test_a_failure_after_superseding_still_reads_down(self) -> None:
        """The reset is not a promise that the provider works."""
        tracker = ProviderHealthTracker()
        last = await _fill(tracker, successes=20)
        await tracker.supersede_liveness("test-provider", at=last)
        await tracker.record(_record(at=last + timedelta(seconds=1), success=False))

        summary = await tracker.get_summary("test-provider", now=_NOW)
        assert summary.health_status is ProviderHealthStatus.DOWN

    async def test_superseding_one_provider_leaves_its_peers_alone(self) -> None:
        tracker = ProviderHealthTracker()
        last = await _fill(tracker, failures=8, provider_name="alpha")
        _ = await _fill(tracker, failures=8, provider_name="beta")
        await tracker.supersede_liveness("alpha", at=last)
        await tracker.record(
            _record(provider_name="alpha", at=last + timedelta(seconds=1))
        )

        summaries = await tracker.get_all_summaries(now=_NOW)
        assert summaries["alpha"].health_status is ProviderHealthStatus.UP
        assert summaries["beta"].health_status is ProviderHealthStatus.DOWN


@pytest.mark.unit
class TestReachability:
    async def test_no_providers_is_ok(self) -> None:
        tracker = ProviderHealthTracker()
        assert await tracker.reachability(now=_NOW) is ProviderReachability.OK

    async def test_unknown_alone_is_ok(self) -> None:
        """A provider nothing has called is not evidence of a problem."""
        tracker = ProviderHealthTracker()
        last = await _fill(tracker, failures=3, provider_name="alpha")
        await tracker.supersede_liveness("alpha", at=last + timedelta(seconds=1))
        assert await tracker.reachability(now=_NOW) is ProviderReachability.OK

    async def test_a_degraded_provider_is_reported_not_folded_into_ok(self) -> None:
        """The regression this replaced a boolean to prevent."""
        tracker = ProviderHealthTracker()
        await _fill(tracker, failures=1, successes=LIVENESS_SAMPLE_SIZE - 1)
        assert await tracker.reachability(now=_NOW) is ProviderReachability.DEGRADED

    async def test_down_beats_degraded(self) -> None:
        tracker = ProviderHealthTracker()
        await _fill(
            tracker,
            failures=1,
            successes=LIVENESS_SAMPLE_SIZE - 1,
            provider_name="alpha",
        )
        await _fill(tracker, failures=LIVENESS_SAMPLE_SIZE, provider_name="beta")
        assert await tracker.reachability(now=_NOW) is ProviderReachability.DOWN

    async def test_all_healthy_is_ok(self) -> None:
        tracker = ProviderHealthTracker()
        await _fill(tracker, successes=5, provider_name="alpha")
        await _fill(tracker, successes=5, provider_name="beta")
        assert await tracker.reachability(now=_NOW) is ProviderReachability.OK
