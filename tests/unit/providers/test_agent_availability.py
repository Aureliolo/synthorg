"""An agent whose bound pair cannot serve is out, and says why."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from synthorg.config.provider_schema import ProviderConfig, ProviderModelConfig
from synthorg.core.agent import ModelConfig
from synthorg.core.types import NotBlankStr
from synthorg.providers.agent_availability import (
    ServiceabilityAvailabilityReader,
    unavailability_from,
    unserved_binding,
)
from synthorg.providers.enums import AuthType
from synthorg.providers.health import (
    ProviderHealthRecord,
    ProviderHealthStatus,
    ProviderOutcomeClass,
    RecordSource,
)
from synthorg.providers.serviceability import (
    ModelServiceability,
    ServiceabilityThresholds,
    aggregate_serviceability,
)
from synthorg.providers.serviceability_settings import (
    resolve_serviceability_thresholds,
)
from synthorg.settings.resolver import ConfigResolver
from tests._shared import mock_of

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
    """Answers with one view, recording what the caller asked with.

    ``thresholds_seen`` is the interesting half: the boundaries decide the
    verdict, so a caller that omits them is asking a different question.
    """

    def __init__(self, view: ModelServiceability) -> None:
        self.view = view
        self.asked: list[tuple[str, str | None]] = []
        self.thresholds_seen: list[ServiceabilityThresholds | None] = []

    async def get_serviceability(
        self,
        provider_name: str,
        model: str | None,
        *,
        now: datetime | None = None,
        thresholds: ServiceabilityThresholds | None = None,
    ) -> ModelServiceability:
        del now
        self.asked.append((provider_name, model))
        self.thresholds_seen.append(thresholds)
        return self.view

    async def get_all_serviceability(
        self,
        *,
        now: datetime | None = None,
        thresholds: ServiceabilityThresholds | None = None,
    ) -> Mapping[tuple[str, str | None], ModelServiceability]:
        del now
        self.asked.append((self.view.provider_name, self.view.model))
        self.thresholds_seen.append(thresholds)
        return {(self.view.provider_name, self.view.model): self.view}


class _UnreadableTracker:
    """A tracker whose window read fails, leaving the catalogue the only ground."""

    async def get_serviceability(
        self,
        provider_name: str,
        model: str | None,
        *,
        now: datetime | None = None,
        thresholds: ServiceabilityThresholds | None = None,
    ) -> ModelServiceability:
        del provider_name, model, now, thresholds
        msg = "the serviceability store is unreachable"
        raise RuntimeError(msg)

    async def get_all_serviceability(
        self,
        *,
        now: datetime | None = None,
        thresholds: ServiceabilityThresholds | None = None,
    ) -> Mapping[tuple[str, str | None], ModelServiceability]:
        del now, thresholds
        msg = "the serviceability store is unreachable"
        raise RuntimeError(msg)


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

    async def test_the_fleet_read_asks_once_for_every_pair(self) -> None:
        """A roster sweep pays one read, not one per agent sharing a pair."""
        tracker = _StubTracker(
            _view(
                _record(ProviderOutcomeClass.INTERNAL, seconds_ago=10),
                _record(ProviderOutcomeClass.INTERNAL, seconds_ago=20),
                _record(ProviderOutcomeClass.INTERNAL, seconds_ago=30),
            ),
        )
        reader = ServiceabilityAvailabilityReader(tracker)

        out = await reader.unavailability_by_pair([(_PROVIDER, _MODEL)], now=_NOW)

        assert len(tracker.asked) == 1
        assert (_PROVIDER, _MODEL) in out

    async def test_the_fleet_read_omits_a_serving_pair(self) -> None:
        tracker = _StubTracker(
            _view(_record(ProviderOutcomeClass.SUCCESS, seconds_ago=1))
        )
        reader = ServiceabilityAvailabilityReader(tracker)

        out = await reader.unavailability_by_pair([(_PROVIDER, _MODEL)], now=_NOW)

        assert out == {}

    async def test_the_fleet_read_carries_the_operators_boundaries(self) -> None:
        """The boundaries decide the verdict, so the read has to carry them.

        A caller snapshotting the tracker directly gets whatever the tracker
        falls back to, and the roster then disagrees with the per-agent read
        about the same pair, using boundaries nobody set against boundaries
        somebody did.
        """
        tracker = _StubTracker(
            _view(_record(ProviderOutcomeClass.SUCCESS, seconds_ago=1))
        )
        reader = ServiceabilityAvailabilityReader(tracker)

        await reader.unavailability_by_pair([(_PROVIDER, _MODEL)], now=_NOW)

        assert tracker.thresholds_seen == [
            await resolve_serviceability_thresholds(None)
        ]


def _catalogue(*model_ids: str) -> dict[str, ProviderConfig]:
    """A one-provider catalogue serving *model_ids*.

    Returns:
        Provider mapping keyed by the test provider name.
    """
    return {
        _PROVIDER: ProviderConfig(
            auth_type=AuthType.NONE,
            models=tuple(ProviderModelConfig(id=model_id) for model_id in model_ids),
        )
    }


def _resolver_serving(catalogue: Mapping[str, ProviderConfig]) -> ConfigResolver:
    """A settings resolver whose live catalogue is *catalogue*.

    Returns:
        The resolver.
    """
    resolver: ConfigResolver = mock_of[ConfigResolver](
        get_provider_configs=AsyncMock(
            spec=ConfigResolver.get_provider_configs, return_value=catalogue
        )
    )
    return resolver


class TestABindingTheCatalogueDoesNotServe:
    """The window cannot report a pair nobody can call.

    A provider retiring a model leaves the roster holding a binding that was
    valid when it was written. Nothing calls it successfully, so it makes no
    failing calls either, so no rate and no latch ever forms: it survives
    selection, capability judging, plan review and dispatch, and fails at
    turn 1 of paid work.
    """

    def test_a_retired_model_takes_its_agent_out(self) -> None:
        out = unserved_binding(_PROVIDER, _MODEL, _catalogue("test-capable-001:0731"))

        assert out is not None
        assert out.verdict is ProviderHealthStatus.DOWN
        assert out.outcome_class is ProviderOutcomeClass.NOT_FOUND

    def test_it_says_an_operator_has_to_act(self) -> None:
        # Nothing about an absent catalogue entry recovers on its own, so
        # reporting it as self-clearing would leave an operator waiting.
        out = unserved_binding(_PROVIDER, _MODEL, _catalogue("other"))

        assert out is not None
        assert out.needs_operator is True
        assert "does not clear without an operator" in out.reason

    def test_a_deleted_connection_takes_its_agents_out_too(self) -> None:
        out = unserved_binding("gone-provider", _MODEL, _catalogue(_MODEL))

        assert out is not None

    def test_a_served_pair_is_not_an_answer(self) -> None:
        assert unserved_binding(_PROVIDER, _MODEL, _catalogue(_MODEL)) is None

    def test_an_empty_catalogue_is_not_an_answer(self) -> None:
        # It reads the same whether nothing is configured or a resolver
        # handed back a partial view mid-boot, and the second would take
        # every agent in the company out on one bad read.
        assert unserved_binding(_PROVIDER, _MODEL, {}) is None

    def test_a_provider_serving_no_listed_models_is_not_an_answer(self) -> None:
        # The same ambiguity one level down: a connection whose models
        # nobody has enumerated reads identically to one that serves none,
        # and every agent on it would go out at once.
        assert unserved_binding(_PROVIDER, _MODEL, _catalogue()) is None


class TestTheReaderConsultsTheCatalogue:
    async def test_the_per_agent_read_reports_a_retired_model(self) -> None:
        healthy = _view(_record(ProviderOutcomeClass.SUCCESS, seconds_ago=1))
        reader = ServiceabilityAvailabilityReader(
            _StubTracker(healthy),
            config_resolver=_resolver_serving(_catalogue("something-else")),
        )

        out = await reader.unavailability_for(_model(), now=_NOW)

        assert out is not None
        assert out.outcome_class is ProviderOutcomeClass.NOT_FOUND

    async def test_the_fleet_read_reports_a_pair_the_window_never_saw(self) -> None:
        # The stub's window holds one healthy pair and knows nothing of the
        # asked-about one, which is exactly the live shape: the binding has
        # never produced a record because it has never been callable.
        reader = ServiceabilityAvailabilityReader(
            _StubTracker(_view(_record(ProviderOutcomeClass.SUCCESS, seconds_ago=1))),
            config_resolver=_resolver_serving(_catalogue(_MODEL)),
        )

        out = await reader.unavailability_by_pair(
            [(_PROVIDER, "retired-model")], now=_NOW
        )

        assert out[_PROVIDER, "retired-model"].outcome_class is (
            ProviderOutcomeClass.NOT_FOUND
        )

    async def test_the_catalogue_verdict_wins_over_the_window(self) -> None:
        # An operator told "failing most recent calls" goes looking at the
        # provider's status page; the remedy here is to repoint the agent.
        reader = ServiceabilityAvailabilityReader(
            _StubTracker(
                _view(
                    _record(ProviderOutcomeClass.INTERNAL, seconds_ago=10),
                    _record(ProviderOutcomeClass.INTERNAL, seconds_ago=20),
                    _record(ProviderOutcomeClass.INTERNAL, seconds_ago=30),
                )
            ),
            config_resolver=_resolver_serving(_catalogue("something-else")),
        )

        out = await reader.unavailability_by_pair([(_PROVIDER, _MODEL)], now=_NOW)

        assert out[_PROVIDER, _MODEL].outcome_class is ProviderOutcomeClass.NOT_FOUND

    async def test_a_served_pair_still_reads_from_the_window(self) -> None:
        reader = ServiceabilityAvailabilityReader(
            _StubTracker(_view(_record(ProviderOutcomeClass.SUCCESS, seconds_ago=1))),
            config_resolver=_resolver_serving(_catalogue(_MODEL)),
        )

        out = await reader.unavailability_by_pair([(_PROVIDER, _MODEL)], now=_NOW)

        assert out == {}

    async def test_an_unreadable_window_still_reports_the_catalogue_absence(
        self,
    ) -> None:
        """The two grounds are independent, so one failing must not hide the other.

        The caller treats a raised read as "nobody is out", so letting a
        tracker fault take the catalogue answer with it puts a pair the
        provider does not serve back in front of paid work: the one ground
        that no window can ever recover on its own.
        """
        reader = ServiceabilityAvailabilityReader(
            _UnreadableTracker(),
            config_resolver=_resolver_serving(_catalogue(_MODEL)),
        )

        out = await reader.unavailability_by_pair(
            [(_PROVIDER, "retired-model")], now=_NOW
        )

        assert out[_PROVIDER, "retired-model"].outcome_class is (
            ProviderOutcomeClass.NOT_FOUND
        )

    async def test_an_unreadable_window_with_nothing_absent_still_raises(self) -> None:
        """No catalogue verdict to report means the caller must see the fault.

        Swallowing it here would report an empty mapping, which reads as a
        real answer meaning nobody is out.
        """
        reader = ServiceabilityAvailabilityReader(
            _UnreadableTracker(),
            config_resolver=_resolver_serving(_catalogue(_MODEL)),
        )

        with pytest.raises(RuntimeError, match="serviceability store is unreachable"):
            await reader.unavailability_by_pair([(_PROVIDER, _MODEL)], now=_NOW)
