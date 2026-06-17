"""Unit tests for ``wire_model_refresh`` startup wiring.

Covers the off-safe default (mode ``off`` wires nothing), idempotency for
re-entered lifespans, the dependency-absent skip (no management /
persistence), the cadence start-before-publish happy path, and the
rollback that leaves the slice unpublished when ``scheduler.start()``
fails.
"""

from unittest.mock import AsyncMock

import pytest

import synthorg.api.lifecycle_helpers.model_refresh_wiring as wiring_module
from synthorg.api.api_core_state import ApiCoreStateSlice
from synthorg.api.lifecycle_helpers.model_refresh_wiring import wire_model_refresh
from synthorg.api.services.org_mutations import OrgMutationService
from synthorg.api.state import AppState
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.persistence.upgrade_recommendation_protocol import (
    UpgradeRecommendationRepository,
)
from synthorg.providers.management.model_refresh_service import ModelRefreshService
from synthorg.providers.management.refresh_scheduler import ModelRefreshScheduler
from synthorg.providers.management.refresh_state import ModelRefreshStateSlice
from synthorg.providers.management.service import ProviderManagementService
from synthorg.providers.state import ProvidersStateSlice
from synthorg.settings.resolver import ConfigResolver
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def _resolver(mode: str) -> ConfigResolver:
    resolver: ConfigResolver = mock_of[ConfigResolver](
        get_str=AsyncMock(return_value=mode),
        get_float=AsyncMock(return_value=86400.0),
        get_bool=AsyncMock(return_value=False),
        get_agents=AsyncMock(return_value=()),
    )
    return resolver


def _cadence_app_state() -> AppState:
    """App state with management + persistence + org mutations all wired."""
    management = mock_of[ProviderManagementService](
        list_providers=AsyncMock(return_value={}),
    )
    return make_app_state(
        config_resolver=_resolver("detect_only"),
        slices={
            ModelRefreshStateSlice: {
                "service": None,
                "scheduler": None,
                "recommendation_repo": None,
            },
            ProvidersStateSlice: {"management": management},
            PersistenceStateSlice: {"backend": object()},
            ApiCoreStateSlice: {
                "org_mutation_service": mock_of[OrgMutationService](),
            },
        },
    )


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


async def test_cadence_mode_publishes_service_and_scheduler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = mock_of[UpgradeRecommendationRepository](query=AsyncMock(return_value=()))
    monkeypatch.setattr(
        wiring_module, "build_upgrade_recommendation_repo", lambda _backend: repo
    )
    app_state = _cadence_app_state()
    await wire_model_refresh(app_state)
    published = app_state.slice(ModelRefreshStateSlice)
    assert published.service is not None
    assert published.recommendation_repo is repo
    scheduler = published.scheduler
    assert scheduler is not None
    # The scheduler is a live background task; stop it so the test loop
    # does not leak it.
    await scheduler.stop()


async def test_cadence_rollback_when_scheduler_start_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = mock_of[UpgradeRecommendationRepository](query=AsyncMock(return_value=()))
    monkeypatch.setattr(
        wiring_module, "build_upgrade_recommendation_repo", lambda _backend: repo
    )

    async def _failing_start(_self: ModelRefreshScheduler) -> None:
        msg = "start boom"
        raise RuntimeError(msg)

    monkeypatch.setattr(ModelRefreshScheduler, "start", _failing_start)
    app_state = _cadence_app_state()
    await wire_model_refresh(app_state)
    # Start failed: the slice is never published, so the controllers 503
    # rather than presenting a half-wired subsystem.
    assert app_state.slice(ModelRefreshStateSlice).service is None
    assert app_state.slice(ModelRefreshStateSlice).scheduler is None
