"""An agent whose bound pair cannot serve is out, and says why."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.agent import ModelConfig
from synthorg.core.types import NotBlankStr
from synthorg.providers.agent_availability import (
    ServiceabilityAvailabilityReader,
    unavailability_from,
)
from synthorg.providers.health import (
    ProviderHealthRecord,
    ProviderOutcomeClass,
    RecordSource,
)
from synthorg.providers.serviceability import (
    ModelServiceability,
    ServiceabilityThresholds,
    aggregate_serviceability,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
_PROVIDER = "test-provider"
_MODEL = "test-capable-001"


def _record(
    outcome: ProviderOutcomeClass,
    *,
    seconds_ago: float,
    latency_ms: float = 120.0,
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
        source=RecordSource.REAL_CALL,
    )


def _view(*records: ProviderHealthRecord) -> ModelServiceability:
    return aggregate_serviceability(
        list(records),
        now=_NOW,
        thresholds=ServiceabilityThresholds(),
        provider_name=_PROVIDER,
        model=_MODEL,
    )


class _StubTracker:
    def __init__(self, view: ModelServiceability) -> None:
        self.view = view
        self.asked: list[tuple[str, str | None]] = []

    async def get_serviceability(
        self,
        provider_name: str,
        model: str | None,
        *,
        now: datetime | None = None,
        thresholds: ServiceabilityThresholds | None = None,
    ) -> ModelServiceability:
        del now, thresholds
        self.asked.append((provider_name, model))
        return self.view


def _model() -> ModelConfig:
    return ModelConfig(provider=_PROVIDER, model_id=_MODEL)


class TestDerivingUnavailability:
    def test_a_serving_pair_leaves_its_agents_available(self) -> None:
        view = _view(
            _record(ProviderOutcomeClass.SUCCESS, seconds_ago=10),
            _record(ProviderOutcomeClass.SUCCESS, seconds_ago=20),
            _record(ProviderOutcomeClass.SUCCESS, seconds_ago=30),
        )

        assert unavailability_from(view) is None

    def test_a_down_pair_takes_its_agents_out(self) -> None:
        view = _view(
            _record(ProviderOutcomeClass.OVERLOADED, seconds_ago=10),
            _record(ProviderOutcomeClass.OVERLOADED, seconds_ago=20),
            _record(ProviderOutcomeClass.SUCCESS, seconds_ago=30),
        )

        found = unavailability_from(view)

        assert found is not None
        assert found.model == _MODEL
        assert found.needs_operator is False
        assert "recovers" in found.reason

    def test_an_empty_balance_stays_out_until_somebody_acts(self) -> None:
        """A 402 is a refusal, not a rate, so it must not decay away."""
        view = _view(
            _record(ProviderOutcomeClass.PAYMENT_REQUIRED, seconds_ago=10),
            _record(ProviderOutcomeClass.SUCCESS, seconds_ago=20),
            _record(ProviderOutcomeClass.SUCCESS, seconds_ago=30),
        )

        found = unavailability_from(view)

        assert found is not None
        assert found.outcome_class is ProviderOutcomeClass.PAYMENT_REQUIRED
        assert found.needs_operator is True
        assert "does not clear without an operator" in found.reason

    def test_a_degraded_pair_keeps_its_agents_working(self) -> None:
        """A slowdown is not an outage, and removing the roster would be."""
        view = _view(
            _record(ProviderOutcomeClass.OVERLOADED, seconds_ago=10),
            *(
                _record(ProviderOutcomeClass.SUCCESS, seconds_ago=n)
                for n in range(20, 40)
            ),
        )

        assert unavailability_from(view) is None

    def test_silence_is_not_a_reason_to_take_an_agent_out(self) -> None:
        """A pair nobody has called has said nothing about itself.

        An idle roster would otherwise empty itself: no calls, no verdict,
        every agent out, and no way back in because being out is what stops
        the calls.
        """
        view = _view(_record(ProviderOutcomeClass.INTERNAL, seconds_ago=10))

        assert unavailability_from(view) is None

    def test_the_reason_carries_how_long_it_has_been_running(self) -> None:
        view = _view(
            _record(ProviderOutcomeClass.OVERLOADED, seconds_ago=600),
            _record(ProviderOutcomeClass.OVERLOADED, seconds_ago=300),
            _record(ProviderOutcomeClass.OVERLOADED, seconds_ago=10),
        )

        found = unavailability_from(view)

        assert found is not None
        assert found.since == _NOW - timedelta(seconds=600)

    def test_a_provider_wide_view_names_no_agent(self) -> None:
        """Availability is per pair; a connection-wide roll-up is not one."""
        wide = aggregate_serviceability(
            [
                _record(ProviderOutcomeClass.OVERLOADED, seconds_ago=10),
                _record(ProviderOutcomeClass.OVERLOADED, seconds_ago=20),
                _record(ProviderOutcomeClass.OVERLOADED, seconds_ago=30),
            ],
            now=_NOW,
            thresholds=ServiceabilityThresholds(),
            provider_name=_PROVIDER,
            model=None,
        )
        assert unavailability_from(wide.model_copy(update={"model": None})) is None


class TestServiceabilityAvailabilityReader:
    async def test_it_asks_about_the_agents_own_pair(self) -> None:
        healthy = _view(_record(ProviderOutcomeClass.SUCCESS, seconds_ago=1))
        tracker = _StubTracker(healthy)
        reader = ServiceabilityAvailabilityReader(tracker)

        await reader.unavailability_for(_model(), now=_NOW)

        assert tracker.asked == [(_PROVIDER, _MODEL)]

    async def test_it_reports_a_down_pair(self) -> None:
        tracker = _StubTracker(
            _view(
                _record(ProviderOutcomeClass.INTERNAL, seconds_ago=10),
                _record(ProviderOutcomeClass.INTERNAL, seconds_ago=20),
                _record(ProviderOutcomeClass.INTERNAL, seconds_ago=30),
            ),
        )
        reader = ServiceabilityAvailabilityReader(tracker)

        assert await reader.unavailability_for(_model(), now=_NOW) is not None
