"""The pool the work spine staffs from excludes agents who are out."""

from datetime import date, datetime

import pytest
import structlog.testing

from synthorg.core.agent import AgentIdentity, ModelConfig, SkillSet
from synthorg.core.role import Skill
from synthorg.core.types import NotBlankStr
from synthorg.engine.roster import ServiceabilityFilteredRoster
from synthorg.hr.registry import AgentRegistryService
from synthorg.observability.events.hr import (
    HR_AGENT_AVAILABLE_MODEL_RECOVERED,
    HR_AGENT_UNAVAILABLE_MODEL_UNSERVICEABLE,
)
from synthorg.providers.agent_availability import AgentUnavailability
from synthorg.providers.health import ProviderHealthStatus, ProviderOutcomeClass
from tests._shared import mock_of

pytestmark = pytest.mark.unit

_PROVIDER = "test-provider"
_WORKING = "test-capable-001"
_BROKEN = "test-broken-001"


def _agent(name: str, model_id: str) -> AgentIdentity:
    return AgentIdentity(
        name=name,
        role="Developer",
        department="Engineering",
        model=ModelConfig(provider=_PROVIDER, model_id=model_id),
        hiring_date=date(2026, 1, 1),
        skills=SkillSet(primary=(Skill(id="python", name="python"),)),
    )


def _out(model_id: str, *, needs_operator: bool = False) -> AgentUnavailability:
    return AgentUnavailability(
        provider_name=NotBlankStr(_PROVIDER),
        model=NotBlankStr(model_id),
        verdict=ProviderHealthStatus.DOWN,
        outcome_class=(
            ProviderOutcomeClass.PAYMENT_REQUIRED if needs_operator else None
        ),
        needs_operator=needs_operator,
    )


class _ScriptedAvailability:
    """Reports whichever models the test says are down."""

    def __init__(self, down: set[str]) -> None:
        self.down = down

    async def unavailability_for(
        self,
        model: ModelConfig,
        *,
        now: datetime | None = None,
    ) -> AgentUnavailability | None:
        del now
        if model.model_id in self.down:
            return _out(model.model_id)
        return None


class _FailingAvailability:
    async def unavailability_for(
        self,
        model: ModelConfig,
        *,
        now: datetime | None = None,
    ) -> AgentUnavailability | None:
        del model, now
        msg = "the health surface is unreachable"
        raise RuntimeError(msg)


class TestTheStaffablePool:
    async def test_an_agent_on_a_working_pair_stays_in(self) -> None:
        registry = mock_of[AgentRegistryService]()
        registry.list_active.return_value = (_agent("Ada", _WORKING),)
        roster = ServiceabilityFilteredRoster(
            registry,
            availability=_ScriptedAvailability(set()),
        )

        assert [a.name for a in await roster.list_available()] == ["Ada"]

    async def test_an_agent_on_a_down_pair_is_out(self) -> None:
        registry = mock_of[AgentRegistryService]()
        registry.list_active.return_value = (
            _agent("Ada", _WORKING),
            _agent("Bo", _BROKEN),
        )
        roster = ServiceabilityFilteredRoster(
            registry,
            availability=_ScriptedAvailability({_BROKEN}),
        )

        assert [a.name for a in await roster.list_available()] == ["Ada"]

    async def test_recovery_reverses_itself_with_no_flag_to_unset(self) -> None:
        """Availability is derived, so nothing has to remember to clear it."""
        registry = mock_of[AgentRegistryService]()
        registry.list_active.return_value = (_agent("Bo", _BROKEN),)
        availability = _ScriptedAvailability({_BROKEN})
        roster = ServiceabilityFilteredRoster(registry, availability=availability)
        assert await roster.list_available() == ()

        availability.down.clear()

        assert [a.name for a in await roster.list_available()] == ["Bo"]

    async def test_no_availability_reader_leaves_the_roster_alone(self) -> None:
        """An installation measuring nothing must not stop staffing work."""
        registry = mock_of[AgentRegistryService]()
        registry.list_active.return_value = (_agent("Ada", _WORKING),)
        roster = ServiceabilityFilteredRoster(registry)

        assert [a.name for a in await roster.list_available()] == ["Ada"]

    async def test_a_failed_availability_read_keeps_the_agent_staffable(self) -> None:
        """A health-surface fault must not empty the company."""
        registry = mock_of[AgentRegistryService]()
        registry.list_active.return_value = (_agent("Ada", _WORKING),)
        roster = ServiceabilityFilteredRoster(
            registry,
            availability=_FailingAvailability(),
        )

        assert [a.name for a in await roster.list_available()] == ["Ada"]

    async def test_lookup_by_id_ignores_availability(self) -> None:
        """A project's recorded lead resolves whether or not it is working.

        Filtering here would turn a provider outage into an orphaned
        project, which is a different problem with a different fix.
        """
        lead = _agent("Bo", _BROKEN)
        registry = mock_of[AgentRegistryService]()
        registry.list_active.return_value = (lead,)
        registry.get.return_value = lead
        roster = ServiceabilityFilteredRoster(
            registry,
            availability=_ScriptedAvailability({_BROKEN}),
        )

        found = await roster.get(NotBlankStr(str(lead.id)))

        assert found is lead


class TestTransitionsAreAnnounced:
    """The moment it changed is what an operator needs, not the steady state."""

    async def test_going_out_is_announced_once(self) -> None:
        registry = mock_of[AgentRegistryService]()
        registry.list_active.return_value = (_agent("Bo", _BROKEN),)
        roster = ServiceabilityFilteredRoster(
            registry,
            availability=_ScriptedAvailability({_BROKEN}),
        )

        with structlog.testing.capture_logs() as logs:
            await roster.list_available()
            await roster.list_available()

        going_out = [
            log
            for log in logs
            if log.get("event") == HR_AGENT_UNAVAILABLE_MODEL_UNSERVICEABLE
        ]
        assert len(going_out) == 1
        assert going_out[0]["model"] == _BROKEN

    async def test_coming_back_is_announced(self) -> None:
        registry = mock_of[AgentRegistryService]()
        registry.list_active.return_value = (_agent("Bo", _BROKEN),)
        availability = _ScriptedAvailability({_BROKEN})
        roster = ServiceabilityFilteredRoster(registry, availability=availability)

        with structlog.testing.capture_logs() as logs:
            await roster.list_available()
            availability.down.clear()
            await roster.list_available()

        assert [
            log.get("event")
            for log in logs
            if log.get("event") == HR_AGENT_AVAILABLE_MODEL_RECOVERED
        ] == [HR_AGENT_AVAILABLE_MODEL_RECOVERED]

    async def test_an_offboarded_agent_is_not_announced_as_recovered(self) -> None:
        """Leaving the company is not the same as the model coming back."""
        registry = mock_of[AgentRegistryService]()
        registry.list_active.return_value = (_agent("Bo", _BROKEN),)
        roster = ServiceabilityFilteredRoster(
            registry,
            availability=_ScriptedAvailability({_BROKEN}),
        )
        await roster.list_available()
        registry.list_active.return_value = ()

        with structlog.testing.capture_logs() as logs:
            await roster.list_available()

        assert not [
            log
            for log in logs
            if log.get("event") == HR_AGENT_AVAILABLE_MODEL_RECOVERED
        ]
