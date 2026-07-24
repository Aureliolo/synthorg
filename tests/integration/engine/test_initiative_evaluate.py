# module-kind: tests
"""End-to-end proof that only a passing evaluation delivers an initiative.

Drives the EVALUATE stage through its public ``schedule`` + ``drain`` path: the
lead runs a real ``ReactLoop`` over a scripted provider that calls
``submit_evaluation``, and the verdict either completes the plan or sends the
gap back as new work. Only the LLM is a deterministic stand-in.
"""

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

import pytest
from pydantic import JsonValue

from synthorg.api.services.plan_service import PlanService
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.project import Project
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.completion import StallReason
from synthorg.engine.initiative.evaluate import EvaluationStageService
from synthorg.hr.registry import AgentRegistryService
from synthorg.providers.protocol import CompletionProvider
from tests._shared import (
    FakeClock,
    as_uuid,
    mock_of,
    sid,
)
from tests._shared import (
    RecordingReplanTrigger as _RecordingReplanTrigger,
)
from tests._shared.scripted_provider import (
    ScriptedProvider,
    build_tool_call_response,
    make_text_response,
)
from tests.unit.api.fakes_backend import FakePersistenceBackend

pytestmark = pytest.mark.integration

_LEAD_ID = as_uuid("evaluate-lead")
_PLAN_ID = "evaluate-plan"
_PROJECT = "evaluate-proj"
_CRITERIA = (NotBlankStr("the game is playable"), NotBlankStr("it saves scores"))


def _lead() -> AgentIdentity:
    return AgentIdentity(
        id=_LEAD_ID,
        name="Delivery Lead",
        role="Engineering Manager",
        department="Engineering",
        model=ModelConfig(provider="test-provider", model_id="test-small-001"),
        hiring_date=date(2026, 1, 1),
    )


def _project() -> Project:
    return Project(
        id=as_uuid(_PROJECT),
        name=NotBlankStr("Tetris"),
        plan_id=as_uuid(_PLAN_ID),
        team=(NotBlankStr(str(_LEAD_ID)),),
        lead=NotBlankStr(str(_LEAD_ID)),
    )


def _plan(status: PlanStatus = PlanStatus.EVALUATING) -> Plan:
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
        objective_criteria=_CRITERIA,
        created_at=now,
        updated_at=now,
    )


def _verdicts(*outcomes: str) -> JsonValue:
    return [
        {
            "criterion": criterion,
            "outcome": outcome,
            "evidence": f"ran the build and observed {outcome}",
        }
        for criterion, outcome in zip(_CRITERIA, outcomes, strict=True)
    ]


async def _seed(
    provider: CompletionProvider,
    *,
    plan: Plan,
    replan_trigger: _RecordingReplanTrigger | None = None,
) -> tuple[EvaluationStageService, FakePersistenceBackend]:
    """Build the stage over a seeded backend.

    Returns:
        The service and the backend, so the test reads the persisted verdict.
    """
    backend = FakePersistenceBackend()
    await backend.plans.save(plan)
    await backend.projects.save(_project())
    clock = FakeClock()
    service = EvaluationStageService(
        persistence=backend,
        agent_registry=mock_of[AgentRegistryService](
            get=AsyncMock(return_value=_lead())
        ),
        provider_selector=lambda _identity: provider,
        default_provider=None,
        plan_status_writer=PlanService(repo=backend.plans, clock=clock),
        replan_trigger=replan_trigger,
        config_resolver=None,
        clock=clock,
    )
    return service, backend


async def _plan_status(backend: FakePersistenceBackend) -> PlanStatus:
    """Read the plan's persisted status.

    Returns:
        The current :class:`PlanStatus`.
    """
    plan = await backend.plans.get(NotBlankStr(sid(_PLAN_ID)))
    assert plan is not None
    return plan.status


