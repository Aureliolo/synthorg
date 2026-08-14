"""Tests for the EVALUATE stage's fail-closed guards.

The integration suite drives the happy paths through a real ``ReactLoop``. This
covers what that tier cannot reach economically: every branch where the stage
declines to produce a verdict, and the reconcile callback that closes the loop
after one lands. Each of them is load-bearing, because this module owns the only
write that can deliver an initiative.
"""

from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from synthorg.api.services.plan_service_factory import build_plan_service
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.evaluation_verdict import CriterionOutcome, CriterionVerdict
from synthorg.core.persistence_errors import QueryError
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.project import Project
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.completion import StallReason
from synthorg.engine.initiative.evaluate import EvaluationStageService
from synthorg.engine.initiative.evaluate_models import EvaluationReport
from synthorg.hr.registry import AgentRegistryService
from synthorg.persistence.evaluation_report_protocol import (
    EvaluationReportFilterSpec,
    EvaluationReportRecord,
)
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
        model=ModelConfig(provider="test-provider", model_id="test-basic-001"),
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
    workspace_root: Path | None = None,
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
        plan_status_writer=build_plan_service(backend, clock=clock),
        replan_trigger=None if replan_trigger is None else lambda: replan_trigger,
        reconcile=reconcile,
        config_resolver=config_resolver,
        workspace_root=workspace_root,
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


def _stub_judgement(monkeypatch: pytest.MonkeyPatch, report: EvaluationReport) -> None:
    """Make the paid session return *report* without running one.

    Patched on the class rather than the instance: the stage declares
    ``__slots__``, so an instance attribute would not take.
    """

    async def _judged(
        _self: EvaluationStageService, _plan: Plan, _project: Project
    ) -> EvaluationReport:
        return report

    monkeypatch.setattr(EvaluationStageService, "_judge", _judged)


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

    async def test_a_trigger_wired_after_the_stage_is_still_used(self) -> None:
        """The trigger is read per verdict, not captured at construction.

        The stage and the trigger are separate subsystems converging on their
        own schedules, so a coordinator arriving after the provider registry
        would otherwise leave the stage holding the ``None`` it was built with
        and park every unmet initiative for the life of the process.
        """
        held: _RecordingReplanTrigger | None = None
        service, _ = await _seed(plan=_plan(), project=_project())
        service._replan_trigger = lambda: held

        await service._apply(_plan(), _report(CriterionOutcome.UNMET))
        held = _RecordingReplanTrigger()
        await service._apply(_plan(), _report(CriterionOutcome.UNMET))

        assert held.fired == [(sid(_PLAN_ID), StallReason.EVALUATION_UNMET)]


class TestRecordingAVerdict:
    """The verdict outlives the status it decides."""

    async def test_a_verdict_is_persisted_with_its_evidence(self) -> None:
        service, backend = await _seed(plan=_plan(), project=_project())

        await service._record(_plan(), _report(CriterionOutcome.UNMET))

        records = await backend.evaluation_reports.query(
            EvaluationReportFilterSpec(plan_id=NotBlankStr(sid(_PLAN_ID))),
        )
        assert len(records) == 1
        assert records[0].attempt == 1
        assert records[0].objective_met is False
        assert records[0].project_id == sid(_PROJECT)
        assert records[0].verdicts[0].evidence == "ran the build and watched it"

    async def test_a_second_judgement_is_a_new_attempt(self) -> None:
        """Overwriting would erase the evidence the replan points at."""
        service, backend = await _seed(plan=_plan(), project=_project())

        await service._record(_plan(), _report(CriterionOutcome.UNMET))
        await service._record(_plan(), _report(CriterionOutcome.MET))

        records = await backend.evaluation_reports.query(
            EvaluationReportFilterSpec(plan_id=NotBlankStr(sid(_PLAN_ID))),
        )
        assert [r.attempt for r in records] == [2, 1]
        assert [r.objective_met for r in records] == [True, False]

    async def test_a_failed_record_write_parks_rather_than_completing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A verdict nobody can read afterwards is, to a later reader, none.

        This stage parks on an absent verdict, and a judgement that did not
        persist is absent the moment the process ends. Completing on one
        would mark an initiative delivered with nothing to point at when an
        operator asks why. Parking costs a re-judgement on the next
        recompute, which is recoverable; the unevidenced COMPLETED is not.
        """
        service, backend = await _seed(plan=_plan(), project=_project())
        _stub_judgement(monkeypatch, _report(CriterionOutcome.MET))

        async def _refuse(_record: EvaluationReportRecord) -> None:
            msg = "store down"
            raise QueryError(msg)

        monkeypatch.setattr(backend.evaluation_reports, "append", _refuse)

        await service._run(_plan())

        assert await _status(backend) is PlanStatus.EVALUATING

    async def test_the_verdict_lands_before_the_status_write(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A lost CAS race must cost the transition, never the judgement."""
        plan = _plan()
        service, backend = await _seed(plan=plan, project=_project())
        _stub_judgement(monkeypatch, _report(CriterionOutcome.MET))

        await service._run(plan)

        records = await backend.evaluation_reports.query(
            EvaluationReportFilterSpec(plan_id=NotBlankStr(sid(_PLAN_ID))),
        )
        assert len(records) == 1
        assert await _status(backend) is PlanStatus.COMPLETED


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


class TestJudgeWorkspaceScope:
    """What the judge can read decides what it can honestly judge.

    Every other test here stubs the judgement wholesale, so without these
    the scoping could be reverted to the shared base root -- letting a
    session read a sibling project's files -- and nothing would fail.
    """

    async def test_read_tools_are_scoped_to_the_plans_own_project(
        self, tmp_path: Path
    ) -> None:
        plan = _plan()
        workspace = tmp_path / "projects" / str(plan.project)
        workspace.mkdir(parents=True)
        service, _ = await _seed(plan=plan, project=_project(), workspace_root=tmp_path)

        tools = service._read_tools(plan)

        assert tools
        assert all(
            tool.workspace_root == workspace.resolve()  # type: ignore[attr-defined]
            for tool in tools
        )

    async def test_a_sibling_projects_workspace_is_not_reachable(
        self, tmp_path: Path
    ) -> None:
        """Two projects share a root; the judge sees only the one it judges."""
        plan = _plan()
        (tmp_path / "projects" / str(plan.project)).mkdir(parents=True)
        sibling = tmp_path / "projects" / "other-project"
        sibling.mkdir(parents=True)
        (sibling / "secret.py").write_text("theirs", encoding="utf-8")
        service, _ = await _seed(plan=plan, project=_project(), workspace_root=tmp_path)

        tools = service._read_tools(plan)

        for tool in tools:
            with pytest.raises(ValueError, match="escapes workspace"):
                tool.path_validator.validate("../other-project/secret.py")  # type: ignore[attr-defined]

    async def test_an_unprovisioned_workspace_judges_without_reads(
        self, tmp_path: Path
    ) -> None:
        """A missing directory must not abort the judgement.

        The file tools refuse a missing root, so letting that propagate
        would burn every attempt and park the plan over a project that was
        simply never provisioned.
        """
        service, _ = await _seed(
            plan=_plan(), project=_project(), workspace_root=tmp_path
        )

        assert service._read_tools(_plan()) == ()

    async def test_no_workspace_root_grants_no_tools(self) -> None:
        service, _ = await _seed(plan=_plan(), project=_project())

        assert service._read_tools(_plan()) == ()
