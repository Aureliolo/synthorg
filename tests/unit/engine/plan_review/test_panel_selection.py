"""Unit tests for stakeholder review-panel selection."""

import pytest

from synthorg.core.agent import AgentIdentity
from synthorg.core.task import Task
from synthorg.core.task_enums import (
    Complexity,
    CoordinationTopology,
    Priority,
    Stakes,
    TaskStructure,
    TaskType,
)
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.models import (
    DecompositionPlan,
    DecompositionResult,
    SubtaskDefinition,
)
from synthorg.engine.plan_review._panel_selection import select_review_panel
from synthorg.hr.seniority import SeniorityLevel
from tests._shared import as_uuid, sid
from tests._shared.scripted_provider import make_e2e_identity

pytestmark = pytest.mark.unit


def _agent(
    label: str,
    *,
    role: str = "Backend Developer",
    department: str = "Engineering",
    level: SeniorityLevel = SeniorityLevel.MID,
) -> AgentIdentity:
    return make_e2e_identity(label=label).model_copy(
        update={"role": role, "department": department, "level": level}
    )


def _plan(*owner_roles: str) -> DecompositionResult:
    subtasks = tuple(
        SubtaskDefinition(
            id=sid(f"item-{index}"),
            title=f"Item {index}",
            description="Do the work",
            estimated_complexity=Complexity.MEDIUM,
            stakes=Stakes.NORMAL,
            required_role=role,
            acceptance_criteria=(NotBlankStr("done"),),
        )
        for index, role in enumerate(owner_roles, start=1)
    )
    plan = DecompositionPlan(
        parent_task_id=sid("root"),
        subtasks=subtasks,
        task_structure=TaskStructure.PARALLEL,
        coordination_topology=CoordinationTopology.AUTO,
    )
    created = tuple(
        Task(
            id=as_uuid(f"item-{index}"),
            title=f"Item {index}",
            description="Do the work",
            type=TaskType.DEVELOPMENT,
            priority=Priority.MEDIUM,
            project="beachhead",
            created_by="ceo",
        )
        for index in range(1, len(owner_roles) + 1)
    )
    return DecompositionResult(plan=plan, created_tasks=created)


class TestSelectReviewPanel:
    def test_excludes_the_owner(self) -> None:
        owner = _agent("owner")
        others = (_agent("a"), _agent("b"))
        panel = select_review_panel(
            _plan("Backend Developer"), (owner, *others), owner=owner, limit=4
        )
        assert owner.id not in {a.id for a in panel}

    def test_seats_cto_and_cfo_when_present(self) -> None:
        cto = _agent("cto", role="CTO", department="Executive")
        cfo = _agent("cfo", role="CFO", department="Executive")
        dev = _agent("dev")
        panel = select_review_panel(
            _plan("Backend Developer"),
            (cto, cfo, dev),
            owner=None,
            limit=4,
        )
        roles = {a.role for a in panel}
        assert "CTO" in roles
        assert "CFO" in roles

    def test_seats_a_domain_lead_for_a_touched_department(self) -> None:
        # A plan owned by an engineering role should seat the most senior
        # engineering agent (a domain lead) even without a CTO/CFO present.
        junior = _agent("eng-junior", level=SeniorityLevel.JUNIOR)
        lead = _agent("eng-lead", role="QA Lead", level=SeniorityLevel.LEAD)
        panel = select_review_panel(
            _plan("Backend Developer"), (junior, lead), owner=None, limit=1
        )
        assert len(panel) == 1
        assert panel[0].id == lead.id

    def test_respects_the_panel_size_limit(self) -> None:
        roster = tuple(_agent(f"a{i}") for i in range(6))
        panel = select_review_panel(
            _plan("Backend Developer"), roster, owner=None, limit=3
        )
        assert len(panel) == 3

    def test_empty_when_only_the_owner_is_available(self) -> None:
        owner = _agent("owner")
        panel = select_review_panel(
            _plan("Backend Developer"), (owner,), owner=owner, limit=4
        )
        assert panel == ()

    def test_empty_when_limit_is_zero(self) -> None:
        panel = select_review_panel(
            _plan("Backend Developer"), (_agent("a"),), owner=None, limit=0
        )
        assert panel == ()

    def test_no_duplicate_reviewers(self) -> None:
        cto = _agent("cto", role="CTO", department="Executive")
        roster = (cto, _agent("dev"), _agent("dev2"))
        panel = select_review_panel(
            _plan("Backend Developer", "CTO"), roster, owner=None, limit=3
        )
        ids = [a.id for a in panel]
        assert len(ids) == len(set(ids))
