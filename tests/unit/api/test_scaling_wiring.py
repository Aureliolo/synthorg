"""Unit tests for ``wire_scaling`` startup wiring.

The service is ghost-wired: always constructed when its collaborators exist,
regardless of ``hr.scaling_enabled`` (enforced live at the evaluate
entrypoint). Covers that ghost-wire, idempotency for a re-entered lifespan,
the persistence- and collaborator-absent skips, the happy path that hydrates
the durable hiring requests, and the best-effort failure handling.
"""

from types import SimpleNamespace
from typing import cast
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
from synthorg.settings.resolver import ConfigResolver
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def _ready_app_state(
    *,
    backend: object | None = object(),
    config_resolver: ConfigResolver | None = None,
) -> AppState:
    """App state with registry + tracker + approval store + persistence."""
    return make_app_state(
        config_resolver=config_resolver,
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


async def test_constructs_regardless_of_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ghost-wire: the service is built even with ``hr.scaling_enabled`` off.

    The boot switch no longer gates wiring; the master switch is enforced live
    at the evaluate endpoint, so the service is always constructed when its
    collaborators exist.
    """
    repo = mock_of[HiringRequestRepository](list_items=AsyncMock(return_value=()))
    monkeypatch.setattr(
        "synthorg.persistence.state.persistence_of",
        lambda _state: SimpleNamespace(hiring_requests=repo),
    )
    monkeypatch.setattr(
        "synthorg.memory.state.org_memory_backend_of",
        lambda _state: None,
    )
    # Make the disabled case explicit: stub hr.scaling_enabled=False and prove
    # wiring never consults it, so a regression that re-introduces a boot-time
    # gate read fails here rather than silently passing on the default harness.
    get_bool = AsyncMock(return_value=False)
    app_state = _ready_app_state(
        config_resolver=cast(
            "ConfigResolver", mock_of[ConfigResolver](get_bool=get_bool)
        ),
    )
    await wire_scaling(app_state)
    assert isinstance(app_state.slice(HrStateSlice).scaling_service, ScalingService)
    get_bool.assert_not_awaited()


async def test_already_wired_is_idempotent() -> None:
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


async def test_skips_when_persistence_absent() -> None:
    app_state = _ready_app_state(backend=None)
    await wire_scaling(app_state)
    assert app_state.slice(HrStateSlice).scaling_service is None


@pytest.mark.parametrize(
    ("registry", "tracker"),
    [
        (None, PerformanceTracker()),
        (AgentRegistryService(), None),
        (None, None),
    ],
)
async def test_skips_when_registry_or_tracker_absent(
    registry: AgentRegistryService | None,
    tracker: PerformanceTracker | None,
) -> None:
    # Cover each missing collaborator independently: with both absent at once a
    # guard that flipped from ``or`` to ``and`` (wire only when BOTH are absent)
    # would still pass. The single-absent cases catch that regression.
    app_state = make_app_state(
        slices={
            HrStateSlice: {
                "agent_registry": registry,
                "performance_tracker": tracker,
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


async def test_skips_when_approval_store_absent() -> None:
    # Exercises the third arm of the collaborator guard: a wired registry +
    # tracker but no approval store must NOT wire the pipeline, else
    # auto-hire / auto-prune decisions would execute with no human-approval gate.
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
    async def _oom(*_args: object, **_kwargs: object) -> None:
        msg = "oom"
        raise MemoryError(msg)

    monkeypatch.setattr("synthorg.api.lifecycle_helpers.scaling_wiring._wire", _oom)
    app_state = _ready_app_state()
    with pytest.raises(MemoryError):
        await wire_scaling(app_state)
