"""Unit coverage for the ``ObjectiveController`` boundary.

The controller is a thin HTTP boundary over the
:class:`ObjectiveEntryAdapter`: ``POST /objectives`` mints a
submission id, spawns the pipeline run as a background task tracked
on ``AppState``, and returns ``202 Accepted``. The pipeline is NOT
awaited synchronously, so the controller does not block on team
coordination.

These tests target the package-level ``submit_objective_impl``
helper to avoid the Litestar route-handler wrapper while exercising
the identical control flow the production HTTP handler runs.
"""

import asyncio
from typing import Any, Final

import pytest
from pydantic import ValidationError

from synthorg.api.controllers.objectives import (
    SubmitObjectiveAck,
    SubmitObjectivePayload,
    submit_objective_impl,
)
from synthorg.core.task_enums import TaskStatus
from synthorg.engine.pipeline.entry.objective_adapter import (
    ObjectiveEntryAdapter,
    ObjectiveSubmission,
)
from synthorg.engine.pipeline.models import (
    ExecutionPath,
    RoutingVerdict,
    WorkItem,
    WorkPhaseResult,
    WorkPipelineResult,
    WorkSource,
)
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit

_DONE_CALLBACK_POLL_ITERATIONS: Final[int] = 10


def _payload(**overrides: Any) -> SubmitObjectivePayload:
    base: dict[str, Any] = {
        "title": "Ship the v0.8 release",
        "description": "Cut a stable v0.8 release with release notes.",
        "requested_by": "human-operator",
    }
    base.update(overrides)
    return SubmitObjectivePayload(**base)


def _result(work_item: WorkItem) -> WorkPipelineResult:
    return WorkPipelineResult(
        work_item=work_item,
        verdict=RoutingVerdict.SPLITTABLE,
        execution_path=ExecutionPath.TEAM,
        task_id="task-99",
        final_task_status=TaskStatus.IN_REVIEW,
        phases=(WorkPhaseResult(phase="intake", success=True, duration_seconds=0.0),),
        total_duration_seconds=0.2,
    )


def _state_with_recording_adapter() -> tuple[Any, list[ObjectiveSubmission]]:
    captured: list[ObjectiveSubmission] = []

    async def _submit(submission: ObjectiveSubmission) -> WorkPipelineResult:
        captured.append(submission)
        return _result(
            WorkItem(
                origin_adapter_id="t",
                source=WorkSource.OBJECTIVE,
                title=submission.title,
                raw_intent=submission.description,
                project="objectives",
                requested_by=submission.requested_by,
                correlation_id=submission.submission_id,
            )
        )

    adapter = mock_of[ObjectiveEntryAdapter](submit=_submit)
    app_state = make_app_state(objective_entry_adapter=adapter)
    return app_state, captured


async def test_submit_returns_202_payload_with_minted_submission_id() -> None:
    app_state, _ = _state_with_recording_adapter()
    response = await submit_objective_impl(app_state, _payload())
    ack = response.data
    assert isinstance(ack, SubmitObjectiveAck)
    assert ack.status == "accepted"
    assert ack.submission_id


async def test_submit_spawns_background_task_drives_adapter() -> None:
    app_state, captured = _state_with_recording_adapter()
    response = await submit_objective_impl(app_state, _payload())
    ack = response.data
    assert ack is not None
    assert len(app_state.objective_background_tasks) == 1
    pending = next(iter(app_state.objective_background_tasks))
    await pending
    assert len(captured) == 1
    submission = captured[0]
    assert submission.title == "Ship the v0.8 release"
    assert submission.requested_by == "human-operator"
    assert submission.submission_id == ack.submission_id


async def test_submit_does_not_block_on_pipeline_run() -> None:
    """The HTTP-equivalent handler returns before the pipeline completes."""
    captured: list[ObjectiveSubmission] = []
    release = asyncio.Event()

    async def _slow_submit(submission: ObjectiveSubmission) -> WorkPipelineResult:
        captured.append(submission)
        await release.wait()
        return _result(
            WorkItem(
                origin_adapter_id="t",
                source=WorkSource.OBJECTIVE,
                title=submission.title,
                raw_intent=submission.description,
                project="objectives",
                requested_by=submission.requested_by,
            )
        )

    adapter = mock_of[ObjectiveEntryAdapter](submit=_slow_submit)
    app_state = make_app_state(objective_entry_adapter=adapter)
    response = await submit_objective_impl(app_state, _payload())
    ack = response.data
    assert ack is not None
    assert ack.status == "accepted"
    assert len(app_state.objective_background_tasks) == 1
    release.set()
    pending = next(iter(app_state.objective_background_tasks))
    await pending


async def test_done_callback_discards_task_from_set() -> None:
    """A completed task is removed from the tracking set."""
    app_state, _ = _state_with_recording_adapter()
    await submit_objective_impl(app_state, _payload())
    pending = next(iter(app_state.objective_background_tasks))
    await pending
    for _ in range(_DONE_CALLBACK_POLL_ITERATIONS):
        if not app_state.objective_background_tasks:
            break
        await asyncio.sleep(0)
    assert len(app_state.objective_background_tasks) == 0


def test_payload_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        SubmitObjectivePayload(
            title="t",
            description="d",
            requested_by="r",
            unknown="x",  # type: ignore[call-arg]
        )


def test_payload_rejects_blank_fields() -> None:
    with pytest.raises(ValidationError):
        SubmitObjectivePayload(title="   ", description="d", requested_by="r")


async def test_submit_rejects_unknown_enum_string() -> None:
    """A bogus enum string in the payload surfaces as ValidationError.

    The HTTP payload accepts ``priority`` / ``estimated_complexity`` /
    ``task_type`` as strings (Litestar deserialises whatever the client
    sends). ``_build_submission`` then constructs
    :class:`ObjectiveSubmission` which coerces the string against the
    real enum; an unknown value must fail loudly rather than silently
    falling through.
    """
    app_state, _ = _state_with_recording_adapter()
    bogus = SubmitObjectivePayload(
        title="t",
        description="d",
        requested_by="r",
        priority="BOGUS_PRIORITY",
    )
    with pytest.raises(ValidationError):
        await submit_objective_impl(app_state, bogus)
