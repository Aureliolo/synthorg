"""Tests for the model-refresh orchestration service."""

from unittest.mock import AsyncMock

import pytest

from synthorg.config.agent_schema import AgentConfig
from synthorg.config.model_metadata import ModelMetadata
from synthorg.config.schema import ProviderConfig, ProviderModelConfig
from synthorg.persistence.upgrade_recommendation_protocol import (
    UpgradeRecommendationRepository,
)
from synthorg.providers.enums import RecommendationStatus
from synthorg.providers.management.live_discovery_probe import (
    LiveCatalogReport,
    LiveDiscoveryProbe,
)
from synthorg.providers.management.model_refresh_service import ModelRefreshService
from synthorg.providers.management.refresh_config import RefreshMode
from synthorg.providers.management.service import ProviderManagementService
from synthorg.providers.management.upgrade_models import (
    StoredUpgradeRecommendation,
    UpgradeRecommendation,
)
from synthorg.providers.management.upgrade_recommender import UpgradeRecommender
from synthorg.settings.resolver import ConfigResolver
from tests._shared import FakeClock, mock_of

pytestmark = pytest.mark.unit


def _model(model_id: str, *, generation: float) -> ProviderModelConfig:
    return ProviderModelConfig(
        id=model_id,
        metadata=ModelMetadata(family="fam", generation=generation),
    )


_OLD = _model("old", generation=1.0)
_NEW = _model("new", generation=2.0)
_PROVIDER = ProviderConfig(
    connection_name="conn-test", base_url="http://localhost:11434", models=(_OLD, _NEW)
)


def _build_service(
    *,
    repo: UpgradeRecommendationRepository,
    agents: tuple[AgentConfig, ...] = (),
    probe: LiveDiscoveryProbe | None = None,
) -> ModelRefreshService:
    probe = probe or mock_of[LiveDiscoveryProbe](
        discover_report=AsyncMock(
            return_value=LiveCatalogReport(
                provider_name="example-provider",
                discovered=(_OLD, _NEW),
                checked_ids=("old", "new"),
            ),
        ),
    )
    mgmt = mock_of[ProviderManagementService](
        list_providers=AsyncMock(return_value={"example-provider": _PROVIDER}),
        add_model=AsyncMock(),
        flag_models_stale=AsyncMock(),
        get_provider=AsyncMock(return_value=_PROVIDER),
    )
    resolver = mock_of[ConfigResolver](get_agents=AsyncMock(return_value=agents))
    return ModelRefreshService(
        mgmt_service=mgmt,
        probe=probe,
        recommender=UpgradeRecommender(),
        repo=repo,
        config_resolver=resolver,
        clock=FakeClock(),
    )


class TestModelRefreshService:
    async def test_off_mode_returns_empty_report(self) -> None:
        repo = mock_of[UpgradeRecommendationRepository]()
        service = _build_service(repo=repo)
        report = await service.run_cycle(mode=RefreshMode.OFF)
        assert report.providers_scanned == 0
        assert report.recommended_count == 0

    async def test_reconcile_persists_recommendation_with_pinned_agents(self) -> None:
        repo = mock_of[UpgradeRecommendationRepository](
            query=AsyncMock(return_value=()),
            save=AsyncMock(),
        )
        agent = AgentConfig(
            name="writer",
            role="Writer",
            department="Engineering",
            model={"provider": "example-provider", "model_id": "old"},
        )
        service = _build_service(repo=repo, agents=(agent,))
        report = await service.run_cycle(mode=RefreshMode.RECONCILE_RECOMMEND)
        assert report.recommended_count == 1
        repo.save.assert_awaited_once()
        stored = repo.save.await_args.args[0]
        assert isinstance(stored, StoredUpgradeRecommendation)
        assert stored.status is RecommendationStatus.PENDING
        assert stored.agent_ids == ("writer",)

    async def test_dedup_skips_existing_pending(self) -> None:
        existing = StoredUpgradeRecommendation(
            recommendation=UpgradeRecommendation(
                provider_name="example-provider",
                current_model_id="old",
                recommended_model_id="new",
                family="fam",
                current_generation=1.0,
                recommended_generation=2.0,
                score=0.5,
                reason="x",
            ),
            created_at=FakeClock().now(),
        )
        repo = mock_of[UpgradeRecommendationRepository](
            query=AsyncMock(return_value=(existing,)),
            save=AsyncMock(),
        )
        service = _build_service(repo=repo)
        report = await service.run_cycle(mode=RefreshMode.RECONCILE_RECOMMEND)
        assert report.recommended_count == 0
        repo.save.assert_not_called()

    async def test_auto_apply_invokes_hook(self) -> None:
        repo = mock_of[UpgradeRecommendationRepository](
            query=AsyncMock(return_value=()),
            save=AsyncMock(),
        )
        service = _build_service(repo=repo)
        applied: list[StoredUpgradeRecommendation] = []

        async def _hook(stored: StoredUpgradeRecommendation) -> None:
            applied.append(stored)

        report = await service.run_cycle(
            mode=RefreshMode.RECONCILE_RECOMMEND,
            auto_apply=True,
            apply_recommendation=_hook,
        )
        assert report.auto_applied_count == 1
        assert len(applied) == 1

    async def test_provider_failure_isolated(self) -> None:
        repo = mock_of[UpgradeRecommendationRepository](
            query=AsyncMock(return_value=()),
            save=AsyncMock(),
        )
        # A probe that errors makes the per-provider reconcile raise; the
        # failing probe is injected at construction, not patched onto a
        # private attribute.
        failing_probe = mock_of[LiveDiscoveryProbe](
            discover_report=AsyncMock(side_effect=RuntimeError("boom")),
        )
        service = _build_service(repo=repo, probe=failing_probe)
        report = await service.run_cycle(mode=RefreshMode.RECONCILE_RECOMMEND)
        # The provider was attempted (counted) but its reconcile failed,
        # so no recommendation was produced.
        assert report.providers_scanned == 1
        assert report.recommended_count == 0
