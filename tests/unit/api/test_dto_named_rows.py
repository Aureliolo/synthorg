"""Read-boundary rows: names travel beside ids, and derived fields survive."""

from datetime import UTC, datetime

import pytest

from synthorg.api.dto_named_rows import (
    CoordinationMetricsRow,
    LifecycleTransitionRow,
    PlanItemRow,
    PlanRow,
    ProjectRow,
    TaskRow,
)
from synthorg.budget.coordination_metric_models import (
    CoordinationMetrics,
    MessageOverhead,
)
from synthorg.budget.coordination_store import CoordinationMetricsRecord
from synthorg.core.lifecycle_transition import (
    LifecycleEntityKind,
    LifecycleTransition,
)
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.project import Project
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from tests._shared import as_uuid, sid

_LEAD = sid("agent-lead")
_NAMES = {_LEAD: "Ada Chen"}


def _task() -> Task:
    return Task(
        title=NotBlankStr("Ship the leaderboard"),
        description=NotBlankStr("Score table across mates"),
        type=TaskType.DEVELOPMENT,
        status=TaskStatus.ASSIGNED,
        project=sid("proj-tetris"),
        created_by=sid("agent-cto"),
        assigned_to=NotBlankStr(_LEAD),
    )


def _plan_item() -> PlanItem:
    return PlanItem(
        id=sid("item-a"),
        title=NotBlankStr("Scaffold the board"),
        description=NotBlankStr("Grid plus falling piece"),
        owner=NotBlankStr(_LEAD),
        acceptance_criteria=(NotBlankStr("the grid renders"),),
        expected_artifacts=(NotBlankStr("src/board.ts"),),
    )


def _transition(requested_by: str | None) -> LifecycleTransition:
    return LifecycleTransition(
        entity_kind=LifecycleEntityKind.PLAN,
        entity_id=sid("plan-a"),
        to_status=NotBlankStr("executing"),
        requested_by=None if requested_by is None else NotBlankStr(requested_by),
        entity_version=2,
        occurred_at=datetime(2026, 8, 17, 9, 0, tzinfo=UTC),
    )


@pytest.mark.unit
class TestNamedRows:
    def test_task_row_names_the_assignee(self) -> None:
        row = TaskRow.of(_task(), _NAMES)
        assert row.assigned_to == _LEAD
        assert row.assigned_to_name == "Ada Chen"

    def test_an_unknown_key_resolves_to_no_name_rather_than_the_key(self) -> None:
        row = TaskRow.of(_task(), {})
        # ``_LEAD`` is a UUID, so there is no readable word inside it. The row
        # says so rather than handing the surface something it would print.
        assert row.assigned_to_name is None

    def test_a_word_owner_is_already_a_name(self) -> None:
        item = _plan_item().model_copy(update={"owner": NotBlankStr("Backend")})
        assert PlanItemRow.of(item, {}).owner_name == "Backend"

    def test_transition_row_names_whoever_asked(self) -> None:
        row = LifecycleTransitionRow.of(_transition(_LEAD), _NAMES)
        assert row.requested_by == _LEAD
        assert row.requested_by_name == "Ada Chen"

    def test_a_transition_nobody_asked_for_names_nobody(self) -> None:
        # The system moved it on its own schedule, which is a different
        # statement from an unresolvable reference and reads the same on the
        # surface: neither prints a key.
        unasked = LifecycleTransitionRow.of(_transition(None), _NAMES)
        unresolvable = LifecycleTransitionRow.of(_transition(_LEAD), {})

        assert unasked.requested_by_name is None
        assert unresolvable.requested_by_name is None

    def test_project_row_names_the_lead(self) -> None:
        project = Project(
            id=as_uuid("proj-tetris"),
            name=NotBlankStr("Tetris"),
            description="Falling blocks, shared leaderboard",
            lead=NotBlankStr(_LEAD),
        )
        assert ProjectRow.of(project, _NAMES).lead_name == "Ada Chen"

    def test_plan_row_names_every_item_owner(self) -> None:
        plan = Plan(
            project=sid("proj-tetris"),
            project_name=NotBlankStr("Tetris"),
            objective_id=sid("obj-1"),
            objective_title=NotBlankStr("A browser Tetris with a leaderboard"),
            parent_task_id=sid("task-objective"),
            items=(_plan_item(),),
            created_at=datetime(2026, 4, 1, tzinfo=UTC),
            updated_at=datetime(2026, 4, 1, tzinfo=UTC),
        )
        row = PlanRow.of(plan, _NAMES)
        assert [item.owner_name for item in row.items] == ["Ada Chen"]

    def test_a_computed_field_on_the_base_does_not_break_the_row(self) -> None:
        # ``MessageOverhead.is_quadratic`` is derived. Rebuilding the row from a
        # full dump would feed that derived value back into a model forbidding
        # extras, which fails the whole request rather than one field.
        record = CoordinationMetricsRecord(
            task_id=NotBlankStr("task-1"),
            agent_id=NotBlankStr(_LEAD),
            computed_at=datetime(2026, 4, 1, tzinfo=UTC),
            team_size=5,
            metrics=CoordinationMetrics(
                message_overhead=MessageOverhead(
                    team_size=5, message_count=20, quadratic_threshold=0.5
                )
            ),
        )
        row = CoordinationMetricsRow.of(record, _NAMES, {"task-1": "Ship it"})
        assert row.agent_name == "Ada Chen"
        assert row.task_title == "Ship it"
        assert row.metrics.message_overhead is not None
        assert row.metrics.message_overhead.is_quadratic is True

    def test_a_system_run_has_no_agent_to_name(self) -> None:
        record = CoordinationMetricsRecord(
            task_id=NotBlankStr("task-1"),
            agent_id=None,
            computed_at=datetime(2026, 4, 1, tzinfo=UTC),
            team_size=2,
            metrics=CoordinationMetrics(),
        )
        row = CoordinationMetricsRow.of(record, _NAMES, {})
        assert row.agent_id is None
        assert row.agent_name is None
        assert row.task_title is None
