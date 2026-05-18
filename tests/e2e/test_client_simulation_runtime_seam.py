"""Acceptance: the client-simulation runtime is wired at the seam.

Drives a real ``ClientRequest`` through the ``IntakeEngine`` built by
the production ``build_client_simulation_runtime`` (the exact code
``create_app`` runs at construction) against a real ``TaskEngine`` --
not a stub strategy. Proves the gate-relevant construction works:
the default ``direct`` strategy walks a SUBMITTED request to
TASK_CREATED and a real task lands in the task engine, with no
provider configured and zero LLM spend.

The full HTTP boot path is covered by
``tests/e2e/test_client_simulation_e2e.py``; this isolates the
builder so the assertion is on the runtime itself.
"""

from collections.abc import AsyncGenerator

import pytest

from synthorg.api.state import AppState
from synthorg.client.models import ClientRequest, RequestStatus, TaskRequirement
from synthorg.client.runtime_builder import build_client_simulation_runtime
from synthorg.client.simulation_state import ClientSimulationState
from synthorg.engine.intake.strategies import DirectIntake
from synthorg.engine.task_engine import TaskEngine
from tests._shared import mock_of
from tests.unit.api.fakes import FakePersistenceBackend

pytestmark = pytest.mark.e2e


@pytest.fixture
async def persistence() -> AsyncGenerator[FakePersistenceBackend]:
    backend = FakePersistenceBackend()
    await backend.connect()
    yield backend
    await backend.disconnect()


@pytest.fixture
async def task_engine(
    persistence: FakePersistenceBackend,
) -> AsyncGenerator[TaskEngine]:
    engine = TaskEngine(persistence=persistence)
    await engine.start()
    yield engine
    await engine.stop()


async def test_builder_wires_intake_engine_to_real_task_engine(
    task_engine: TaskEngine,
) -> None:
    app_state = mock_of[AppState](
        task_engine=task_engine,
        has_task_engine=True,
        has_active_provider=False,
        has_cost_tracker=False,
    )

    state = build_client_simulation_runtime(app_state, env={})

    # Gate-relevant construction: a populated runtime state.
    assert isinstance(state, ClientSimulationState)
    assert state.intake_engine is not None
    assert state.review_pipeline is not None
    assert state.review_pipeline.stage_names == ("internal",)
    # Default strategy is the no-LLM DirectIntake.
    assert isinstance(state.intake_engine.strategy, DirectIntake)

    request = ClientRequest(
        client_id="seam-client",
        requirement=TaskRequirement(
            title="Seam feature",
            description="Drive a request through the wired intake engine.",
        ),
    )
    assert request.status is RequestStatus.SUBMITTED

    final, result = await state.intake_engine.process(request)

    # The request walked to the terminal TASK_CREATED state and a real
    # task was created in the task engine (not a stubbed id).
    assert final.status is RequestStatus.TASK_CREATED
    assert result.accepted is True
    assert result.task_id is not None
    created = await task_engine.get_task(result.task_id)
    assert created is not None
    assert created.id == result.task_id
    assert created.title == "Seam feature"
