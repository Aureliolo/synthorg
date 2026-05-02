"""Idempotency guard tests for ``POST /simulations/``.

Per audit #133: a redelivered ``start_simulation`` request with the
same ``simulation_id`` must not spawn a second runner that races the
first on the in-memory store. The controller now rejects the second
request with HTTP 409 Conflict.
"""

import contextlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.api.controllers.simulations import (
    SimulationController,
    StartSimulationPayload,
)
from synthorg.client.models import SimulationConfig
from synthorg.client.store import SimulationRecord
from synthorg.core.domain_errors import ConflictError

pytestmark = pytest.mark.unit


def _make_config(simulation_id: str = "sim-001") -> SimulationConfig:
    return SimulationConfig(
        simulation_id=simulation_id,
        project_id="proj-1",
        clients_per_round=1,
        requirements_per_client=1,
    )


def _make_state_with_existing(record: SimulationRecord | None) -> MagicMock:
    """Build a mocked Litestar state whose simulation_store returns *record*."""
    sim_state = MagicMock()
    if record is None:

        async def _raise(_id: str) -> SimulationRecord:
            del _id
            msg = "Simulation not found"
            raise KeyError(msg)

        sim_state.simulation_store.get = _raise
    else:

        async def _return(_id: str) -> SimulationRecord:
            del _id
            return record

        sim_state.simulation_store.get = _return
    sim_state.simulation_store.save = AsyncMock()
    sim_state.background_tasks = set()
    sim_state.intake_engine = MagicMock()
    sim_state.pool = MagicMock()
    sim_state.pool.list_clients = AsyncMock(return_value=())
    sim_state.feedback_store = MagicMock()
    sim_state.feedback_store.record = MagicMock()
    app_state = MagicMock()
    app_state.client_simulation_state = sim_state
    app_state.config_resolver = MagicMock()
    state = MagicMock()
    state.app_state = app_state
    return state


def _make_request() -> MagicMock:
    return MagicMock()


class TestSimulationsIdempotency:
    """Duplicate ``simulation_id`` is rejected with HTTP 409."""

    async def test_duplicate_id_rejected_with_conflict(self) -> None:
        existing = SimulationRecord(
            simulation_id="sim-001",
            config=_make_config(),
            status="running",
        )
        state = _make_state_with_existing(existing)
        ctrl = SimulationController(owner=SimulationController)  # type: ignore[arg-type]
        payload = StartSimulationPayload(config=_make_config())

        with pytest.raises(ConflictError) as exc:
            await ctrl.start_simulation.fn(
                ctrl,
                request=_make_request(),
                state=state,
                data=payload,
            )
        assert exc.value.status_code == 409
        assert "already exists" in str(exc.value)

    async def test_first_request_passes_idempotency_check(self) -> None:
        """A fresh ``simulation_id`` survives the idempotency check.

        We cannot easily exercise the full happy path here without a
        full app fixture (the runner requires intake_engine etc.).
        The check verifies the controller progresses past the
        idempotency guard and reaches ``simulation_store.save``.
        """
        state = _make_state_with_existing(None)
        ctrl = SimulationController(owner=SimulationController)  # type: ignore[arg-type]
        payload = StartSimulationPayload(config=_make_config(simulation_id="sim-002"))

        # The handler will reach .save() then attempt to spawn the
        # runner. We tolerate any post-save error since this test
        # only verifies idempotency-guard behaviour, not the runner
        # plumbing exercised in the integration suite.
        with contextlib.suppress(Exception):
            await ctrl.start_simulation.fn(
                ctrl,
                request=_make_request(),
                state=state,
                data=payload,
            )
        sim_store = state.app_state.client_simulation_state.simulation_store
        sim_store.save.assert_awaited_once()
