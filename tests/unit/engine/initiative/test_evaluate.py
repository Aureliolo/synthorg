"""Tests for the EVALUATE stage's fail-closed guards.

The integration suite drives the happy paths through a real ``ReactLoop``. This
covers what that tier cannot reach economically: every branch where the stage
declines to produce a verdict, and the reconcile callback that closes the loop
after one lands. Each of them is load-bearing, because this module owns the only
write that can deliver an initiative.
"""

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from synthorg.api.services.plan_service import PlanService
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.project import Project
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.completion import StallReason
from synthorg.engine.initiative.evaluate import EvaluationStageService
from synthorg.engine.initiative.evaluate_models import (
    CriterionOutcome,
    CriterionVerdict,
    EvaluationReport,
)
from synthorg.hr.registry import AgentRegistryService
from synthorg.providers.protocol import CompletionProvider
from synthorg.settings.resolver import ConfigResolver
from tests._shared import (
    FakeClock,
    as_uuid,
    mock_of,
    sid,
)
from tests._shared import (
    RecordingReplanTrigger as _RecordingReplanTrigger,
)
from tests.unit.api.fakes_backend import FakePersistenceBackend

pytestmark = pytest.mark.unit

_LEAD_ID = as_uuid("evaluate-lead")
_PLAN_ID = "evaluate-plan"
_PROJECT = "evaluate-proj"
_CRITERIA = (NotBlankStr("the game is playable"),)


class _RecordingReconcile:
    """A reconcile port that records the plans it was asked to re-derive."""

    def __init__(self) -> None:
        self.recomputed: list[UUID] = []

    async def recompute(self, plan_id: UUID) -> None:
        self.recomputed.append(plan_id)


def _lead() -> AgentIdentity:
    return AgentIdentity(
        id=_LEAD_ID,
        name="Delivery Lead",
        role="Engineering Manager",
        department="Engineering",
        model=ModelConfig(provider="test-provider", model_id="test-small-001"),
        hiring_date=date(2026, 1, 1),
    )


def _project(*, with_lead: bool = True) -> Project:
    return Project(
        id=as_uuid(_PROJECT),
        name=NotBlankStr("Tetris"),
        plan_id=as_uuid(_PLAN_ID),
        team=(NotBlankStr(str(_LEAD_ID)),) if with_lead else (),
        lead=NotBlankStr(str(_LEAD_ID)) if with_lead else None,
    )


def _plan(
    status: PlanStatus = PlanStatus.EVALUATING,
    *,
    criteria: tuple[NotBlankStr, ...] = _CRITERIA,
) -> Plan:
    now = datetime(2026, 7, 24, tzinfo=UTC)
    return Plan(
        id=as_uuid(_PLAN_ID),
        project=NotBlankStr(sid(_PROJECT)),
        objective_id=NotBlankStr("obj-1"),
        objective_title=NotBlankStr("Ship the game"),
        parent_task_id=NotBlankStr(sid("parent-1")),
        items=(
            PlanItem(
                id=NotBlankStr(sid("item-a")),
                title=NotBlankStr("Board grid"),
                description=NotBlankStr("Render the grid"),
                acceptance_criteria=(NotBlankStr("grid renders"),),
                expected_artifacts=(NotBlankStr("src/board.py"),),
            ),
        ),
        status=status,
        objective_criteria=criteria,
        created_at=now,
        updated_at=now,
    )


def _report(outcome: CriterionOutcome) -> EvaluationReport:
    return EvaluationReport(
        summary=NotBlankStr("Played it end to end."),
        verdicts=(
            CriterionVerdict(
                criterion=_CRITERIA[0],
                outcome=outcome,
                evidence=NotBlankStr("ran the build and watched it"),
            ),
        ),
    )


async def _seed(
    *,
    plan: Plan | None = None,
    project: Project | None = None,
    provider: CompletionProvider | None = None,
    lead: AgentIdentity | None = None,
    replan_trigger: _RecordingReplanTrigger | None = None,
    reconcile: _RecordingReconcile | None = None,
    config_resolver: ConfigResolver | None = None,
) -> tuple[EvaluationStageService, FakePersistenceBackend]:
    """Build the stage over a seeded backend.

    Returns:
        The service and the backend, so a test reads the persisted status.
    """
    backend = FakePersistenceBackend()
    if plan is not None:
        await backend.plans.save(plan)
    if project is not None:
        await backend.projects.save(project)
    clock = FakeClock()

    def selector(_identity: AgentIdentity) -> CompletionProvider:
        return provider  # type: ignore[return-value]

    service = EvaluationStageService(
        persistence=backend,
        agent_registry=mock_of[AgentRegistryService](get=AsyncMock(return_value=lead)),
        provider_selector=selector,
        default_provider=provider,
        plan_status_writer=PlanService(repo=backend.plans, clock=clock),
        replan_trigger=replan_trigger,
        reconcile=reconcile,
        config_resolver=config_resolver,
        clock=clock,
    )
    return service, backend


async def _status(backend: FakePersistenceBackend) -> PlanStatus | None:
    """Read the plan's persisted status.

    Returns:
        The current status, or ``None`` when the plan is absent.
    """
    plan = await backend.plans.get(NotBlankStr(sid(_PLAN_ID)))
    return None if plan is None else plan.status


async def _fire(service: EvaluationStageService, plan: Plan) -> None:
    """Schedule the stage and wait for the detached judgement to finish."""
    service.schedule(plan=plan)
    await service.drain(timeout_sec=5.0)


