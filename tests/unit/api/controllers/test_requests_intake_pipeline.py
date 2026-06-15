"""Unit coverage for the real-intake background reconciliation.

``process_intake_pipeline`` runs the injected work-entry adapter and
reconciles the stored ``ClientRequest`` to its terminal status. It is
the coroutine the ``POST /requests/{id}/approve`` handler spawns; the
sync handler contract (202 + APPROVED + task spawned) is covered by
the integration suite.
"""

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.controllers.requests.pipeline import process_intake_pipeline
from synthorg.api.state import AppState
from synthorg.client.models import ClientRequest, RequestStatus, TaskRequirement
from synthorg.client.simulation_state import ClientSimulationState
from synthorg.config.schema import RootConfig
from synthorg.core.task_enums import TaskStatus
from synthorg.engine.errors import ProjectNotFoundError
from synthorg.engine.pipeline.errors import WorkIntakeRejectedError
from synthorg.engine.pipeline.models import (
    ExecutionPath,
    RoutingVerdict,
    WorkItem,
    WorkPhaseResult,
    WorkPipelineResult,
    WorkSource,
)
from tests._shared import make_app_state

pytestmark = pytest.mark.unit

_REQUEST_ID = "req-1"


class _StubAdapter:
    """Work-entry adapter double with a scripted outcome."""

    def __init__(
        self,
        *,
        result: WorkPipelineResult | None = None,
        error: BaseException | None = None,
    ) -> None:
        self._result = result
        self._error = error
        self.calls = 0

    @property
    def source(self) -> WorkSource:
        return WorkSource.INTAKE

    async def submit(self, request: ClientRequest) -> WorkPipelineResult:
        del request
        self.calls += 1
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def _work_item() -> WorkItem:
    return WorkItem(
        origin_adapter_id="intake-entry-adapter",
        source=WorkSource.INTAKE,
        title="t",
        raw_intent="d",
        project="client-intake",
        requested_by="acme",
        correlation_id=_REQUEST_ID,
    )


def _result(task_id: str) -> WorkPipelineResult:
    return WorkPipelineResult(
        work_item=_work_item(),
        verdict=RoutingVerdict.LEAF,
        execution_path=ExecutionPath.SOLO,
        task_id=task_id,
        final_task_status=TaskStatus.IN_REVIEW,
        phases=(WorkPhaseResult(phase="intake", success=True, duration_seconds=0.0),),
        total_duration_seconds=0.1,
    )


def _request(status: RequestStatus = RequestStatus.APPROVED) -> ClientRequest:
    return ClientRequest(
        request_id=_REQUEST_ID,
        client_id="acme",
        requirement=TaskRequirement(title="t", description="d"),
        status=status,
    )


async def _state(
    adapter: _StubAdapter,
    *,
    seeded: ClientRequest,
) -> tuple[AppState, ClientSimulationState]:
    sim_state = ClientSimulationState(intake_default_project="client-intake")
    await sim_state.request_store.save(seeded)
    app_state = make_app_state(
        config=RootConfig(company_name="t"),
        approval_store=ApprovalStore(),
        intake_entry_adapter=adapter,
        client_simulation_state=sim_state,
    )
    return app_state, sim_state


async def test_success_walks_to_task_created() -> None:
    adapter = _StubAdapter(result=_result("task-9"))
    app_state, sim_state = await _state(adapter, seeded=_request())

    await process_intake_pipeline(
        app_state=app_state,
        sim_state=sim_state,
        request_id=_REQUEST_ID,
    )

    final = await sim_state.request_store.get(_REQUEST_ID)
    assert final.status is RequestStatus.TASK_CREATED
    assert final.metadata["task_id"] == "task-9"
    assert adapter.calls == 1


async def test_intake_rejection_cancels_request() -> None:
    adapter = _StubAdapter(error=WorkIntakeRejectedError("not actionable"))
    app_state, sim_state = await _state(adapter, seeded=_request())

    await process_intake_pipeline(
        app_state=app_state,
        sim_state=sim_state,
        request_id=_REQUEST_ID,
    )

    final = await sim_state.request_store.get(_REQUEST_ID)
    assert final.status is RequestStatus.CANCELLED
    rejection_reason = final.metadata["rejection_reason"]
    assert isinstance(rejection_reason, str)
    assert "not actionable" in rejection_reason


async def test_pipeline_error_cancels_request() -> None:
    adapter = _StubAdapter(error=ProjectNotFoundError(project_id="ghost"))
    app_state, sim_state = await _state(adapter, seeded=_request())

    await process_intake_pipeline(
        app_state=app_state,
        sim_state=sim_state,
        request_id=_REQUEST_ID,
    )

    final = await sim_state.request_store.get(_REQUEST_ID)
    assert final.status is RequestStatus.CANCELLED


async def test_concurrent_terminal_state_is_respected() -> None:
    adapter = _StubAdapter(result=_result("task-9"))
    app_state, sim_state = await _state(
        adapter,
        seeded=_request(status=RequestStatus.CANCELLED),
    )

    await process_intake_pipeline(
        app_state=app_state,
        sim_state=sim_state,
        request_id=_REQUEST_ID,
    )

    final = await sim_state.request_store.get(_REQUEST_ID)
    assert final.status is RequestStatus.CANCELLED
    assert adapter.calls == 0


async def test_memory_error_is_reraised() -> None:
    adapter = _StubAdapter(error=MemoryError())
    app_state, sim_state = await _state(adapter, seeded=_request())

    with pytest.raises(MemoryError):
        await process_intake_pipeline(
            app_state=app_state,
            sim_state=sim_state,
            request_id=_REQUEST_ID,
        )


async def test_vanished_request_evicts_lock_registry_entry() -> None:
    # The early-return paths must drop the per-request lock entry
    # from ``_request_locks`` so the dict cannot leak an entry per
    # orphaned id. Without the eviction, a vanished request acquired
    # under ``acquire_request_lock`` would leave the Lock object
    # behind even though the refcount drops to zero.
    adapter = _StubAdapter(result=_result("task-9"))
    app_state, sim_state = await _state(adapter, seeded=_request())
    await sim_state.request_store.delete(_REQUEST_ID)

    await process_intake_pipeline(
        app_state=app_state,
        sim_state=sim_state,
        request_id=_REQUEST_ID,
    )

    assert _REQUEST_ID not in app_state.request_locks._locks
    assert adapter.calls == 0


async def test_non_approved_request_evicts_lock_registry_entry() -> None:
    adapter = _StubAdapter(result=_result("task-9"))
    app_state, sim_state = await _state(
        adapter,
        seeded=_request(status=RequestStatus.CANCELLED),
    )

    await process_intake_pipeline(
        app_state=app_state,
        sim_state=sim_state,
        request_id=_REQUEST_ID,
    )

    assert _REQUEST_ID not in app_state.request_locks._locks
    assert adapter.calls == 0