async def test_every_criterion_met_delivers_the_initiative() -> None:
    provider = ScriptedProvider(
        [
            build_tool_call_response(
                "submit_evaluation",
                {
                    "summary": "Played it end to end.",
                    "verdicts": _verdicts("met", "met"),
                },
            ),
            make_text_response("Evaluation submitted."),
        ]
    )
    service, backend = await _seed(provider, plan=_plan())

    service.schedule(plan=_plan())
    await service.drain(timeout_sec=30.0)

    assert await _plan_status(backend) is PlanStatus.COMPLETED


async def test_an_unmet_criterion_sends_the_gap_back_as_work() -> None:
    trigger = _RecordingReplanTrigger()
    provider = ScriptedProvider(
        [
            build_tool_call_response(
                "submit_evaluation",
                {
                    "summary": "It plays but loses scores.",
                    "verdicts": _verdicts("met", "unmet"),
                },
            ),
            make_text_response("Evaluation submitted."),
        ]
    )
    service, backend = await _seed(provider, plan=_plan(), replan_trigger=trigger)

    service.schedule(plan=_plan())
    await service.drain(timeout_sec=30.0)

    assert await _plan_status(backend) is PlanStatus.EVALUATING
    assert trigger.fired == [(sid(_PLAN_ID), StallReason.EVALUATION_UNMET)]


async def test_a_partial_criterion_does_not_deliver() -> None:
    """Mostly met is the case a lenient gate would wave through."""
    trigger = _RecordingReplanTrigger()
    provider = ScriptedProvider(
        [
            build_tool_call_response(
                "submit_evaluation",
                {
                    "summary": "Scores save sometimes.",
                    "verdicts": _verdicts("met", "partial"),
                },
            ),
            make_text_response("Evaluation submitted."),
        ]
    )
    service, backend = await _seed(provider, plan=_plan(), replan_trigger=trigger)

    service.schedule(plan=_plan())
    await service.drain(timeout_sec=30.0)

    assert await _plan_status(backend) is PlanStatus.EVALUATING
    assert len(trigger.fired) == 1


async def test_no_verdict_leaves_the_initiative_parked() -> None:
    """Fail closed: an evaluation that never happened is not a pass."""
    provider = ScriptedProvider([make_text_response("I'd rather not say.")])
    service, backend = await _seed(provider, plan=_plan())

    service.schedule(plan=_plan())
    await service.drain(timeout_sec=30.0)

    assert await _plan_status(backend) is PlanStatus.EVALUATING


async def test_an_incomplete_verdict_is_returned_for_correction() -> None:
    """A dropped criterion is rejected in-session, not silently accepted."""
    provider = ScriptedProvider(
        [
            build_tool_call_response(
                "submit_evaluation",
                {
                    "summary": "Only checked one.",
                    "verdicts": [
                        {
                            "criterion": _CRITERIA[0],
                            "outcome": "met",
                            "evidence": "played it",
                        }
                    ],
                },
            ),
            build_tool_call_response(
                "submit_evaluation",
                {
                    "summary": "Checked both.",
                    "verdicts": _verdicts("met", "met"),
                },
            ),
            make_text_response("Evaluation submitted."),
        ]
    )
    service, backend = await _seed(provider, plan=_plan())

    service.schedule(plan=_plan())
    await service.drain(timeout_sec=30.0)

    # The first submission was rejected and the corrected one accepted.
    assert await _plan_status(backend) is PlanStatus.COMPLETED


async def test_a_plan_that_left_evaluating_is_not_judged() -> None:
    provider = ScriptedProvider([make_text_response("unused")])
    service, _ = await _seed(provider, plan=_plan(status=PlanStatus.EXECUTING))

    service.schedule(plan=_plan(status=PlanStatus.EXECUTING))
    await service.drain(timeout_sec=30.0)

    assert provider.call_count == 0


async def test_an_objective_without_criteria_is_not_judged() -> None:
    """There is nothing to judge against, and passing would invent a verdict."""
    provider = ScriptedProvider([make_text_response("unused")])
    plan = _plan().model_copy(update={"objective_criteria": ()})
    service, backend = await _seed(provider, plan=plan)

    service.schedule(plan=plan)
    await service.drain(timeout_sec=30.0)

    assert provider.call_count == 0
    assert await _plan_status(backend) is PlanStatus.EVALUATING
