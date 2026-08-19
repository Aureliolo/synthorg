"""Parking a subtask nobody can take is a level, not an edge.

Two invariants, both about a pass that runs more than once. Routing re-runs
over EVERY subtask of the plan on every coordination pass, and a resumed or
recovery-driven plan runs the pass again, so the park is asked for repeatedly
against rows it already moved.

1. A row already carrying an outcome is left alone. The state machine has no
   ``BLOCKED -> BLOCKED`` hop, so re-asserting the park is refused, and the
   refusal surfaced as a raw ``ValueError`` at WARNING every recovery cadence,
   for ever, against a row that was in exactly the state the park wanted.
2. A refused park is REPORTED. The engine answers a refusal with an
   unsuccessful result rather than an exception, so the handler that promises
   to name a failed park only ever covered the failures that cannot happen,
   and the one failure mode the engine actually produces was silent.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog
from structlog.testing import capture_logs

from synthorg.core.task_enums import BlockedReason, TaskStatus
from synthorg.engine.coordination.service import MultiAgentCoordinator
from synthorg.engine.decomposition.service import DecompositionService
from synthorg.engine.parallel import ParallelExecutor
from synthorg.engine.routing.service import TaskRoutingService
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import (
    TaskMutationResult,
    TransitionTaskMutation,
)
from tests._shared import coerce_id, mock_of
from tests.unit.engine.conftest import (
    make_assignment_task,
    make_decomposition,
    make_routing,
    make_subtask,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _structlog_capture_setup() -> None:
    """structlog needs a bound processor before ``capture_logs`` sees records."""
    structlog.reset_defaults()


def _engine(  # type: ignore[explicit-any]  # mock_of returns Any
    statuses: dict[str, TaskStatus],
    *,
    accepts: bool = True,
) -> Any:
    rows = {
        coerce_id(label): make_assignment_task(
            id=label,
            status=status,
            assigned_to=None if status is TaskStatus.CREATED else "alice",
        )
        for label, status in statuses.items()
    }

    def _get(task_id: str) -> object | None:
        return rows.get(task_id)

    def _submit(mutation: TransitionTaskMutation) -> TaskMutationResult:
        if accepts:
            return TaskMutationResult(request_id="r", success=True, version=1)
        return TaskMutationResult(
            request_id="r",
            success=False,
            error="Invalid task status transition: 'blocked' -> 'blocked'",
            error_code="validation",
        )

    return mock_of[TaskEngine](
        get_task=AsyncMock(side_effect=_get),
        submit=AsyncMock(side_effect=_submit),
    )


async def _park(engine: Any) -> None:  # type: ignore[explicit-any]
    coordinator = MultiAgentCoordinator(
        decomposition_service=AsyncMock(spec=DecompositionService),
        routing_service=MagicMock(spec=TaskRoutingService),
        parallel_executor=AsyncMock(spec=ParallelExecutor),
        task_engine=engine,
    )
    await coordinator._park_unroutable(
        make_routing([], unroutable=("sub-b",)),
        make_decomposition((make_subtask("sub-b"),)),
    )


def _parks(engine: Any) -> list[TransitionTaskMutation]:  # type: ignore[explicit-any]
    return [
        call.args[0]
        for call in engine.submit.await_args_list
        if (call.args[0].overrides or {}).get("blocked_reason")
        is BlockedReason.NO_CAPABLE_AGENT
    ]


class TestUnroutableParkIsIdempotent:
    async def test_a_row_still_awaiting_dispatch_is_parked(self) -> None:
        engine = _engine({"sub-b": TaskStatus.CREATED})
        await _park(engine)
        assert [p.task_id for p in _parks(engine)] == [coerce_id("sub-b")]

    @pytest.mark.parametrize(
        "status",
        [
            TaskStatus.BLOCKED,
            TaskStatus.FAILED,
            TaskStatus.COMPLETED,
            TaskStatus.CANCELLED,
        ],
    )
    async def test_a_row_with_an_outcome_is_left_alone(
        self, status: TaskStatus
    ) -> None:
        engine = _engine({"sub-b": status})
        await _park(engine)
        assert _parks(engine) == []

    async def test_a_row_the_engine_does_not_hold_is_still_parked(self) -> None:
        # No row is not an outcome: the plan filed this subtask and nothing
        # answers for it, so the park is attempted and its verdict decides.
        engine = _engine({})
        await _park(engine)
        assert [p.task_id for p in _parks(engine)] == [coerce_id("sub-b")]


class TestRefusedParkIsReported:
    async def test_an_unsuccessful_result_is_logged_as_a_park_failure(self) -> None:
        engine = _engine({"sub-b": TaskStatus.CREATED}, accepts=False)
        with capture_logs() as caplog:
            await _park(engine)
        errors = [record for record in caplog if record.get("log_level") == "error"]
        assert errors, "a refused park must be reported, not discarded"
        assert errors[-1].get("error_type") == "TaskMutationRejected"
        assert errors[-1].get("subtask_id") == coerce_id("sub-b")

    async def test_an_accepted_park_reports_nothing(self) -> None:
        engine = _engine({"sub-b": TaskStatus.CREATED})
        with capture_logs() as caplog:
            await _park(engine)
        assert [r for r in caplog if r.get("log_level") == "error"] == []
