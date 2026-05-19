"""Unit tests for work pipeline domain models."""

from typing import Any

import pytest
from pydantic import ValidationError

from synthorg.core.enums import Complexity, Priority, TaskStatus, TaskType
from synthorg.engine.pipeline.models import (
    ExecutionPath,
    RoutingVerdict,
    WorkItem,
    WorkPhaseResult,
    WorkPipelineResult,
    WorkSource,
)

pytestmark = pytest.mark.unit


def _work_item(**overrides: Any) -> WorkItem:
    base: dict[str, Any] = {
        "origin_adapter_id": "simulation-harness",
        "source": WorkSource.SIMULATION,
        "title": "Add a health endpoint",
        "raw_intent": "Implement GET /health returning 200 with status JSON.",
        "project": "proj-1",
        "requested_by": "operator-1",
    }
    base.update(overrides)
    return WorkItem(**base)


class TestWorkSourceAndVerdict:
    def test_work_source_members(self) -> None:
        assert WorkSource.SIMULATION.value == "simulation"
        assert WorkSource.INTAKE.value == "intake"
        assert WorkSource.TASK_BOARD.value == "task_board"
        assert WorkSource.OBJECTIVE.value == "objective"
        assert WorkSource.CONVERSATIONAL.value == "conversational"

    def test_routing_verdict_members(self) -> None:
        assert RoutingVerdict.LEAF.value == "leaf"
        assert RoutingVerdict.SPLITTABLE.value == "splittable"

    def test_execution_path_members(self) -> None:
        assert ExecutionPath.SOLO.value == "solo"
        assert ExecutionPath.TEAM.value == "team"


class TestWorkItem:
    def test_minimal_construction_applies_defaults(self) -> None:
        item = _work_item()
        assert item.priority is Priority.MEDIUM
        assert item.task_type is TaskType.DEVELOPMENT
        assert item.estimated_complexity is Complexity.MEDIUM
        assert item.acceptance_criteria == ()
        assert item.correlation_id  # auto-generated, non-blank
        assert item.created_at is not None

    def test_explicit_fields_preserved(self) -> None:
        item = _work_item(
            priority=Priority.HIGH,
            task_type=TaskType.RESEARCH,
            estimated_complexity=Complexity.COMPLEX,
            acceptance_criteria=("returns 200", "json body"),
            correlation_id="corr-xyz",
        )
        assert item.priority is Priority.HIGH
        assert item.task_type is TaskType.RESEARCH
        assert item.estimated_complexity is Complexity.COMPLEX
        assert item.acceptance_criteria == ("returns 200", "json body")
        assert item.correlation_id == "corr-xyz"

    def test_frozen(self) -> None:
        item = _work_item()
        with pytest.raises(ValidationError):
            item.title = "changed"  # type: ignore[misc]

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            _work_item(unexpected="nope")

    @pytest.mark.parametrize(
        "blank_field",
        ["origin_adapter_id", "title", "raw_intent", "project", "requested_by"],
    )
    def test_blank_identifiers_rejected(self, blank_field: str) -> None:
        with pytest.raises(ValidationError):
            _work_item(**{blank_field: "   "})


class TestWorkPhaseResult:
    def test_success_phase(self) -> None:
        phase = WorkPhaseResult(phase="intake", success=True, duration_seconds=0.5)
        assert phase.error is None

    def test_failed_phase_requires_error(self) -> None:
        with pytest.raises(ValidationError, match="failed phase"):
            WorkPhaseResult(phase="intake", success=False, duration_seconds=0.1)

    def test_success_phase_rejects_error(self) -> None:
        with pytest.raises(ValidationError, match="successful phase"):
            WorkPhaseResult(
                phase="intake",
                success=True,
                duration_seconds=0.1,
                error="should not be here",
            )

    def test_negative_duration_rejected(self) -> None:
        with pytest.raises(ValidationError):
            WorkPhaseResult(phase="intake", success=True, duration_seconds=-1.0)

    def test_frozen(self) -> None:
        phase = WorkPhaseResult(phase="intake", success=True, duration_seconds=0.0)
        with pytest.raises(ValidationError):
            phase.success = False  # type: ignore[misc]


class TestWorkPipelineResult:
    def _result(self, **overrides: Any) -> WorkPipelineResult:
        base: dict[str, Any] = {
            "work_item": _work_item(),
            "verdict": RoutingVerdict.LEAF,
            "execution_path": ExecutionPath.SOLO,
            "task_id": "task-1",
            "final_task_status": TaskStatus.IN_REVIEW,
            "phases": (
                WorkPhaseResult(phase="intake", success=True, duration_seconds=0.1),
            ),
            "is_success": True,
            "total_duration_seconds": 0.2,
        }
        base.update(overrides)
        return WorkPipelineResult(**base)

    def test_construction(self) -> None:
        result = self._result()
        assert result.verdict is RoutingVerdict.LEAF
        assert result.execution_path is ExecutionPath.SOLO
        assert result.final_task_status is TaskStatus.IN_REVIEW

    def test_phases_must_be_non_empty(self) -> None:
        with pytest.raises(ValidationError):
            self._result(phases=())

    def test_frozen(self) -> None:
        result = self._result()
        with pytest.raises(ValidationError):
            result.is_success = False  # type: ignore[misc]

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            self._result(surprise=1)
