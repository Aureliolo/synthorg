"""Tests for embedding model selector."""

import pytest

from synthorg.memory.embedding.rankings import (
    LMEB_RANKINGS,
    DeploymentTier,
)
from synthorg.memory.embedding.selector import (
    infer_deployment_tier,
    select_embedding_model,
)


@pytest.mark.unit
class TestSelectEmbeddingModel:
    def test_returns_highest_ranked_match(self) -> None:
        """When multiple LMEB models are available, pick the best."""
        top = LMEB_RANKINGS[0]
        second = LMEB_RANKINGS[1]
        available = (top.model_id, second.model_id)
        result = select_embedding_model(available)
        assert result is not None
        assert result.model_id == top.model_id

    def test_returns_none_when_no_match(self) -> None:
        result = select_embedding_model(
            ("unknown-model-xyz", "another-unknown"),
        )
        assert result is None

    def test_empty_available_returns_none(self) -> None:
        result = select_embedding_model(())
        assert result is None

    def test_respects_tier_filter(self) -> None:
        """When tier is specified, only models from that tier match."""
        cpu_models = [r for r in LMEB_RANKINGS if r.tier == DeploymentTier.CPU]
        assert len(cpu_models) >= 1
        cpu_model = cpu_models[0]
        # Offer both a GPU and CPU model -- tier filter should pick CPU
        gpu_model = next(
            (r for r in LMEB_RANKINGS if r.tier == DeploymentTier.GPU_FULL),
            None,
        )
        assert gpu_model is not None, "LMEB_RANKINGS must have a GPU_FULL entry"
        available = (gpu_model.model_id, cpu_model.model_id)
        result = select_embedding_model(
            available,
            deployment_tier=DeploymentTier.CPU,
        )
        assert result is not None
        # The CPU-tier ranking is the only candidate under the filter, so the
        # CPU model is chosen (selection carries the catalogue id, not tier).
        assert result.model_id == cpu_model.model_id

    def test_tier_miss_falls_back_to_all_tiers(self) -> None:
        """Tier is a preference: a tier miss falls back to all tiers.

        A CPU host whose only ranked model is GPU-tier must still get a
        selection rather than ending up with no embedder.
        """
        gpu_model = next(
            (r for r in LMEB_RANKINGS if r.tier == DeploymentTier.GPU_FULL),
            None,
        )
        assert gpu_model is not None, "LMEB_RANKINGS must have a GPU_FULL entry"
        result = select_embedding_model(
            (gpu_model.model_id,),
            deployment_tier=DeploymentTier.CPU,
        )
        assert result is not None
        assert result.model_id == gpu_model.model_id

    def test_substring_match(self) -> None:
        """Ollama model names may include version tags."""
        top = LMEB_RANKINGS[0]
        # Simulate Ollama-style name with :latest suffix
        available = (f"{top.model_id}:latest",)
        result = select_embedding_model(available)
        assert result is not None
        # Selection returns the operator's catalogue id (with the tag), while
        # the matched benchmark id is exposed separately.
        assert result.model_id == f"{top.model_id}:latest"
        assert result.ranking_model_id == top.model_id

    def test_case_insensitive_match(self) -> None:
        top = LMEB_RANKINGS[0]
        available = (top.model_id.upper(),)
        result = select_embedding_model(available)
        assert result is not None
        assert result.model_id == top.model_id.upper()
        assert result.ranking_model_id == top.model_id

    def test_no_tier_uses_all_rankings(self) -> None:
        """Without tier filter, all tiers are considered."""
        top = LMEB_RANKINGS[0]
        available = (top.model_id,)
        result = select_embedding_model(available)
        assert result is not None
        assert result.model_id == top.model_id

    def test_qwen3_embedding_8b_variant_matches(self) -> None:
        """A size variant (`:8b`) the benchmark list omits still resolves.

        LMEB lists only `Qwen3-Embedding-4B`; the family/MTEB entry covers the
        8B the operator actually pulled. Selection must return the catalogue id.
        """
        result = select_embedding_model(("qwen3-embedding:8b",))
        assert result is not None
        assert result.model_id == "qwen3-embedding:8b"
        assert "qwen3-embedding" in result.ranking_model_id.lower()
        # Must NOT inherit the 4B entry's 2560 dims: the family entry carries
        # the 8B native dimension.
        assert result.output_dims == 4096

    def test_ingest_dims_override_static_fallback(self) -> None:
        """Live-discovered dims win over the ranking's static dimension."""
        result = select_embedding_model(
            ("qwen3-embedding:8b",),
            dims_by_model={"qwen3-embedding:8b": 4096},
        )
        assert result is not None
        assert result.output_dims == 4096

    def test_curated_local_embedder_matches(self) -> None:
        """A self-curated local embedder (nomic) resolves with its dims."""
        result = select_embedding_model(("nomic-embed-text:latest",))
        assert result is not None
        assert result.model_id == "nomic-embed-text:latest"
        assert result.output_dims == 768


@pytest.mark.unit
class TestInferDeploymentTier:
    @pytest.mark.parametrize(
        "preset_name",
        ["ollama", "lm-studio", "vllm"],
    )
    def test_local_with_gpu(self, preset_name: str) -> None:
        result = infer_deployment_tier(preset_name, has_gpu=True)
        assert result == DeploymentTier.GPU_CONSUMER

    @pytest.mark.parametrize(
        "preset_name",
        ["ollama", "lm-studio", "vllm"],
    )
    def test_local_without_gpu(self, preset_name: str) -> None:
        result = infer_deployment_tier(preset_name, has_gpu=False)
        assert result == DeploymentTier.CPU

    @pytest.mark.parametrize(
        "preset_name",
        ["ollama", "lm-studio", "vllm"],
    )
    def test_local_gpu_unknown(self, preset_name: str) -> None:
        """Unknown GPU status defaults to GPU_CONSUMER for local."""
        result = infer_deployment_tier(preset_name, has_gpu=None)
        assert result == DeploymentTier.GPU_CONSUMER

    @pytest.mark.parametrize(
        "preset_name",
        ["example-cloud-provider", "some-api-service"],
    )
    def test_cloud_provider(self, preset_name: str) -> None:
        """Non-local providers assume full GPU resources."""
        result = infer_deployment_tier(preset_name)
        assert result == DeploymentTier.GPU_FULL

    def test_none_preset_defaults_gpu_consumer(self) -> None:
        result = infer_deployment_tier(None)
        assert result == DeploymentTier.GPU_CONSUMER

    def test_case_insensitive(self) -> None:
        result = infer_deployment_tier("Ollama", has_gpu=False)
        assert result == DeploymentTier.CPU
