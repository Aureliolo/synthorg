"""Unit tests for the run-narrative domain models."""

import pytest
from pydantic import ValidationError

from synthorg.core.enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.narrative.models import (
    AgentContribution,
    NarrativeProse,
    OpenItem,
    ReducedDecision,
    ReducedRun,
    RunMetric,
    SourceRef,
)
from synthorg.project_brain.models import BrainEntryKind, BrainEntryStatus

pytestmark = pytest.mark.unit


def _decision() -> ReducedDecision:
    return ReducedDecision(
        title="Adopt event-sourced ledger",
        outcome="Event-sourced ledger",
        rationale="Auditability outweighs the write-amplification cost.",
        alternatives=("CRUD ledger", "Append-only log"),
        sources=(
            SourceRef(
                label="Brain entry e7",
                url="#brain-entry-e7",
                kind=NotBlankStr("entry"),
            ),
        ),
    )


class TestReducedModels:
    def test_reduced_run_round_trips(self) -> None:
        run = ReducedRun(
            project_id=NotBlankStr("proj-1"),
            task_id=NotBlankStr("task-1"),
            execution_id=NotBlankStr("exec-1"),
            brief_title=NotBlankStr("Ship checkout"),
            final_status=TaskStatus.COMPLETED,
            metrics=(RunMetric(name="Turns", value="42"),),
            decisions=(_decision(),),
            contributions=(
                AgentContribution(
                    agent_id=NotBlankStr("agent-a"),
                    turn_count=10,
                    cost=1.5,
                    tools=("read", "write"),
                ),
            ),
            outcomes=("Checkout shipped",),
            open_items=(
                OpenItem(
                    kind=BrainEntryKind.RISK,
                    title="Latency under load",
                    status=BrainEntryStatus.ACTIVE,
                ),
            ),
            sources=(
                SourceRef(
                    label="Task task-1",
                    url="#task-task-1",
                    kind=NotBlankStr("task"),
                ),
            ),
        )
        assert run.final_status is TaskStatus.COMPLETED
        assert run.decisions[0].alternatives == ("CRUD ledger", "Append-only log")

    def test_models_are_frozen(self) -> None:
        ref = SourceRef(label="Task t1", url="#task-t1", kind=NotBlankStr("task"))
        with pytest.raises(ValidationError):
            ref.label = "mutated"  # type: ignore[misc]

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            RunMetric(name="Cost", value="1.0", currency="USD")  # type: ignore[call-arg]

    def test_metric_value_is_string(self) -> None:
        metric = RunMetric(name="Cost", value="2.50", unit=NotBlankStr("USD"))
        assert metric.value == "2.50"
        assert metric.unit == "USD"


class TestNarrativeProse:
    def test_summary_required(self) -> None:
        with pytest.raises(ValidationError):
            NarrativeProse()  # type: ignore[call-arg]

    def test_optional_sections_default_none(self) -> None:
        prose = NarrativeProse(summary="A clean run.")
        assert prose.decisions is None
        assert prose.contributions is None
        assert prose.outcomes is None

    def test_blank_summary_rejected(self) -> None:
        with pytest.raises(ValidationError):
            NarrativeProse(summary="")
