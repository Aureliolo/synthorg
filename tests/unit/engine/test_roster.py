"""The pool the work spine staffs from excludes agents who are out."""

import asyncio
from collections.abc import Mapping
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
    """Reports whichever models the test says are down.

    ``reads`` counts fleet-wide calls, so a sweep that regressed to one read
    per agent would fail rather than merely be slower.
    ``peak_in_flight`` records how many reads were ever open at once, which
    is how the sweep's serialisation is observable from outside it.
    """

    def __init__(self, down: set[str]) -> None:
        self.down = down
        self.reads = 0
        self.in_flight = 0
        self.peak_in_flight = 0
        self.fail = False

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

    async def unavailability_by_pair(
        self,
        *,
        now: datetime | None = None,
    ) -> Mapping[tuple[str, str], AgentUnavailability]:
        del now
        self.reads += 1
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        # A real read awaits I/O. Yielding here is what lets a second
        # sweep in, if the roster ever lets one in.
        await asyncio.sleep(0)
        self.in_flight -= 1
        if self.fail:
            msg = "the health surface is unreachable"
            raise RuntimeError(msg)
        return {(_PROVIDER, model_id): _out(model_id) for model_id in self.down}


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

    async def unavailability_by_pair(
        self,
        *,
        now: datetime | None = None,
    ) -> Mapping[tuple[str, str], AgentUnavailability]:
        del now
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

    async def test_the_sweep_reads_availability_once_for_the_whole_roster(
        self,
    ) -> None:
        """Agents share pairs, so the question is asked once, not per agent."""
        registry = mock_of[AgentRegistryService]()
        registry.list_active.return_value = (
            _agent("Ada", _WORKING),
            _agent("Bo", _BROKEN),
            _agent("Cy", _BROKEN),
        )
        availability = _ScriptedAvailability({_BROKEN})
        roster = ServiceabilityFilteredRoster(registry, availability=availability)

        assert [a.name for a in await roster.list_available()] == ["Ada"]
        assert availability.reads == 1

    async def test_two_sweeps_never_read_availability_at_the_same_time(self) -> None:
        """The read is part of the transition, so it is inside the lock.

        Hoisting it out shortens the hold and lets two sweeps snapshot in
        one order and write back in the other, so the older answer lands
        last and the transition log announces a change that did not happen.
        """
        registry = mock_of[AgentRegistryService]()
        registry.list_active.return_value = (
            _agent("Ada", _WORKING),
            _agent("Bo", _BROKEN),
        )
        availability = _ScriptedAvailability({_BROKEN})
        roster = ServiceabilityFilteredRoster(registry, availability=availability)

        await asyncio.gather(roster.list_available(), roster.list_available())

        assert availability.reads == 2
        assert availability.peak_in_flight == 1

    async def test_two_sweeps_never_read_the_active_roster_at_the_same_time(
        self,
    ) -> None:
        """The roster read feeds the same write, so it is inside the lock too.

        `_report_transitions` decides recovery by membership of the tuple
        this read returns, so a stale one announces an agent offboarded in
        between as back at work.
        """
        seen = {"in_flight": 0, "peak": 0}

        async def counting_list_active() -> tuple[AgentIdentity, ...]:
            seen["in_flight"] += 1
            seen["peak"] = max(seen["peak"], seen["in_flight"])
            await asyncio.sleep(0)
            seen["in_flight"] -= 1
            return (_agent("Ada", _WORKING),)

        registry = mock_of[AgentRegistryService]()
        registry.list_active.side_effect = counting_list_active
        roster = ServiceabilityFilteredRoster(
            registry,
            availability=_ScriptedAvailability(set()),
        )

        await asyncio.gather(roster.list_available(), roster.list_available())

        assert seen["peak"] == 1

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

    async def test_a_failed_read_announces_nothing_and_forgets_nothing(
        self,
    ) -> None:
        """A read that failed is not a measurement saying everyone is back.

        Reading a failure as "nobody is out" would announce recovery for
        every agent currently out AND clear the map that remembers them, so
        a health-surface blip would report the whole roster returning to
        work and then have no record to correct itself against.
        """
        registry = mock_of[AgentRegistryService]()
        registry.list_active.return_value = (_agent("Bo", _BROKEN),)
        availability = _ScriptedAvailability({_BROKEN})
        roster = ServiceabilityFilteredRoster(registry, availability=availability)
        await roster.list_available()

        availability.fail = True
        with structlog.testing.capture_logs() as during_failure:
            staffable = await roster.list_available()

        # Staffing still fails open: only the verdict is withheld.
        assert [a.name for a in staffable] == ["Bo"]
        assert not [
            log
            for log in during_failure
            if log.get("event") == HR_AGENT_AVAILABLE_MODEL_RECOVERED
        ]

        # The memory survived, so the pair still being down is not news.
        availability.fail = False
        with structlog.testing.capture_logs() as after_recovery:
            await roster.list_available()

        assert not [
            log
            for log in after_recovery
            if log.get("event") == HR_AGENT_UNAVAILABLE_MODEL_UNSERVICEABLE
        ]

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
