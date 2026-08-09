"""Unit tests for ``wire_tool_call_feedback`` startup wiring.

Covers idempotency for re-entered lifespans, the dependency-absent skip
(no management / persistence), and the happy path that publishes the
tracker and installs the global sink last.
"""

from collections.abc import Iterator
from unittest.mock import AsyncMock

import pytest

from synthorg.api.lifecycle_helpers.tool_call_feedback_wiring import (
    wire_tool_call_feedback,
)
from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.persistence.model_tool_call_signal_protocol import (
    ModelToolCallSignalRepository,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.providers.management.service import ProviderManagementService
from synthorg.providers.state import ProvidersStateSlice
from synthorg.providers.tool_call_feedback.sink import (
    get_tool_call_signal_sink,
    uninstall_tool_call_signal_sink,
)
from synthorg.providers.tool_call_feedback.state import ToolCallFeedbackStateSlice
from synthorg.providers.tool_call_feedback.tracker import ToolCallFeedbackTracker
from synthorg.settings.resolver import ConfigResolver
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_sink() -> Iterator[None]:
    uninstall_tool_call_signal_sink()
    yield
    uninstall_tool_call_signal_sink()


def _resolver() -> ConfigResolver:
    resolver: ConfigResolver = mock_of[ConfigResolver](
        get_bool=AsyncMock(return_value=True),
        get_int=AsyncMock(return_value=3),
    )
    return resolver


def _wired_app_state() -> AppState:
    management = mock_of[ProviderManagementService]()
    backend = mock_of[PersistenceBackend](
        model_tool_call_signals=mock_of[ModelToolCallSignalRepository](),
    )
    return make_app_state(
        config_resolver=_resolver(),
        slices={
            ToolCallFeedbackStateSlice: {"tracker": None},
            ProvidersStateSlice: {"management": management},
            PersistenceStateSlice: {"backend": backend},
        },
    )


async def test_already_wired_is_idempotent() -> None:
    existing = mock_of[ToolCallFeedbackTracker]()
    app_state = make_app_state(
        config_resolver=_resolver(),
        slices={ToolCallFeedbackStateSlice: {"tracker": existing}},
    )
    await wire_tool_call_feedback(app_state)
    assert app_state.slice(ToolCallFeedbackStateSlice).tracker is existing


async def test_declines_naming_absent_management() -> None:
    backend = mock_of[PersistenceBackend](
        model_tool_call_signals=mock_of[ModelToolCallSignalRepository](),
    )
    app_state = make_app_state(
        config_resolver=_resolver(),
        slices={
            ToolCallFeedbackStateSlice: {"tracker": None},
            ProvidersStateSlice: {"management": None},
            PersistenceStateSlice: {"backend": backend},
        },
    )
    with pytest.raises(SubsystemDeclinedError, match="no provider management service"):
        await wire_tool_call_feedback(app_state)
    assert app_state.slice(ToolCallFeedbackStateSlice).tracker is None
    assert get_tool_call_signal_sink() is None


async def test_declines_naming_absent_persistence() -> None:
    management = mock_of[ProviderManagementService]()
    app_state = make_app_state(
        config_resolver=_resolver(),
        slices={
            ToolCallFeedbackStateSlice: {"tracker": None},
            ProvidersStateSlice: {"management": management},
            PersistenceStateSlice: {"backend": None},
        },
    )
    with pytest.raises(SubsystemDeclinedError, match="no persistence backend"):
        await wire_tool_call_feedback(app_state)
    assert app_state.slice(ToolCallFeedbackStateSlice).tracker is None
    assert get_tool_call_signal_sink() is None


async def test_declines_naming_the_absent_resolver() -> None:
    app_state = make_app_state(
        slices={ToolCallFeedbackStateSlice: {"tracker": None}},
    )
    with pytest.raises(SubsystemDeclinedError, match="no settings resolver"):
        await wire_tool_call_feedback(app_state)
    assert app_state.slice(ToolCallFeedbackStateSlice).tracker is None


async def test_wires_tracker_and_installs_sink() -> None:
    app_state = _wired_app_state()
    await wire_tool_call_feedback(app_state)
    tracker = app_state.slice(ToolCallFeedbackStateSlice).tracker
    assert tracker is not None
    assert get_tool_call_signal_sink() is tracker
