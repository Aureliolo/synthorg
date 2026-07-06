"""Tests for the pluggable model-refresh strategies + factory."""

from unittest.mock import AsyncMock

import pytest

from synthorg.config.model_metadata import ModelMetadata
from synthorg.config.schema import ProviderConfig, ProviderModelConfig
from synthorg.providers.management.live_discovery_probe import (
    LiveCatalogReport,
    LiveDiscoveryProbe,
)
from synthorg.providers.management.refresh_config import RefreshMode
from synthorg.providers.management.refresh_strategy import (
    DetectOnlyStrategy,
    ReconcileRecommendStrategy,
    RefreshStrategy,
    build_refresh_strategy,
)
from synthorg.providers.management.service import ProviderManagementService
from synthorg.providers.management.upgrade_recommender import UpgradeRecommender
from tests._shared import FakeClock, mock_of

pytestmark = pytest.mark.unit


def _model(
    model_id: str, *, family: str = "fam", generation: float = 1.0
) -> ProviderModelConfig:
    return ProviderModelConfig(
        id=model_id,
        metadata=ModelMetadata(family=family, generation=generation),
    )


def _provider(*models: ProviderModelConfig) -> ProviderConfig:
    return ProviderConfig(
        connection_name="conn-test", base_url="http://localhost:11434", models=models
    )


class TestBuildRefreshStrategy:
    def test_off_and_manual_only_map_to_none(self) -> None:
        probe = mock_of[LiveDiscoveryProbe]()
        mgmt = mock_of[ProviderManagementService]()
        rec = UpgradeRecommender()
        for mode in (RefreshMode.OFF, RefreshMode.MANUAL_ONLY):
            assert (
                build_refresh_strategy(
                    mode, probe=probe, mgmt_service=mgmt, recommender=rec
                )
                is None
            )

    def test_detect_only_maps_to_detect_strategy(self) -> None:
        strategy = build_refresh_strategy(
            RefreshMode.DETECT_ONLY,
            probe=mock_of[LiveDiscoveryProbe](),
            mgmt_service=mock_of[ProviderManagementService](),
            recommender=UpgradeRecommender(),
        )
        assert isinstance(strategy, DetectOnlyStrategy)
        assert isinstance(strategy, RefreshStrategy)

    def test_reconcile_maps_to_reconcile_strategy(self) -> None:
        strategy = build_refresh_strategy(
            RefreshMode.RECONCILE_RECOMMEND,
            probe=mock_of[LiveDiscoveryProbe](),
            mgmt_service=mock_of[ProviderManagementService](),
            recommender=UpgradeRecommender(),
        )
        assert isinstance(strategy, ReconcileRecommendStrategy)


class TestDetectOnlyStrategy:
    async def test_flags_missing_without_adding_or_recommending(self) -> None:
        probe = mock_of[LiveDiscoveryProbe](
            discover_report=AsyncMock(
                return_value=LiveCatalogReport(
                    provider_name="local",
                    discovered=(_model("m1"),),
                    missing_ids=("gone",),
                    checked_ids=("m1", "gone"),
                ),
            ),
        )
        mgmt = mock_of[ProviderManagementService](
            flag_models_stale=AsyncMock(),
            add_model=AsyncMock(),
        )
        strategy = DetectOnlyStrategy(probe=probe, mgmt_service=mgmt, clock=FakeClock())
        outcome = await strategy.reconcile("local", _provider(_model("m1")))
        assert outcome.stale_ids == ("gone",)
        assert outcome.added_ids == ()
        assert outcome.recommendations == ()
        mgmt.flag_models_stale.assert_awaited_once()
        mgmt.add_model.assert_not_called()


class TestReconcileRecommendStrategy:
    async def test_adds_new_refreshes_metadata_and_recommends(self) -> None:
        # 'old' is persisted with a stale family; the live catalogue advertises
        # it with a fresh family (a parser improvement) plus a new model.
        old_stale = _model("old", family="stale-fam", generation=1.0)
        old_fresh = _model("old", family="fresh-fam", generation=1.0)
        new = _model("new", family="fresh-fam", generation=2.0)
        probe = mock_of[LiveDiscoveryProbe](
            discover_report=AsyncMock(
                return_value=LiveCatalogReport(
                    provider_name="local",
                    discovered=(old_fresh, new),
                    added_ids=("new",),
                    checked_ids=("old",),
                ),
            ),
        )
        # The refresh persists the merged set; the read-back reflects it.
        mgmt = mock_of[ProviderManagementService](
            update_provider=AsyncMock(),
            flag_models_stale=AsyncMock(),
            get_provider=AsyncMock(return_value=_provider(old_fresh, new)),
        )
        strategy = ReconcileRecommendStrategy(
            probe=probe,
            mgmt_service=mgmt,
            recommender=UpgradeRecommender(),
            clock=FakeClock(),
        )
        outcome = await strategy.reconcile("local", _provider(old_stale))
        assert outcome.added_ids == ("new",)
        assert outcome.recommendations_valid is True
        mgmt.update_provider.assert_awaited_once()
        persisted = {m.id: m for m in mgmt.update_provider.await_args.args[1].models}
        # Existing model's metadata is refreshed and the new one is added.
        assert persisted["old"].metadata.family == "fresh-fam"
        assert persisted["new"].metadata.family == "fresh-fam"
        # The refreshed provider yields an old->new in-family recommendation.
        assert len(outcome.recommendations) == 1
        assert outcome.recommendations[0].recommended_model_id == "new"

    async def test_skips_persist_when_catalogue_unchanged(self) -> None:
        model = _model("m", generation=1.0)
        probe = mock_of[LiveDiscoveryProbe](
            discover_report=AsyncMock(
                return_value=LiveCatalogReport(
                    provider_name="local",
                    discovered=(model,),
                    checked_ids=("m",),
                ),
            ),
        )
        mgmt = mock_of[ProviderManagementService](
            update_provider=AsyncMock(),
            flag_models_stale=AsyncMock(),
            get_provider=AsyncMock(return_value=_provider(model)),
        )
        strategy = ReconcileRecommendStrategy(
            probe=probe,
            mgmt_service=mgmt,
            recommender=UpgradeRecommender(),
            clock=FakeClock(),
        )
        outcome = await strategy.reconcile("local", _provider(model))
        assert outcome.added_ids == ()
        mgmt.update_provider.assert_not_called()

    async def test_recommend_failure_marks_recommendations_invalid(self) -> None:
        model = _model("m", generation=1.0)
        probe = mock_of[LiveDiscoveryProbe](
            discover_report=AsyncMock(
                return_value=LiveCatalogReport(
                    provider_name="local",
                    discovered=(model,),
                    checked_ids=("m",),
                ),
            ),
        )
        # The post-refresh re-read (used only by the recommend step) fails.
        mgmt = mock_of[ProviderManagementService](
            update_provider=AsyncMock(),
            flag_models_stale=AsyncMock(),
            get_provider=AsyncMock(side_effect=RuntimeError("boom")),
        )
        strategy = ReconcileRecommendStrategy(
            probe=probe,
            mgmt_service=mgmt,
            recommender=UpgradeRecommender(),
            clock=FakeClock(),
        )
        outcome = await strategy.reconcile("local", _provider(model))
        assert outcome.recommendations == ()
        assert outcome.recommendations_valid is False
