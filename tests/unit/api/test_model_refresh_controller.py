"""Unit tests for ModelRefreshController handler logic.

Calls the controller methods directly with a fake ``State`` so the 503
(unwired) paths and the status read are covered without standing up a
full TestClient. The list/approve/reject happy paths are covered at the
service layer in ``test_upgrade_recommendation_service``.
"""

from unittest.mock import AsyncMock

import pytest
from litestar.datastructures import State

from synthorg.api.controllers.model_refresh import ModelRefreshController
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.providers.management.refresh_config import RefreshMode
from synthorg.providers.management.refresh_state import ModelRefreshStateSlice
from synthorg.settings.resolver import ConfigResolver
from tests._shared import make_app_state, mock_of, sid

pytestmark = pytest.mark.unit


def _controller() -> ModelRefreshController:
    return object.__new__(ModelRefreshController)


def _unwired_state() -> State:
    state = State()
    state.app_state = make_app_state(
        slices={
            ModelRefreshStateSlice: {
                "service": None,
                "scheduler": None,
                "recommendation_repo": None,
            },
        },
    )
    return state


async def test_list_recommendations_503_when_repo_unwired() -> None:
    with pytest.raises(ServiceUnavailableError):
        await ModelRefreshController.list_recommendations.fn(
            _controller(), state=_unwired_state(), status=None
        )


async def test_approve_503_when_repo_unwired() -> None:
    with pytest.raises(ServiceUnavailableError):
        await ModelRefreshController.approve_recommendation.fn(
            _controller(), rec_id=sid("rec"), state=_unwired_state()
        )


async def test_reject_503_when_repo_unwired() -> None:
    with pytest.raises(ServiceUnavailableError):
        await ModelRefreshController.reject_recommendation.fn(
            _controller(), rec_id=sid("rec"), state=_unwired_state()
        )


async def test_trigger_refresh_503_when_service_unwired() -> None:
    with pytest.raises(ServiceUnavailableError):
        await ModelRefreshController.trigger_refresh.fn(
            _controller(), state=_unwired_state()
        )


async def test_get_status_reports_resolved_config() -> None:
    resolver: ConfigResolver = mock_of[ConfigResolver](
        get_str=AsyncMock(return_value="manual_only"),
        get_float=AsyncMock(return_value=3600.0),
        get_bool=AsyncMock(return_value=True),
    )
    state = State()
    state.app_state = make_app_state(
        config_resolver=resolver,
        slices={
            ModelRefreshStateSlice: {
                "service": None,
                "scheduler": None,
                "recommendation_repo": None,
            },
        },
    )
    result = await ModelRefreshController.get_status.fn(_controller(), state=state)
    assert result.data.mode is RefreshMode.MANUAL_ONLY
    assert result.data.interval_seconds == 3600.0
    assert result.data.auto_apply_within_family is True
