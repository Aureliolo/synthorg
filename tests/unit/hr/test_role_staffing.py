"""Tests for the role-staffing selection ladder.

The gates ask HR "who holds this role and which of them fits this work".
The caller passes what the WORK is (its stakes and complexity) rather than a
rung, so it cannot answer "what does judging this demand" differently from
the one capability policy every other selection reads. The answer decides
WHO reviews; it never rewrites what anybody runs.
"""

import pytest

from synthorg.core.agent import AgentIdentity
from synthorg.core.role_catalog import COMPLETION_REVIEWER_ROLE_NAME
from synthorg.core.task_enums import Complexity, Stakes
from synthorg.core.types import CapabilityLevel, NotBlankStr
from synthorg.hr.role_staffing import RoleStaffingSelection, RoleStaffingService
from tests._shared.staffing import role_holder, staffing_with

pytestmark = pytest.mark.unit

_ROLE = NotBlankStr(COMPLETION_REVIEWER_ROLE_NAME)

#: Work whose shipped stakes floor is each rung, so a test can say what the
#: work DEMANDS without restating the ladder the policy owns.
_WORK_DEMANDING: dict[CapabilityLevel, Stakes] = {
    "basic": Stakes.LOW,
    "capable": Stakes.NORMAL,
    "expert": Stakes.HIGH,
}


def _holder(
    label: str,
    *,
    capability: CapabilityLevel | None = "capable",
    role: str = COMPLETION_REVIEWER_ROLE_NAME,
) -> AgentIdentity:
    """Build a role holder whose bound model carries *capability*.

    A thin default-binder over the shared builder, so this module and every
    other gate test agree on what a role holder is.
    """
    return role_holder(label, role=role, capability=capability, per_label_model=True)


def _service(
    *holders: AgentIdentity, executor: AgentIdentity | None = None
) -> RoleStaffingService:
    return staffing_with(*holders, executor=executor)


async def _select(
    service: RoleStaffingService,
    *,
    demanding: CapabilityLevel,
    exclude: str = "executor",
    contributors: tuple[NotBlankStr, ...] = (),
    project_id: str | None = None,
) -> RoleStaffingSelection | None:
    """Select a holder for work whose floor is *demanding*.

    Returns:
        The selection, or ``None`` when nobody eligible holds the role.
    """
    return await service.select_holder(
        role=_ROLE,
        stakes=_WORK_DEMANDING[demanding],
        complexity=Complexity.MEDIUM,
        exclude_agent_id=NotBlankStr(exclude),
        contributors=contributors,
        project_id=NotBlankStr(project_id) if project_id is not None else None,
    )


class TestExecutorCapabilityFloor:
    """A judge is never weaker than the agent whose work it judges.

    The work's stakes and complexity set the requirement, and for a subtask
    both are proposed by the agent that decomposed it. Without a floor that
    lets the thing under review bid its own judge down, because the fit
    ladder PREFERS an exact match: a lower bar does not merely permit a
    weaker reviewer, it selects one.
    """

    async def test_a_stronger_executor_raises_the_requirement(self) -> None:
        executor = _holder("author", capability="expert", role="Developer")
        weak = _holder("weak", capability="basic")
        strong = _holder("strong", capability="expert")

        result = await _select(
            _service(weak, strong, executor=executor),
            demanding="basic",
            exclude=str(executor.id),
        )

        assert result is not None
        assert result.agent.id == strong.id
        assert result.required_capability == "expert"

    async def test_a_weaker_executor_leaves_the_requirement_alone(self) -> None:
        executor = _holder("author", capability="basic", role="Developer")
        exact = _holder("exact", capability="capable")

        result = await _select(
            _service(exact, executor=executor),
            demanding="capable",
            exclude=str(executor.id),
        )

        assert result is not None
        assert result.required_capability == "capable"
        assert result.capability_fit == "match"

    async def test_an_unclassified_executor_leaves_the_requirement_alone(self) -> None:
        executor = _holder("author", capability=None, role="Developer")
        exact = _holder("exact", capability="capable")

        result = await _select(
            _service(exact, executor=executor),
            demanding="capable",
            exclude=str(executor.id),
        )

        assert result is not None
        assert result.required_capability == "capable"

    async def test_the_floor_never_invents_a_holder(self) -> None:
        """Raising the bar changes who is preferred, never whether one exists.

        The ladder still falls to a weaker holder when nothing better is on
        the roster: a weaker reviewer is a real independent reviewer, and
        refusing one would trade a real review for none at all.
        """
        executor = _holder("author", capability="expert", role="Developer")
        weak = _holder("weak", capability="basic")

        result = await _select(
            _service(weak, executor=executor),
            demanding="basic",
            exclude=str(executor.id),
        )

        assert result is not None
        assert result.agent.id == weak.id
        assert result.capability_fit == "lower"


class TestNoCandidates:
    """No holder at all is the unstaffed case the gates fail closed on."""

    async def test_no_holders_yields_none(self) -> None:
        assert await _select(_service(), demanding="capable") is None

    async def test_the_only_holder_being_the_executor_yields_none(self) -> None:
        # The executor may hold the reviewer role; it still may not review its
        # own work, and no independent reviewer remains.
        executor = _holder("solo")

        result = await _select(
            _service(executor), demanding="capable", exclude=str(executor.id)
        )

        assert result is None


