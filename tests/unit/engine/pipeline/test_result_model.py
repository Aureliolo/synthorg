"""Unit tests for the WorkPipelineResult refinement-handoff invariant."""

import pytest
from pydantic import ValidationError

from synthorg.core.task_enums import TaskStatus
from synthorg.engine.pipeline.models import (
    ExecutionPath,
    RefinementHandoff,
    RoutingVerdict,
    WorkItem,
    WorkPhaseResult,
    WorkPipelineResult,
    WorkSource,
)

pytestmark = pytest.mark.unit


def _work_item() -> WorkItem:
    return WorkItem(
        origin_adapter_id="harness",
        source=WorkSource.OBJECTIVE,
        title="A goal",
        raw_intent="Do the thing.",
        project="proj",
        requested_by="operator",
    )


def _handoff() -> RefinementHandoff:
    return RefinementHandoff(
        conversation_id="conv-1",
        needs_clarification=True,
        detail="What does done look like?",
    )


_PHASES = (
    WorkPhaseResult(phase="refinement_handoff", success=True, duration_seconds=0.0),
)


def _result(
    *,
    execution_path: ExecutionPath,
    handoff: RefinementHandoff | None,
) -> WorkPipelineResult:
    return WorkPipelineResult(
        work_item=_work_item(),
        verdict=RoutingVerdict.SPLITTABLE,
        execution_path=execution_path,
        task_id="task-1",
        final_task_status=TaskStatus.CREATED,
        phases=_PHASES,
        refinement_handoff=handoff,
        total_duration_seconds=0.0,
    )


def test_refinement_path_with_handoff_is_valid() -> None:
    result = _result(execution_path=ExecutionPath.REFINEMENT, handoff=_handoff())

    assert result.execution_path is ExecutionPath.REFINEMENT
    assert result.refinement_handoff is not None
    assert result.is_success is True


def test_refinement_path_without_handoff_rejected() -> None:
    with pytest.raises(ValidationError, match="requires a refinement_handoff"):
        _result(execution_path=ExecutionPath.REFINEMENT, handoff=None)


def test_non_refinement_path_with_handoff_rejected() -> None:
    with pytest.raises(ValidationError, match="only valid on the REFINEMENT path"):
        _result(execution_path=ExecutionPath.TEAM, handoff=_handoff())
