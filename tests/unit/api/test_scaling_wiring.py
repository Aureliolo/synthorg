"""Unit tests for ``wire_scaling`` startup wiring.

Covers the default opt-out (no service when ``hr.scaling_enabled`` is
unset), idempotency for a re-entered lifespan, the persistence- and
collaborator-absent skips, and the opt-in happy path that constructs the
pipeline and hydrates the durable hiring requests.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.lifecycle_helpers.scaling_wiring import wire_scaling
from synthorg.api.state import AppState
from synthorg.approval.state import ApprovalStateSlice
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.scaling.service import ScalingService
from synthorg.hr.state import HrStateSlice
from synthorg.persistence.hiring_request_protocol import HiringRequestRepository
from synthorg.persistence.state import PersistenceStateSlice
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit

_ENABLED_ENV = "SYNTHORG_HR_SCALING_ENABLED"


def _ready_app_state(*, backend: object | None = object()) -> AppState:
    """App state with registry + tracker + approval store + persistence."""
    return make_app_state(
        slices={
            HrStateSlice: {
                "agent_registry": AgentRegistryService(),
                "performance_tracker": PerformanceTracker(),
                "scaling_service": None,
            },
            ApprovalStateSlice: {"store": ApprovalStore()},
            PersistenceStateSlice: {"backend": backend},
        },
    )


async def test_disabled_by_default_wires_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(_ENABLED_ENV, raising=False)
    app_state = _ready_app_state()
    await wire_scaling(app_state)
    assert app_state.slice(HrStateSlice).scaling_service is None


async def test_already_wired_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENABLED_ENV, "true")
    existing = mock_of[ScalingService]()
    app_state = make_app_state(
        slices={
            HrStateSlice: {
                "agent_registry": AgentRegistryService(),
                "performance_tracker": PerformanceTracker(),
                "scaling_service": existing,
            },
        },
    )
    await wire_scaling(app_state)
    assert app_state.slice(HrStateSlice).scaling_service is existing


async def test_skips_when_persistence_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENABLED_ENV, "true")
    app_state = _ready_app_state(backend=None)
    await wire_scaling(app_state)
    assert app_state.slice(HrStateSlice).scaling_service is None


async def test_skips_when_registry_or_tracker_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENABLED_ENV, "true")
    app_state = make_app_state(
        slices={
            HrStateSlice: {
                "agent_registry": None,
                "performance_tracker": None,
                "scaling_service": None,
            },
            ApprovalStateSlice: {"store": ApprovalStore()},
            PersistenceStateSlice: {"backend": object()},
        },
    )
    await wire_scaling(app_state)
    assert app_state.slice(HrStateSlice).scaling_service is None


async def test_wires_pipeline_and_hydrates_durable_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENABLED_ENV, "true")
    repo = mock_of[HiringRequestRepository](
        list_items=AsyncMock(return_value=()),
    )
    monkeypatch.setattr(
        "synthorg.persistence.state.persistence_of",
        lambda _state: SimpleNamespace(hiring_requests=repo),
    )
    # No org-memory backend in this unit harness; OffboardingService accepts
    # ``None`` and degrades to dropping the departing-agent snapshot.
    monkeypatch.setattr(
        "synthorg.memory.state.org_memory_backend_of",
        lambda _state: None,
    )
    app_state = _ready_app_state()

    await wire_scaling(app_state)

    service = app_state.slice(HrStateSlice).scaling_service
    assert isinstance(service, ScalingService)
    # The durable hiring requests were rehydrated through the attached repo;
    # an empty first page terminates pagination after exactly one read.
    repo.list_items.assert_awaited_once()


async def test_skips_when_approval_store_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Exercises the third arm of the collaborator guard: a wired registry +
    # tracker but no approval store must NOT wire the pipeline, else
    # auto-hire / auto-prune decisions would execute with no human-approval gate.
    monkeypatch.setenv(_ENABLED_ENV, "true")
    app_state = make_app_state(
        slices={
            HrStateSlice: {
                "agent_registry": AgentRegistryService(),
                "performance_tracker": PerformanceTracker(),
                "scaling_service": None,
            },
            ApprovalStateSlice: {"store": None},
            PersistenceStateSlice: {"backend": object()},
        },
    )
    await wire_scaling(app_state)
    assert app_state.slice(HrStateSlice).scaling_service is None


async def test_wire_failure_leaves_service_unwired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A non-critical failure inside _wire must degrade to "service unwired"
    # (best-effort), never crash the API startup sequence.
    monkeypatch.setenv(_ENABLED_ENV, "true")

    async def _boom(*_args: object, **_kwargs: object) -> None:
        msg = "wire boom"
        raise ValueError(msg)

    monkeypatch.setattr("synthorg.api.lifecycle_helpers.scaling_wiring._wire", _boom)
    app_state = _ready_app_state()
    await wire_scaling(app_state)
    assert app_state.slice(HrStateSlice).scaling_service is None


async def test_memory_error_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The broad except must re-raise criticals (reraise_critical) rather than
    # swallow a MemoryError and let the process limp on in a corrupted state.
    monkeypatch.setenv(_ENABLED_ENV, "true")

    async def _oom(*_args: object, **_kwargs: object) -> None:
        msg = "oom"
        raise MemoryError(msg)

    monkeypatch.setattr("synthorg.api.lifecycle_helpers.scaling_wiring._wire", _oom)
    app_state = _ready_app_state()
    with pytest.raises(MemoryError):
        await wire_scaling(app_state)
