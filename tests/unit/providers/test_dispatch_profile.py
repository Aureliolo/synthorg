"""An agent's own dispatch record, joined live and honest about its size."""

from datetime import UTC, date, datetime, timedelta

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.types import NotBlankStr
from synthorg.providers.dispatch_profile import (
    DEFAULT_MIN_CALLS_FOR_PROFILE,
    build_dispatch_profile,
)
from synthorg.providers.health import (
    ProviderHealthRecord,
    ProviderOutcomeClass,
    RecordSource,
)
from tests._shared import as_uuid

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
_PROVIDER = "example-provider"
_MODEL = "example-capable-001"


def _identity(label: str = "agent-a") -> AgentIdentity:
    return AgentIdentity(
        id=as_uuid(label),
        name="Ada",
        role="Developer",
        department="Engineering",
        hiring_date=date(2026, 1, 15),
        model=ModelConfig(
            provider=NotBlankStr(_PROVIDER),
            model_id=NotBlankStr(_MODEL),
            capability="capable",
        ),
    )


def _record(
    outcome: ProviderOutcomeClass,
    *,
    seconds_ago: float = 10.0,
    latency_ms: float = 120.0,
    source: RecordSource = RecordSource.REAL_CALL,
) -> ProviderHealthRecord:
    succeeded = outcome is ProviderOutcomeClass.SUCCESS
    return ProviderHealthRecord(
        provider_name=NotBlankStr(_PROVIDER),
        model=NotBlankStr(_MODEL),
        timestamp=_NOW - timedelta(seconds=seconds_ago),
        success=succeeded,
        response_time_ms=latency_ms,
        outcome_class=outcome,
        error_message=None if succeeded else f"{outcome.value} from upstream",
        source=source,
    )


class TestJoin:
    def test_identity_is_joined_from_the_live_roster(self) -> None:
        # Never denormalised onto a record: a row that copied a department
        # would change meaning the day the agent moved.
        profile = build_dispatch_profile(_identity(), [_record(_SUCCESS)])

        assert profile.agent_name == "Ada"
        assert profile.role == "Developer"
        assert profile.department == "Engineering"
        assert profile.provider_name == _PROVIDER
        assert profile.model == _MODEL
        assert profile.capability == "capable"


_SUCCESS = ProviderOutcomeClass.SUCCESS
_OVERLOADED = ProviderOutcomeClass.OVERLOADED


class TestCounts:
    def test_outcomes_split_by_class(self) -> None:
        records = [_record(_SUCCESS)] * 3 + [_record(_OVERLOADED)]

        profile = build_dispatch_profile(_identity(), records)

        assert profile.call_count == 4
        assert profile.outcome_counts[_SUCCESS] == 3
        assert profile.outcome_counts[_OVERLOADED] == 1
        assert profile.success_rate_percent == 75.0

    def test_an_absent_class_is_absent_rather_than_zero(self) -> None:
        profile = build_dispatch_profile(_identity(), [_record(_SUCCESS)])

        assert ProviderOutcomeClass.AUTH not in profile.outcome_counts

    def test_probe_traffic_is_excluded(self) -> None:
        # A probe belongs to no agent; letting a healthy probe cadence dilute
        # a failing agent's numbers is the reporting defect this avoids.
        records = [
            _record(_OVERLOADED),
            _record(_SUCCESS, source=RecordSource.PROBE),
            _record(_SUCCESS, source=RecordSource.PROBE),
        ]

        profile = build_dispatch_profile(_identity(), records)

        assert profile.call_count == 1
        assert profile.success_rate_percent == 0.0

    def test_latency_is_a_distribution(self) -> None:
        records = [
            _record(_SUCCESS, latency_ms=100.0),
            _record(_SUCCESS, latency_ms=200.0),
            _record(_SUCCESS, latency_ms=5000.0),
        ]

        profile = build_dispatch_profile(_identity(), records)

        assert profile.latency is not None
        assert profile.latency.p50_ms == 200.0
        assert profile.latency.max_ms == 5000.0

    def test_last_call_is_the_most_recent(self) -> None:
        records = [
            _record(_SUCCESS, seconds_ago=600.0),
            _record(_SUCCESS, seconds_ago=30.0),
        ]

        profile = build_dispatch_profile(_identity(), records)

        assert profile.last_call_at == _NOW - timedelta(seconds=30.0)


class TestSampleSize:
    def test_a_thin_sample_reports_as_insufficient(self) -> None:
        # A rate over four calls is not a measurement, and rendering it beside
        # one over four hundred invites a decision the data cannot support.
        profile = build_dispatch_profile(_identity(), [_record(_SUCCESS)] * 4)

        assert profile.call_count == 4
        assert not profile.has_enough_calls

    def test_the_floor_is_the_operator_s(self) -> None:
        profile = build_dispatch_profile(
            _identity(), [_record(_SUCCESS)] * 4, min_calls=3
        )

        assert profile.has_enough_calls

    def test_the_default_floor_is_the_shipped_one(self) -> None:
        records = [_record(_SUCCESS)] * DEFAULT_MIN_CALLS_FOR_PROFILE

        assert build_dispatch_profile(_identity(), records).has_enough_calls

    def test_an_agent_with_no_calls_is_a_profile_not_an_absence(self) -> None:
        # A new agent has made none, which is a true statement about it.
        profile = build_dispatch_profile(_identity(), [])

        assert profile.call_count == 0
        assert profile.latency is None
        assert profile.last_call_at is None
        assert not profile.has_enough_calls
        assert profile.success_rate_percent == 0.0