class TestContributorPreference:
    """A holder who already worked the initiative is preferred."""

    async def test_prefers_a_holder_who_worked_the_initiative(self) -> None:
        on_team = _holder("on-team")
        elsewhere = _holder("elsewhere")

        result = await _select(
            _service(elsewhere, on_team),
            demanding="capable",
            contributors=(NotBlankStr(str(on_team.id)), NotBlankStr("someone-else")),
            project_id="proj-apollo",
        )

        assert result is not None
        assert result.agent.id == on_team.id
        assert result.source == "project_team"

    async def test_widens_org_wide_when_no_contributor_is_eligible(self) -> None:
        elsewhere = _holder("elsewhere")

        result = await _select(
            _service(elsewhere),
            demanding="capable",
            contributors=(NotBlankStr("someone-else"),),
            project_id="proj-apollo",
        )

        assert result is not None
        assert result.agent.id == elsewhere.id
        assert result.source == "org_wide"

    async def test_no_contributors_reads_org_wide(self) -> None:
        anyone = _holder("anyone")

        result = await _select(_service(anyone), demanding="capable")

        assert result is not None
        assert result.source == "org_wide"


class TestCapabilityFit:
    """Match first, then higher, then lower; never a model swap."""

    async def test_prefers_an_exact_capability_match(self) -> None:
        weak = _holder("weak", capability="basic")
        exact = _holder("exact", capability="capable")
        strong = _holder("strong", capability="expert")

        result = await _select(_service(weak, strong, exact), demanding="capable")

        assert result is not None
        assert result.agent.id == exact.id
        assert result.capability_fit == "match"

    async def test_goes_higher_when_no_exact_match_exists(self) -> None:
        weak = _holder("weak", capability="basic")
        strong = _holder("strong", capability="expert")

        result = await _select(_service(weak, strong), demanding="capable")

        assert result is not None
        assert result.agent.id == strong.id
        assert result.capability_fit == "higher"

    async def test_takes_the_nearest_higher_rung(self) -> None:
        nearer = _holder("nearer", capability="capable")
        further = _holder("further", capability="expert")

        result = await _select(_service(further, nearer), demanding="basic")

        assert result is not None
        assert result.agent.id == nearer.id

    async def test_falls_back_to_lower_only_when_nothing_higher_exists(self) -> None:
        # A weaker reviewer is still a real independent reviewer; refusing one
        # would trade a real review for no review at all.
        weak = _holder("weak", capability="basic")

        result = await _select(_service(weak), demanding="expert")

        assert result is not None
        assert result.agent.id == weak.id
        assert result.capability_fit == "lower"

    async def test_takes_the_nearest_lower_rung(self) -> None:
        nearer = _holder("nearer", capability="capable")
        further = _holder("further", capability="basic")

        result = await _select(_service(further, nearer), demanding="expert")

        assert result is not None
        assert result.agent.id == nearer.id

    async def test_an_unclassified_model_sorts_below_every_rung(self) -> None:
        # An unclassified pair must never outrank a classified one by default;
        # otherwise a model nobody graded would win every selection.
        unclassified = _holder("unclassified", capability=None)
        classified = _holder("classified", capability="capable")

        result = await _select(_service(unclassified, classified), demanding="capable")

        assert result is not None
        assert result.agent.id == classified.id


class TestSubstantialComplexityRaisesTheBar:
    async def test_a_complex_brief_demands_one_rung_more(self) -> None:
        """The requirement follows the work, and complexity is part of it."""
        exact = _holder("exact", capability="capable")
        stronger = _holder("stronger", capability="expert")

        result = await staffing_with(exact, stronger).select_holder(
            role=_ROLE,
            stakes=Stakes.NORMAL,
            complexity=Complexity.COMPLEX,
            exclude_agent_id=NotBlankStr("executor"),
        )

        assert result is not None
        assert result.required_capability == "expert"
        assert result.agent.id == stronger.id


class TestDeterminism:
    """Two equally-fit holders resolve the same way every time."""

    async def test_ties_break_on_the_lowest_agent_id(self) -> None:
        """The tie-break is a specific holder, not merely a stable one.

        Asserting only that two orderings agree passes under any symmetric
        rule, including one that picks the last candidate seen; the rule is
        that ties sort on ``str(agent.id)``, so the test names the winner.
        """
        first = _holder("aaa", capability="capable")
        second = _holder("bbb", capability="capable")
        expected = min(first, second, key=lambda agent: str(agent.id))

        forward = await _select(_service(first, second), demanding="capable")
        reversed_order = await _select(_service(second, first), demanding="capable")

        assert forward is not None
        assert reversed_order is not None
        assert forward.agent.id == expected.id
        assert reversed_order.agent.id == expected.id

    async def test_the_selection_reports_what_it_was_asked_for(self) -> None:
        holder = _holder("holder", capability="expert")

        result = await _select(_service(holder), demanding="capable")

        assert result is not None
        assert result.required_capability == "capable"
        assert result.role == COMPLETION_REVIEWER_ROLE_NAME
        assert result.reason
