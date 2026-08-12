"""Tests for the per-(provider, model) serviceability aggregate.

The distinction this whole module exists for: a model that answers a
reachability probe while returning 503 on most completions and taking
minutes for a trivial reply is *reachable* and *unserviceable*. The health
summary averages over 24 hours and cannot see it; these aggregates can.
"""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from synthorg.providers.health import (
    ProviderHealthRecord,
    ProviderHealthStatus,
    ProviderOutcomeClass,
    RecordSource,
)
from synthorg.providers.serviceability import (
    ServiceabilityThresholds,
    aggregate_serviceability,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 12, 12, 0, 0, tzinfo=UTC)
_PROVIDER = "test-provider"
_MODEL = "test-large-001"


def _record(
    *,
    outcome: ProviderOutcomeClass = ProviderOutcomeClass.SUCCESS,
    latency_ms: float = 100.0,
    age_seconds: float = 0.0,
    model: str | None = _MODEL,
    provider_name: str = _PROVIDER,
    source: RecordSource = RecordSource.REAL_CALL,
    agent_id: str | None = None,
    task_id: str | None = None,
) -> ProviderHealthRecord:
    """Build one outcome record, defaulting to a fast success."""
    succeeded = outcome is ProviderOutcomeClass.SUCCESS
    return ProviderHealthRecord(
        provider_name=provider_name,
        model=model,
        timestamp=_NOW - timedelta(seconds=age_seconds),
        success=succeeded,
        outcome_class=outcome,
        response_time_ms=latency_ms,
        error_message=None if succeeded else f"upstream said {outcome.value}",
        source=source,
        agent_id=agent_id,
        task_id=task_id,
    )


class TestRecordShape:
    def test_outcome_class_must_agree_with_success(self) -> None:
        # The two fields describe one fact. Letting them disagree would put a
        # failure in the success bucket, which is the one thing the aggregate
        # must never do.
        with pytest.raises(ValidationError):
            ProviderHealthRecord(
                provider_name=_PROVIDER,
                model=_MODEL,
                timestamp=_NOW,
                success=True,
                outcome_class=ProviderOutcomeClass.OVERLOADED,
                response_time_ms=10.0,
                error_message=None,
                source=RecordSource.REAL_CALL,
            )

    def test_failure_must_not_claim_success_outcome(self) -> None:
        with pytest.raises(ValidationError):
            ProviderHealthRecord(
                provider_name=_PROVIDER,
                model=_MODEL,
                timestamp=_NOW,
                success=False,
                outcome_class=ProviderOutcomeClass.SUCCESS,
                response_time_ms=10.0,
                error_message="boom",
                source=RecordSource.REAL_CALL,
            )

    def test_model_is_optional_for_a_probe(self) -> None:
        # A reachability probe calls no model, so it has none to name. It
        # still belongs to the provider's history.
        record = _record(model=None, source=RecordSource.PROBE)
        assert record.model is None
        assert record.source is RecordSource.PROBE

    def test_attribution_defaults_to_absent(self) -> None:
        # No synthetic owner: a call outside a cost scope has no agent and no
        # task, and inventing an id that names no row is worse than None.
        record = _record()
        assert record.agent_id is None
        assert record.task_id is None


class TestOutcomeSplit:
    def test_counts_every_class_that_occurred(self) -> None:
        records = [
            _record(outcome=ProviderOutcomeClass.SUCCESS),
            _record(outcome=ProviderOutcomeClass.SUCCESS),
            _record(outcome=ProviderOutcomeClass.OVERLOADED),
            _record(outcome=ProviderOutcomeClass.OVERLOADED),
            _record(outcome=ProviderOutcomeClass.TIMEOUT),
        ]
        view = aggregate_serviceability(records, now=_NOW)
        assert view.call_count == 5
        assert view.outcome_counts[ProviderOutcomeClass.SUCCESS] == 2
        assert view.outcome_counts[ProviderOutcomeClass.OVERLOADED] == 2
        assert view.outcome_counts[ProviderOutcomeClass.TIMEOUT] == 1

    def test_absent_classes_are_absent_not_zero(self) -> None:
        view = aggregate_serviceability([_record()], now=_NOW)
        assert ProviderOutcomeClass.PAYMENT_REQUIRED not in view.outcome_counts

    def test_a_503_run_is_countable_apart_from_a_500_run(self) -> None:
        # The reported incident: five 503s in eight calls. Reading that as a
        # generic internal error tells an operator to investigate an outage
        # when the model is merely queueing.
        records = [
            *[_record(outcome=ProviderOutcomeClass.OVERLOADED) for _ in range(5)],
            *[_record() for _ in range(2)],
            _record(outcome=ProviderOutcomeClass.TIMEOUT),
        ]
        view = aggregate_serviceability(records, now=_NOW)
        assert view.outcome_counts[ProviderOutcomeClass.OVERLOADED] == 5
        assert ProviderOutcomeClass.INTERNAL not in view.outcome_counts


