"""Unit tests for recording an approved plan's decisions into the brain."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg._core.features import BaseFeatureStateSlice
from synthorg.api.controllers._plan_decision_record import record_plan_decisions
from synthorg.core.plan import Plan, PlanItem, PlanOption
from synthorg.core.plan_enums import PlanItemKind, PlanStatus
from synthorg.core.types import NotBlankStr
from synthorg.project_brain.models import BrainEntryKind, BrainEntryStatus
from synthorg.project_brain.service import ProjectBrainService
from synthorg.project_brain.state import ProjectBrainStateSlice
from tests._shared import as_uuid, make_app_state, mock_of, sid

pytestmark = pytest.mark.unit

_Configured = Any  # type: ignore[explicit-any]
_NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)


def _work_item(label: str) -> PlanItem:
    return PlanItem(
        id=NotBlankStr(sid(label)),
        title=NotBlankStr(f"Build {label}"),
        description=NotBlankStr(f"Work for {label}"),
        acceptance_criteria=(NotBlankStr("done"),),
        expected_artifacts=(NotBlankStr(f"src/{label}.py"),),
    )


def _opt(oid: str, *, recommended: bool = False) -> PlanOption:
    return PlanOption(
        id=NotBlankStr(oid),
        title=NotBlankStr(f"Option {oid}"),
        summary=NotBlankStr(f"Tradeoffs for {oid}"),
        recommended=recommended,
    )


def _decision_item(label: str, *, chosen_option_id: str | None = None) -> PlanItem:
    return PlanItem(
        id=NotBlankStr(sid(label)),
        title=NotBlankStr(f"Decide {label}"),
        description=NotBlankStr(f"Choice for {label}"),
        acceptance_criteria=(NotBlankStr("decision recorded"),),
        kind=PlanItemKind.DECISION,
        options=(_opt("react", recommended=True), _opt("svelte")),
        chosen_option_id=chosen_option_id,
    )


def _plan(items: tuple[PlanItem, ...]) -> Plan:
    return Plan(
        id=as_uuid("plan-1"),
        project=NotBlankStr("proj-1"),
        objective_id=NotBlankStr("obj-1"),
        objective_title=NotBlankStr("Ship the thing"),
        parent_task_id=NotBlankStr(str(as_uuid("task-1"))),
        items=items,
        status=PlanStatus.APPROVED,
        created_at=_NOW,
        updated_at=_NOW,
    )


async def _seed(*, with_brain: bool = True) -> tuple[_Configured, _Configured]:
    brain = mock_of[ProjectBrainService](append_entry=AsyncMock(return_value=None))
    slices: dict[type[BaseFeatureStateSlice], dict[str, object]] | None = (
        {ProjectBrainStateSlice: {"service": brain}} if with_brain else None
    )
    state = make_app_state(slices=slices)
    return state, brain


class TestRecordPlanDecisions:
    async def test_records_the_chosen_option(self) -> None:
        plan = _plan((_work_item("a"), _decision_item("d", chosen_option_id="svelte")))
        state, brain = await _seed()

        await record_plan_decisions(state, plan, decided_by="admin")

        brain.append_entry.assert_awaited_once()
        kwargs = brain.append_entry.await_args.kwargs
        assert kwargs["project_id"] == "proj-1"
        assert kwargs["status"] is BrainEntryStatus.ACCEPTED
        assert kwargs["author"] == "admin"
        payload = kwargs["payload"]
        assert payload.entry_kind is BrainEntryKind.DECISION
        assert payload.decision_outcome == "Option svelte"
        assert payload.alternatives == ("Option react",)

    async def test_falls_back_to_the_recommended_option(self) -> None:
        plan = _plan((_decision_item("d"),))
        state, brain = await _seed()

        await record_plan_decisions(state, plan, decided_by="admin")

        payload = brain.append_entry.await_args.kwargs["payload"]
        assert payload.decision_outcome == "Option react"
        assert payload.alternatives == ("Option svelte",)

    async def test_records_one_entry_per_decision(self) -> None:
        plan = _plan((_decision_item("d1"), _work_item("a"), _decision_item("d2")))
        state, brain = await _seed()

        await record_plan_decisions(state, plan, decided_by="admin")

        assert brain.append_entry.await_count == 2

    async def test_work_only_plan_records_nothing(self) -> None:
        plan = _plan((_work_item("a"), _work_item("b")))
        state, brain = await _seed()

        await record_plan_decisions(state, plan, decided_by="admin")

        brain.append_entry.assert_not_called()

    async def test_no_brain_service_is_noop(self) -> None:
        plan = _plan((_decision_item("d"),))
        state, _ = await _seed(with_brain=False)

        await record_plan_decisions(state, plan, decided_by="admin")

    async def test_brain_write_failure_never_raises(self) -> None:
        plan = _plan((_decision_item("d"),))
        state, brain = await _seed()
        brain.append_entry.side_effect = RuntimeError("brain down")

        await record_plan_decisions(state, plan, decided_by="admin")
