"""The scaling evaluate endpoint is live-gated on ``hr.scaling_enabled``."""

from typing import cast
from unittest.mock import AsyncMock

import pytest
from litestar.datastructures import State

from synthorg.api.controllers.scaling import ScalingController
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.hr.state import HrStateSlice
from synthorg.settings.resolver import ConfigResolver
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def _state(*, enabled: bool) -> tuple[State, AsyncMock]:
    get_bool = AsyncMock(return_value=enabled)
    app_state = make_app_state(
        config_resolver=cast(
            "ConfigResolver",
            mock_of[ConfigResolver](get_bool=get_bool),
        ),
    )
    state = State()
    state.app_state = app_state
    return state, get_bool


def _controller() -> ScalingController:
    return ScalingController(owner=ScalingController)  # type: ignore[arg-type]


async def test_evaluate_503s_when_scaling_disabled() -> None:
    """A disabled switch 503s the evaluate endpoint before touching the service."""
    ctrl = _controller()
    state, get_bool = _state(enabled=False)
    with pytest.raises(ServiceUnavailableError):
        await ctrl.trigger_evaluation.fn(ctrl, state=state)
    # Pin the controller/resolver contract: a resolver stub that answered any
    # key would let the gate read the wrong flag and still pass this test.
    get_bool.assert_awaited_once_with("hr", "scaling_enabled")


async def test_evaluate_passes_gate_when_enabled() -> None:
    """When enabled, the gate passes and the missing-service path is reached."""
    ctrl = _controller()
    state, get_bool = _state(enabled=True)
    # Service is unwired in this harness, so past the gate the endpoint returns
    # the configured "not configured" response rather than raising the 503 gate.
    result = await ctrl.trigger_evaluation.fn(ctrl, state=state)
    assert state.app_state.slice(HrStateSlice).scaling_service is None
    assert result.error == "Scaling service not configured"
    get_bool.assert_awaited_once_with("hr", "scaling_enabled")
