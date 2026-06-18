"""Tests for the in-family upgrade recommender."""

from datetime import UTC, datetime

import pytest

from synthorg.config.model_metadata import ModelMetadata
from synthorg.config.model_staleness import ModelStaleness
from synthorg.config.schema import ProviderConfig, ProviderModelConfig
from synthorg.providers.management.upgrade_recommender import UpgradeRecommender

pytestmark = pytest.mark.unit


def _model(  # noqa: PLR0913 -- model dimensions are intrinsic to the fixture
    model_id: str,
    *,
    family: str | None = "example-large",
    generation: float | None = 1.0,
    tools: bool = False,
    vision: bool = False,
    reasoning: bool = False,
    max_context: int = 200_000,
    stale: bool = False,
) -> ProviderModelConfig:
    return ProviderModelConfig(
        id=model_id,
        max_context=max_context,
        metadata=ModelMetadata(
            family=family,
            generation=generation,
            supports_tools=tools,
            supports_vision=vision,
            supports_reasoning=reasoning,
        ),
        stale=(
            ModelStaleness(
                reason="deprecated", flagged_at=datetime(2026, 6, 1, tzinfo=UTC)
            )
            if stale
            else None
        ),
    )


def _provider(*models: ProviderModelConfig) -> dict[str, ProviderConfig]:
    return {"example-provider": ProviderConfig(models=models)}


class TestUpgradeRecommender:
    def test_recommends_newer_in_family(self) -> None:
        providers = _provider(
            _model("old", generation=1.0),
            _model("new", generation=2.0),
        )
        analysis = UpgradeRecommender().recommend(providers)
        assert analysis.recommendation_count == 1
        rec = analysis.recommendations[0]
        assert rec.current_model_id == "old"
        assert rec.recommended_model_id == "new"
        assert rec.recommended_generation == 2.0
        assert 0.0 <= rec.score <= 1.0

    def test_only_recommends_against_newest(self) -> None:
        providers = _provider(
            _model("v1", generation=1.0),
            _model("v2", generation=2.0),
            _model("v3", generation=3.0),
        )
        analysis = UpgradeRecommender().recommend(providers)
        assert {r.current_model_id for r in analysis.recommendations} == {"v1", "v2"}
        assert all(r.recommended_model_id == "v3" for r in analysis.recommendations)

    def test_skips_unparseable_family_or_generation(self) -> None:
        providers = _provider(
            _model("a", family=None, generation=1.0),
            _model("b", family="x", generation=None),
        )
        assert UpgradeRecommender().recommend(providers).recommendation_count == 0

    def test_skips_stale_models(self) -> None:
        providers = _provider(
            _model("old", generation=1.0, stale=True),
            _model("new", generation=2.0),
        )
        assert UpgradeRecommender().recommend(providers).recommendation_count == 0

    def test_capability_regression_guard(self) -> None:
        # Current supports tools; newest does not -> not a safe upgrade.
        providers = _provider(
            _model("old", generation=1.0, tools=True),
            _model("new", generation=2.0, tools=False),
        )
        assert UpgradeRecommender().recommend(providers).recommendation_count == 0

    def test_capability_gain_is_recommended(self) -> None:
        providers = _provider(
            _model("old", generation=1.0, tools=True),
            _model("new", generation=2.0, tools=True, vision=True),
        )
        assert UpgradeRecommender().recommend(providers).recommendation_count == 1

    def test_separate_families_isolated(self) -> None:
        providers = _provider(
            _model("a1", family="alpha", generation=1.0),
            _model("a2", family="alpha", generation=2.0),
            _model("b1", family="beta", generation=5.0),
        )
        analysis = UpgradeRecommender().recommend(providers)
        assert analysis.recommendation_count == 1
        assert analysis.recommendations[0].family == "alpha"

    def test_single_model_family_no_recommendation(self) -> None:
        providers = _provider(_model("only", generation=1.0))
        assert UpgradeRecommender().recommend(providers).recommendation_count == 0