class TestFailClosed:
    """Every branch that cannot produce a verdict parks the plan."""

    async def test_a_plan_that_left_evaluating_is_not_judged(self) -> None:
        plan = _plan(PlanStatus.EXECUTING)
        service, backend = await _seed(plan=plan, project=_project())

        await _fire(service, plan)

        assert await _status(backend) is PlanStatus.EXECUTING

    async def test_a_missing_plan_is_not_judged(self) -> None:
        service, backend = await _seed(project=_project())

        await _fire(service, _plan())

        assert await _status(backend) is None

    async def test_an_objective_with_no_criteria_parks(self) -> None:
        """There is nothing to judge against, and passing would be a guess."""
        plan = _plan(criteria=())
        service, backend = await _seed(plan=plan, project=_project())

        await _fire(service, plan)

        assert await _status(backend) is PlanStatus.EVALUATING

    async def test_a_missing_project_parks(self) -> None:
        plan = _plan()
        service, backend = await _seed(plan=plan)

        await _fire(service, plan)

        assert await _status(backend) is PlanStatus.EVALUATING

    async def test_an_unresolvable_lead_parks(self) -> None:
        """Nobody is accountable for the verdict, so nobody delivers it."""
        plan = _plan()
        service, backend = await _seed(
            plan=plan, project=_project(with_lead=False), lead=None
        )

        await _fire(service, plan)

        assert await _status(backend) is PlanStatus.EVALUATING

    async def test_no_provider_parks(self) -> None:
        plan = _plan()
        service, backend = await _seed(
            plan=plan, project=_project(), lead=_lead(), provider=None
        )

        await _fire(service, plan)

        assert await _status(backend) is PlanStatus.EVALUATING

    async def test_a_failing_settings_read_does_not_stop_the_stage(self) -> None:
        """A settings outage degrades to the documented defaults, not a crash."""
        plan = _plan()
        service, backend = await _seed(
            plan=plan,
            project=_project(),
            lead=_lead(),
            config_resolver=mock_of[ConfigResolver](
                get_int=AsyncMock(side_effect=RuntimeError("settings down")),
                get_float=AsyncMock(side_effect=RuntimeError("settings down")),
            ),
        )

        await _fire(service, plan)

        assert await _status(backend) is PlanStatus.EVALUATING


class TestApplyingAVerdict:
    """What happens once a verdict exists."""

    async def test_a_met_verdict_completes_and_reconciles(self) -> None:
        """The write mutates no task, so nothing else would re-derive the graph."""
        reconcile = _RecordingReconcile()
        service, backend = await _seed(
            plan=_plan(), project=_project(), reconcile=reconcile
        )

        await service._apply(_plan(), _report(CriterionOutcome.MET))

        assert await _status(backend) is PlanStatus.COMPLETED
        assert reconcile.recomputed == [as_uuid(_PLAN_ID)]

    async def test_completion_without_a_reconcile_port_still_completes(self) -> None:
        service, backend = await _seed(plan=_plan(), project=_project())

        await service._apply(_plan(), _report(CriterionOutcome.MET))

        assert await _status(backend) is PlanStatus.COMPLETED

    async def test_a_plan_that_moved_during_the_judgement_is_not_completed(
        self,
    ) -> None:
        """A verdict is only good for the plan it was reached about."""
        service, backend = await _seed(
            plan=_plan(PlanStatus.SUPERSEDED), project=_project()
        )

        await service._apply(_plan(), _report(CriterionOutcome.MET))

        assert await _status(backend) is PlanStatus.SUPERSEDED

    @pytest.mark.parametrize(
        "outcome",
        [CriterionOutcome.UNMET, CriterionOutcome.PARTIAL],
        ids=lambda value: str(value.value),
    )
    async def test_an_unmet_verdict_replans_with_the_evidence(
        self, outcome: CriterionOutcome
    ) -> None:
        """The judged evidence is the only account of what actually failed."""
        trigger = _RecordingReplanTrigger()
        service, backend = await _seed(
            plan=_plan(), project=_project(), replan_trigger=trigger
        )

        await service._apply(_plan(), _report(outcome))

        assert trigger.fired == [(sid(_PLAN_ID), StallReason.EVALUATION_UNMET)]
        assert trigger.details[0] is not None
        assert "the game is playable" in trigger.details[0]
        assert await _status(backend) is PlanStatus.EVALUATING

    async def test_an_unmet_verdict_without_a_trigger_parks(self) -> None:
        service, backend = await _seed(plan=_plan(), project=_project())

        await service._apply(_plan(), _report(CriterionOutcome.UNMET))

        assert await _status(backend) is PlanStatus.EVALUATING


class TestScheduling:
    """The stage is fired from a best-effort observer on every recompute."""

    async def test_a_burst_of_schedules_collapses_to_one_judgement(self) -> None:
        plan = _plan()
        service, backend = await _seed(plan=plan, project=_project())

        service.schedule(plan=plan)
        service.schedule(plan=plan)
        await service.drain(timeout_sec=5.0)

        assert await _status(backend) is PlanStatus.EVALUATING

    async def test_repeated_scheduling_stops_at_the_attempt_cap(self) -> None:
        """Each judgement is a paid session; a plan that never resolves must
        stop spending rather than re-judge on every passing task event."""
        plan = _plan()
        service, _ = await _seed(plan=plan, project=_project())

        for _ in range(6):
            service.schedule(plan=plan)
            await service.settle(timeout_sec=5.0)

        assert service.attempts_for(plan) == 3

    async def test_a_drained_stage_refuses_new_judgements(self) -> None:
        """The task engine still runs when the tails are drained."""
        plan = _plan()
        service, _ = await _seed(plan=plan, project=_project())

        await service.drain(timeout_sec=5.0)
        service.schedule(plan=plan)

        assert service.attempts_for(plan) == 0
