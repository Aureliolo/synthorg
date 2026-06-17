"""Unit tests for ``wire_model_refresh`` startup wiring.

Covers the off-safe default (mode ``off`` wires nothing), idempotency for
re-entered lifespans, and the dependency-absent skip (no management /
persistence). The cadence start-before-publish + rollback path is covered
by the scheduler's own lifecycle tests.
"""

from unittest.mock import AsyncMock

import pytest

from synthorg.api.lifecycle_helpers.model_refresh_wiring import wire_model_refresh
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.providers.management.model_refresh_service import ModelRefreshService
from synthorg.providers.management.refresh_state import ModelRefreshStateSlice
from synthorg.providers.state import ProvidersStateSlice
from synthorg.settings.resolver import ConfigResolver
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def _resolver(mode: str) -> ConfigResolver:
    resolver: ConfigResolver = mock_of[ConfigResolver](
        get_str=AsyncMock(return_value=mode),
        get_float=AsyncMock(return_value=86400.0),
        get_bool=AsyncMock(return_value=False),
    )
    return resolver


async def test_off_mode_wires_nothing() -> None:
    app_state = make_app_state(
        config_resolver=_resolver("off"),
        slices={
            ModelRefreshStateSlice: {
                "service": None,
                "scheduler": None,
                "recommendation_repo": None,
            },
        },
    )
    await wire_model_refresh(app_state)
    assert app_state.slice(ModelRefreshStateSlice).service is None


async def test_already_wired_is_idempotent() -> None:
    existing = mock_of[ModelRefreshService]()
    app_state = make_app_state(
        config_resolver=_resolver("reconcile_recommend"),
        slices={
            ModelRefreshStateSlice: {
                "service": existing,
                "scheduler": None,
                "recommendation_repo": None,
            },
        },
    )
    await wire_model_refresh(app_state)
    assert app_state.slice(ModelRefreshStateSlice).service is existing


async def test_skips_when_management_or_persistence_absent() -> None:
    app_state = make_app_state(
        config_resolver=_resolver("detect_only"),
        slices={
            ModelRefreshStateSlice: {
                "service": None,
                "scheduler": None,
                "recommendation_repo": None,
            },
            ProvidersStateSlice: {"management": None},
            PersistenceStateSlice: {"backend": None},
        },
    )
    await wire_model_refresh(app_state)
    assert app_state.slice(ModelRefreshStateSlice).service is None
