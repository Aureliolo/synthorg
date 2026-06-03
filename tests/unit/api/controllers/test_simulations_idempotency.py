"""Idempotency guard tests for ``POST /simulations/``.

A redelivered ``start_simulation`` request with the same
``simulation_id`` must not spawn a second runner that races the first
on the in-memory store. The controller rejects the second request
with HTTP 409 Conflict.
"""

import asyncio
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from litestar.connection import Request
from litestar.datastructures import State

from synthorg.api.controllers.simulations import (
    SimulationController,
    StartSimulationPayload,
)
from synthorg.client.models import SimulationConfig
from synthorg.client.simulation_state import ClientSimulationState
from synthorg.client.state import client_simulation_state_of
from synthorg.client.store import SimulationStore
from synthorg.core.domain_errors import ConflictError
from synthorg.settings.resolver import ConfigResolver
from tests._shared import make_app_state

pytestmark = pytest.mark.unit


def _make_config(simulation_id: str = "sim-001") -> SimulationConfig:
    return SimulationConfig(
        simulation_id=simulation_id,
        project_id="proj-1",
        clients_per_round=1,
        requirements_per_client=1,
    )


def _make_state(*, claim_succeeds: bool) -> State:
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
    # ``ClientSimulationState`` ``spec=`` already auto-mocks
    # ``intake_engine`` / ``pool`` / ``feedback_store`` to mocks that
    # mirror the real attribute types; we don't need to override
    # them. ``_publish_event`` and the runner spawn are patched in
    # the tests so the bodies of these attributes never get touched.
    sim_state.background_tasks = set()
    app_state = make_app_state(
        client_simulation_state=sim_state,
        config_resolver=MagicMock(spec=ConfigResolver),
    )
    return State({"app_state": app_state})


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
        sim_store = cast(
            AsyncMock, client_simulation_state_of(state.app_state).simulation_store
        )
        sim_store.register_if_absent.assert_awaited_once()

    async def test_first_request_passes_idempotency_check(self) -> None:
        """A fresh ``simulation_id`` survives the idempotency check.

        We patch the WS publish helper and the runner-spawning hooks
        so the controller can complete the happy path without
        bootstrapping the full app -- this lets the test assert
        ``register_if_absent`` was awaited without swallowing
        unrelated downstream errors.
        """
        state = _make_state(claim_succeeds=True)
        ctrl = SimulationController(owner=SimulationController)  # type: ignore[arg-type]
        payload = StartSimulationPayload(config=_make_config(simulation_id="sim-002"))

        # Patch the boundary collaborators so the handler reaches the
        # end of its body without raising. We're not exercising
        # publish-ws-event or the background runner here; the
        # integration suite covers that. Narrowed patches replace the
        # earlier ``contextlib.suppress(Exception)`` which would
        # mask any future regression that happened to raise here.
        # ``_publish_event`` is patched to a no-op so we don't need
        # the WS backbone wired. ``asyncio.create_task`` is patched
        # to return a real (immediately-cancelled) Task object so the
        # subsequent ``task.add_done_callback`` calls in the
        # controller's spawn block work without needing the runner
        # body to actually run.
        async def _noop() -> None:
            return None

        def _spawn_dummy_task(coro: Any, *_a: Any, **_kw: Any) -> asyncio.Task[None]:
            # Close the real runner coro (we are not exercising it) and hand
            # back a genuine, immediately-completing ``Task`` so the
            # controller's ``spawned_task: asyncio.Task[None]`` assignment and
            # its ``add_done_callback`` wiring are satisfied. ``loop.create_task``
            # is used (not the patched ``asyncio.create_task``) to build it.
            coro.close()
            return asyncio.get_running_loop().create_task(_noop())

        with (
            patch(
                "synthorg.api.controllers.simulations._publish_event",
                lambda *_a, **_kw: None,
            ),
            patch(
                "synthorg.api.controllers.simulations.asyncio.create_task",
                _spawn_dummy_task,
            ),
        ):
            await ctrl.start_simulation.fn(
                ctrl,
                request=_make_request(),
                state=state,
                data=payload,
            )
        sim_store = cast(
            AsyncMock, client_simulation_state_of(state.app_state).simulation_store
        )
        sim_store.register_if_absent.assert_awaited_once()
