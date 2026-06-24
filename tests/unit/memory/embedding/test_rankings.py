"""Tests for LMEB ranking data integrity."""

import pytest
from pydantic import ValidationError

from synthorg.memory.embedding.rankings import (
    LMEB_RANKINGS,
    DeploymentTier,
    EmbeddingModelRanking,
)


@pytest.mark.unit
class TestDeploymentTier:
    def test_values(self) -> None:
        assert DeploymentTier.GPU_FULL.value == "gpu_full"
        assert DeploymentTier.GPU_CONSUMER.value == "gpu_consumer"
        assert DeploymentTier.CPU.value == "cpu"

    def test_exhaustive(self) -> None:
        assert len(DeploymentTier) == 3


@pytest.mark.unit
class TestEmbeddingModelRanking:
    def test_frozen(self) -> None:
        ranking = EmbeddingModelRanking(
            model_id="test-model",
            params_billions=1.0,
            tier=DeploymentTier.CPU,
            episodic=50.0,
            procedural=50.0,
            dialogue=50.0,
            semantic=50.0,
            overall=50.0,
            use_instructions=True,
            output_dims=768,
        )
        with pytest.raises(ValidationError):
            ranking.model_id = "changed"  # type: ignore[misc]

    def test_rejects_blank_model_id(self) -> None:
        with pytest.raises(ValidationError):
            EmbeddingModelRanking(
                model_id="   ",
                params_billions=1.0,
                tier=DeploymentTier.CPU,
                episodic=50.0,
                procedural=50.0,
                dialogue=50.0,
                semantic=50.0,
                overall=50.0,
                use_instructions=True,
                output_dims=768,
            )

    def test_rejects_zero_dims(self) -> None:
        with pytest.raises(ValidationError):
            EmbeddingModelRanking(
                model_id="test-model",
                params_billions=1.0,
                tier=DeploymentTier.CPU,
                episodic=50.0,
                procedural=50.0,
                dialogue=50.0,
                semantic=50.0,
                overall=50.0,
                use_instructions=True,
                output_dims=0,
            )


@pytest.mark.unit
class TestLMEBRankings:
    def test_not_empty(self) -> None:
        assert len(LMEB_RANKINGS) >= 3

    def test_all_are_ranking_instances(self) -> None:
        for ranking in LMEB_RANKINGS:
            assert isinstance(ranking, EmbeddingModelRanking)

    def test_no_duplicate_model_ids(self) -> None:
        ids = [r.model_id for r in LMEB_RANKINGS]
        assert len(ids) == len(set(ids))

    def test_scores_in_valid_range(self) -> None:
        """NDCG@10 scores must be between 0 and 100."""
        for ranking in LMEB_RANKINGS:
            for field in ("episodic", "procedural", "dialogue", "semantic", "overall"):
                score = getattr(ranking, field)
                assert 0.0 <= score <= 100.0, f"{ranking.model_id}.{field} = {score}"

    def test_tiers_consistent_with_params(self) -> None:
        """GPU_FULL >= 7B, GPU_CONSUMER 1-7B, CPU < 1B."""
        for ranking in LMEB_RANKINGS:
            # params_billions is float | None on the shared model (non-LMEB
            # entries omit it); every LMEB ranking carries it.
            assert ranking.params_billions is not None
            if ranking.tier == DeploymentTier.GPU_FULL:
                assert ranking.params_billions >= 7.0, (
                    f"{ranking.model_id}: {ranking.params_billions}B "
                    f"too small for GPU_FULL"
                )
            elif ranking.tier == DeploymentTier.GPU_CONSUMER:
                assert 1.0 <= ranking.params_billions < 7.0, (
                    f"{ranking.model_id}: {ranking.params_billions}B "
                    f"out of range for GPU_CONSUMER"
                )
            elif ranking.tier == DeploymentTier.CPU:
                assert ranking.params_billions < 1.0, (
                    f"{ranking.model_id}: {ranking.params_billions}B too large for CPU"
                )

    def test_positive_dims(self) -> None:
        for ranking in LMEB_RANKINGS:
            assert ranking.output_dims >= 1

    def test_sorted_by_overall_descending(self) -> None:
        """Rankings should be sorted by overall score descending."""
        # overall is float | None on the shared model; every LMEB ranking sets
        # it, so the filter keeps them all (asserted) and narrows for sorted().
        scores = [r.overall for r in LMEB_RANKINGS if r.overall is not None]
        assert len(scores) == len(LMEB_RANKINGS)
        assert scores == sorted(scores, reverse=True)

    def test_all_three_tiers_represented(self) -> None:
        tiers = {r.tier for r in LMEB_RANKINGS}
        assert DeploymentTier.GPU_FULL in tiers
        assert DeploymentTier.GPU_CONSUMER in tiers
        assert DeploymentTier.CPU in tiers
