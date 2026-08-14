"""Unit tests for ``wire_hiring`` startup wiring.

The hiring pipeline is its own subsystem because three callers need it (the
scaler, the approvals controller finishing an approved hire, and the staffing
sweep asking for one), so it is built once and published rather than reached
for through whoever happened to construct it.
"""

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.lifecycle_helpers.hiring_wiring import wire_hiring
from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.approval.state import ApprovalStateSlice
from synthorg.hr.hiring_service import HiringService
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.state import HrStateSlice
from synthorg.persistence.hiring_request_protocol import HiringRequestRepository
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.settings.resolver import ConfigResolver
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def _app_state(
    *,
    backend: object | None = object(),
    with_store: bool = True,
    with_resolver: bool = True,
    existing: HiringService | None = None,
) -> AppState:
    """App state with a registry, an approval store and persistence."""
    return make_app_state(
        config_resolver=(
            cast("ConfigResolver", mock_of[ConfigResolver]()) if with_resolver else None
        ),
        slices={
            HrStateSlice: {
                "agent_registry": AgentRegistryService(),
                "hiring_service": existing,
            },
            ApprovalStateSlice: {"store": ApprovalStore() if with_store else None},
            PersistenceStateSlice: {"backend": backend},
        },
    )


async def test_wires_and_hydrates_the_durable_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = mock_of[HiringRequestRepository](list_items=AsyncMock(return_value=()))
    monkeypatch.setattr(
        "synthorg.api.lifecycle_helpers.hiring_wiring.persistence_of",
        lambda _state: SimpleNamespace(hiring_requests=repo),
    )
    app_state = _app_state()

    await wire_hiring(app_state)

    assert isinstance(app_state.slice(HrStateSlice).hiring_service, HiringService)
    # An empty first page terminates pagination after exactly one read.
    repo.list_items.assert_awaited_once()


async def test_already_wired_is_idempotent() -> None:
    existing = HiringService(
        registry=AgentRegistryService(), approval_store=ApprovalStore()
    )
    app_state = _app_state(existing=existing)
    await wire_hiring(app_state)
    assert app_state.slice(HrStateSlice).hiring_service is existing


async def test_declines_naming_the_absent_approval_store() -> None:
    # Every hire is a gated decision, so a pipeline with no gate must not
    # come up at all.
    app_state = _app_state(with_store=False)
    with pytest.raises(SubsystemDeclinedError, match="no approval store"):
        await wire_hiring(app_state)
    assert app_state.slice(HrStateSlice).hiring_service is None


async def test_declines_naming_absent_persistence() -> None:
    app_state = _app_state(backend=None)
    with pytest.raises(SubsystemDeclinedError, match="no persistence backend"):
        await wire_hiring(app_state)
    assert app_state.slice(HrStateSlice).hiring_service is None


async def test_declines_naming_the_absent_settings_resolver() -> None:
    # Without a resolver the pipeline could only ever hire against a pair
    # nobody chose, so it waits for one rather than coming up half-armed.
    app_state = _app_state(with_resolver=False)
    with pytest.raises(SubsystemDeclinedError, match="no settings resolver"):
        await wire_hiring(app_state)
    assert app_state.slice(HrStateSlice).hiring_service is None


async def test_hydration_failure_still_publishes_the_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed rehydrate orphans in-flight requests; it must not go dormant."""
    repo = mock_of[HiringRequestRepository](
        list_items=AsyncMock(side_effect=TimeoutError("backend hung")),
    )
    monkeypatch.setattr(
        "synthorg.api.lifecycle_helpers.hiring_wiring.persistence_of",
        lambda _state: SimpleNamespace(hiring_requests=repo),
    )
    app_state = _app_state()

    await wire_hiring(app_state)

    assert isinstance(app_state.slice(HrStateSlice).hiring_service, HiringService)
