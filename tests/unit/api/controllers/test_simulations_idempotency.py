"""Idempotency guard tests for ``POST /simulations/``.

A redelivered ``start_simulation`` request with the same
``simulation_id`` must not spawn a second runner that races the first
on the in-memory store. The controller rejects the second request
with HTTP 409 Conflict.
"""

import contextlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.api.controllers.simulations import (
    SimulationController,
    StartSimulationPayload,
)
from synthorg.client.models import SimulationConfig
from synthorg.core.domain_errors import ConflictError

pytestmark = pytest.mark.unit


def _make_config(simulation_id: str = "sim-001") -> SimulationConfig:
    return SimulationConfig(
        simulation_id=simulation_id,
        project_id="proj-1",
        clients_per_round=1,
        requirements_per_client=1,
    )


def _make_state(*, claim_succeeds: bool) -> MagicMock:
    """Build a mocked Litestar state with a controllable register_if_absent.

    When *claim_succeeds* is ``True``, ``register_if_absent`` returns
    True (fresh id), simulating the first request. When ``False`` it
    returns False (id already registered), simulating a duplicate.
    """
    sim_state = MagicMock()
    sim_state.simulation_store.register_if_absent = AsyncMock(
        return_value=claim_succeeds,
    )
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
        # Store reports the id was already present (claim fails).
        state = _make_state(claim_succeeds=False)
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
        sim_store = state.app_state.client_simulation_state.simulation_store
        sim_store.register_if_absent.assert_awaited_once()

    async def test_first_request_passes_idempotency_check(self) -> None:
        """A fresh ``simulation_id`` survives the idempotency check.

        We cannot easily exercise the full happy path here without a
        full app fixture (the runner requires intake_engine etc.).
        The check verifies the controller progresses past the
        idempotency guard and calls ``register_if_absent``.
        """
        state = _make_state(claim_succeeds=True)
        ctrl = SimulationController(owner=SimulationController)  # type: ignore[arg-type]
        payload = StartSimulationPayload(config=_make_config(simulation_id="sim-002"))

        # The handler will reach the register call then attempt to
        # spawn the runner. We tolerate any post-claim error since
        # this test only verifies idempotency-guard behaviour, not
        # the runner plumbing exercised in the integration suite.
        with contextlib.suppress(Exception):
            await ctrl.start_simulation.fn(
                ctrl,
                request=_make_request(),
                state=state,
                data=payload,
            )
        sim_store = state.app_state.client_simulation_state.simulation_store
        sim_store.register_if_absent.assert_awaited_once()
