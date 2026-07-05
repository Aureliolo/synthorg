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
    embeddings: bool = False,
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
            supports_embeddings=embeddings,
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
    return {
        "example-provider": ProviderConfig(connection_name="conn-test", models=models)
    }


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

    def test_embedding_model_not_upgraded_to_newer_chat_model(self) -> None:
        # An embedding model has no tool/vision/reasoning caps to "lose", so
        # the capability-regression guard alone would let a newer-gen chat
        # model of the same family be recommended as its upgrade. An embedding
        # model and a chat model are different classes; there must be no
        # cross-class recommendation.
        providers = _provider(
            _model("example-embed", family="example", generation=1.0, embeddings=True),
            _model("example-chat", family="example", generation=2.0, tools=True),
        )
        assert UpgradeRecommender().recommend(providers).recommendation_count == 0

    def test_chat_upgrades_ignore_a_newer_embedding_sibling(self) -> None:
        # The newer-generation model in the family is an embedding model; a
        # chat upgrade must route to the newest *chat* model, never the
        # embedding one.
        providers = _provider(
            _model("chat-v1", family="example", generation=1.0, tools=True),
            _model("chat-v2", family="example", generation=2.0, tools=True),
            _model("example-embed", family="example", generation=3.0, embeddings=True),
        )
        analysis = UpgradeRecommender().recommend(providers)
        assert analysis.recommendation_count == 1
        rec = analysis.recommendations[0]
        assert rec.current_model_id == "chat-v1"
        assert rec.recommended_model_id == "chat-v2"

    def test_prefers_stronger_variant_over_alphabetically_first(self) -> None:
        # Two same-generation candidates: a smaller/cheaper 'flash' (sorts
        # first alphabetically) and a larger/more-capable 'pro'. The
        # recommender must pick the stronger 'pro', not the first by id.
        providers = _provider(
            _model("model-v3", generation=3.0, tools=True, max_context=100_000),
            _model("model-v4-flash", generation=4.0, tools=True, max_context=100_000),
            _model(
                "model-v4-pro",
                generation=4.0,
                tools=True,
                vision=True,
                max_context=400_000,
            ),
        )
        analysis = UpgradeRecommender().recommend(providers)
        assert analysis.recommendation_count == 1
        assert analysis.recommendations[0].recommended_model_id == "model-v4-pro"

    def test_score_tie_broken_deterministically_by_id(self) -> None:
        # Two newest-gen candidates identical on every scored dimension: the
        # tiebreak must be the model id (max), never insertion order, so the
        # recommendation is stable across runs.
        providers = _provider(
            _model("model-v1", generation=1.0, tools=True, max_context=100_000),
            _model("model-v2-aaa", generation=2.0, tools=True, max_context=100_000),
            _model("model-v2-zzz", generation=2.0, tools=True, max_context=100_000),
        )
        analysis = UpgradeRecommender().recommend(providers)
        assert analysis.recommendation_count == 1
        assert analysis.recommendations[0].recommended_model_id == "model-v2-zzz"

    def test_regressing_newest_candidate_skipped_for_safe_sibling(self) -> None:
        # Two newest-generation siblings: the higher-context one drops the
        # current model's tool capability (a regression) while the other
        # keeps it. The regression filter is per-candidate, so the safe
        # sibling wins even though the regressing one would score higher.
        providers = _provider(
            _model("current", generation=1.0, tools=True, max_context=100_000),
            _model("new-drops-tools", generation=2.0, tools=False, max_context=900_000),
            _model("new-keeps-tools", generation=2.0, tools=True, max_context=100_000),
        )
        analysis = UpgradeRecommender().recommend(providers)
        assert analysis.recommendation_count == 1
        assert analysis.recommendations[0].recommended_model_id == "new-keeps-tools"