class TestLatencyDistribution:
    def test_percentiles_expose_the_tail_a_mean_hides(self) -> None:
        # 1.2s, 2.65s, 34.8s, 72.7s, 112.7s, 311s -- the measured incident.
        # The mean reads about 89s, which sounds survivable; p99 is what an
        # operator actually waits for.
        latencies = [1200.0, 2650.0, 34800.0, 72700.0, 112700.0, 311000.0]
        records = [_record(latency_ms=ms) for ms in latencies]
        view = aggregate_serviceability(records, now=_NOW)
        assert view.latency is not None
        assert view.latency.max_ms == pytest.approx(311000.0)
        assert view.latency.p99_ms >= view.latency.p90_ms >= view.latency.p50_ms
        assert view.latency.p50_ms < view.latency.max_ms

    def test_single_sample_reports_that_sample_everywhere(self) -> None:
        view = aggregate_serviceability([_record(latency_ms=42.0)], now=_NOW)
        assert view.latency is not None
        assert view.latency.p50_ms == pytest.approx(42.0)
        assert view.latency.p99_ms == pytest.approx(42.0)
        assert view.latency.max_ms == pytest.approx(42.0)

    def test_no_records_means_no_distribution(self) -> None:
        view = aggregate_serviceability([], now=_NOW)
        assert view.latency is None
        assert view.call_count == 0


class TestVerdict:
    def test_no_calls_is_unknown_not_healthy(self) -> None:
        view = aggregate_serviceability([], now=_NOW)
        assert view.verdict is ProviderHealthStatus.UNKNOWN

    def test_all_successes_is_up(self) -> None:
        view = aggregate_serviceability([_record() for _ in range(10)], now=_NOW)
        assert view.verdict is ProviderHealthStatus.UP

    def test_mostly_failing_is_down(self) -> None:
        records = [
            *[_record(outcome=ProviderOutcomeClass.OVERLOADED) for _ in range(8)],
            *[_record() for _ in range(2)],
        ]
        view = aggregate_serviceability(records, now=_NOW)
        assert view.verdict is ProviderHealthStatus.DOWN

    def test_a_single_failure_cannot_condemn_a_pair(self) -> None:
        # Below the minimum sample the verdict withholds judgement rather
        # than reading DOWN, so one blip cannot take a roster out of service.
        thresholds = ServiceabilityThresholds(min_calls_for_verdict=5)
        view = aggregate_serviceability(
            [_record(outcome=ProviderOutcomeClass.OVERLOADED)],
            now=_NOW,
            thresholds=thresholds,
        )
        assert view.verdict is ProviderHealthStatus.UNKNOWN

    def test_payment_required_is_down_on_sight(self) -> None:
        # An empty balance does not decay and cannot be averaged away: one
        # 402 means the pair serves nothing until an operator acts.
        records = [
            *[_record() for _ in range(20)],
            _record(outcome=ProviderOutcomeClass.PAYMENT_REQUIRED),
        ]
        view = aggregate_serviceability(records, now=_NOW)
        assert view.verdict is ProviderHealthStatus.DOWN

    def test_payment_required_outranks_a_later_success(self) -> None:
        # Deliberately NOT last-write-wins: a provider that serves a cached
        # or free request after refusing a billed one has not been topped up.
        records = [
            _record(outcome=ProviderOutcomeClass.PAYMENT_REQUIRED, age_seconds=30),
            _record(age_seconds=0),
        ]
        view = aggregate_serviceability(records, now=_NOW)
        assert view.verdict is ProviderHealthStatus.DOWN


class TestWindow:
    def test_recent_window_sees_what_a_daily_average_hides(self) -> None:
        # The incident's shape: an hour of failure behind a day of success.
        # Averaged over 24h the pair reads healthy; that is exactly why the
        # existing summary reported nothing.
        recent = [
            _record(outcome=ProviderOutcomeClass.OVERLOADED, age_seconds=60)
            for _ in range(10)
        ]
        older = [_record(age_seconds=6 * 3600) for _ in range(500)]
        thresholds = ServiceabilityThresholds(window_seconds=900.0)
        view = aggregate_serviceability(
            [*older, *recent], now=_NOW, thresholds=thresholds
        )
        assert view.call_count == 10
        assert view.verdict is ProviderHealthStatus.DOWN

    def test_records_outside_the_window_are_excluded(self) -> None:
        thresholds = ServiceabilityThresholds(window_seconds=60.0)
        view = aggregate_serviceability(
            [_record(age_seconds=3600)], now=_NOW, thresholds=thresholds
        )
        assert view.call_count == 0
        assert view.verdict is ProviderHealthStatus.UNKNOWN

    def test_a_future_timestamp_is_excluded(self) -> None:
        # Clock skew on a peer must not let a record from ahead of now
        # dominate a window it does not belong to.
        view = aggregate_serviceability([_record(age_seconds=-120)], now=_NOW)
        assert view.call_count == 0
