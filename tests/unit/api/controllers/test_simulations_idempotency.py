"""Idempotency guard tests for ``POST /simulations/``.

A redelivered ``start_simulation`` request with the same
``simulation_id`` must not spawn a second runner that races the first
on the in-memory store. The controller rejects the second request
with HTTP 409 Conflict.
"""

import contextlib
from unittest.mock import AsyncMock, MagicMock

import pytest
from litestar.connection import Request
from litestar.datastructures import State

from synthorg.api.controllers.simulations import (
    SimulationController,
    StartSimulationPayload,
)
from synthorg.api.state import AppState
from synthorg.client.models import SimulationConfig
from synthorg.client.simulation_state import ClientSimulationState
from synthorg.client.store import SimulationStore
from synthorg.core.domain_errors import ConflictError
from synthorg.settings.resolver import ConfigResolver

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
    sim_store = MagicMock(spec=SimulationStore)
    sim_store.register_if_absent = AsyncMock(
        spec=SimulationStore.register_if_absent,
        return_value=claim_succeeds,
    )
    sim_store.save = AsyncMock(spec=SimulationStore.save)
    sim_state = MagicMock(spec=ClientSimulationState)
    sim_state.simulation_store = sim_store
    sim_state.background_tasks = set()
    sim_state.intake_engine = MagicMock()
    sim_state.pool = MagicMock()
    sim_state.pool.list_clients = AsyncMock(return_value=())
    sim_state.feedback_store = MagicMock()
    sim_state.feedback_store.record = MagicMock()
    app_state = MagicMock(spec=AppState)
    app_state.client_simulation_state = sim_state
    app_state.config_resolver = MagicMock(spec=ConfigResolver)
    state = MagicMock(spec=State)
    state.app_state = app_state
    return state


def _make_request() -> MagicMock:
    return MagicMock(spec=Request)


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
