"""Tests for embedder config resolution."""

import pytest

from synthorg.core.vector_limits import STORAGE_MAX_DIMENSIONS
from synthorg.memory.config import CompanyMemoryConfig, EmbedderOverrideConfig
from synthorg.memory.embedding.rankings import LMEB_RANKINGS, DeploymentTier
from synthorg.memory.embedding.resolve import resolve_embedder_config
from synthorg.memory.errors import MemoryConfigError


@pytest.mark.unit
class TestResolveEmbedderConfig:
    def test_settings_override_wins(self) -> None:
        """Settings override takes highest priority."""
        override = EmbedderOverrideConfig(
            provider="override-provider",
            model="override-model",
            dims=512,
        )
        config = CompanyMemoryConfig()
        result = resolve_embedder_config(
            config,
            available_models=(LMEB_RANKINGS[0].model_id,),
            settings_override=override,
        )
        assert result.provider == "override-provider"
        assert result.model == "override-model"
        assert result.dims == 512
        # Truncation is only sanctioned where the operator pinned the width;
        # an auto-selected one that disagrees with the model is a fault.
        assert result.dims_explicit is True

    def test_yaml_config_override_second_priority(self) -> None:
        """YAML config embedder override wins over auto-select."""
        yaml_override = EmbedderOverrideConfig(
            provider="yaml-provider",
            model="yaml-model",
            dims=768,
        )
        config = CompanyMemoryConfig(embedder=yaml_override)
        result = resolve_embedder_config(
            config,
            available_models=(LMEB_RANKINGS[0].model_id,),
        )
        assert result.provider == "yaml-provider"
        assert result.model == "yaml-model"
        assert result.dims == 768

    def test_auto_select_from_available_models(self) -> None:
        """Falls back to auto-selection from available models."""
        top = LMEB_RANKINGS[0]
        config = CompanyMemoryConfig()
        result = resolve_embedder_config(
            config,
            available_models=(top.model_id,),
            provider_preset_name="ollama",
        )
        assert result.model == top.model_id
        assert result.dims == top.output_dims
        assert result.dims_explicit is False

    def test_a_yaml_override_alone_marks_the_width_explicit(self) -> None:
        """Either override in the chain counts, not only the settings one."""
        yaml_override = EmbedderOverrideConfig(
            provider="yaml-provider",
            model="yaml-model",
            dims=768,
        )
        config = CompanyMemoryConfig(embedder=yaml_override)
        result = resolve_embedder_config(
            config,
            available_models=(LMEB_RANKINGS[0].model_id,),
        )

        assert result.dims == 768
        assert result.dims_explicit is True

    def test_a_width_beyond_the_storage_ceiling_is_refused(self) -> None:
        """The registry caps an override; auto-selection never passes through it."""
        override = EmbedderOverrideConfig(
            provider="wide-provider",
            model="wide-model",
            dims=STORAGE_MAX_DIMENSIONS + 1,
        )
        config = CompanyMemoryConfig()
        with pytest.raises(MemoryConfigError, match="ceiling"):
            resolve_embedder_config(
                config,
                available_models=(LMEB_RANKINGS[0].model_id,),
                settings_override=override,
            )

    def test_auto_select_with_tier(self) -> None:
        """Tier inference affects auto-selection."""
        cpu_model = next(r for r in LMEB_RANKINGS if r.tier == DeploymentTier.CPU)
        config = CompanyMemoryConfig()
        result = resolve_embedder_config(
            config,
            available_models=(cpu_model.model_id,),
            provider_preset_name="ollama",
            has_gpu=False,
        )
        assert result.model == cpu_model.model_id
        assert result.dims == cpu_model.output_dims

    def test_no_available_models_raises(self) -> None:
        """Raises MemoryConfigError when no models can be resolved."""
        config = CompanyMemoryConfig()
        with pytest.raises(MemoryConfigError, match="resolve"):
            resolve_embedder_config(config)

    def test_no_lmeb_match_raises(self) -> None:
        """Raises when available models don't match any LMEB entry."""
        config = CompanyMemoryConfig()
        with pytest.raises(MemoryConfigError, match="resolve"):
            resolve_embedder_config(
                config,
                available_models=("unknown-model-xyz",),
            )

    def test_partial_settings_override_fills_from_auto(self) -> None:
        """Provider-only override uses auto-select for model/dims."""
        top = LMEB_RANKINGS[0]
        override = EmbedderOverrideConfig(provider="custom-provider")
        config = CompanyMemoryConfig()
        result = resolve_embedder_config(
            config,
            available_models=(top.model_id,),
            settings_override=override,
        )
        assert result.provider == "custom-provider"
        assert result.model == top.model_id
        assert result.dims == top.output_dims

    def test_partial_yaml_override_fills_from_auto(self) -> None:
        """YAML provider-only override fills model/dims from auto."""
        top = LMEB_RANKINGS[0]
        yaml_override = EmbedderOverrideConfig(provider="yaml-prov")
        config = CompanyMemoryConfig(embedder=yaml_override)
        result = resolve_embedder_config(
            config,
            available_models=(top.model_id,),
        )
        assert result.provider == "yaml-prov"
        assert result.model == top.model_id

    def test_settings_override_beats_yaml_override(self) -> None:
        """When both settings and YAML override exist, settings wins."""
        yaml_override = EmbedderOverrideConfig(
            provider="yaml-prov",
            model="yaml-model",
            dims=768,
        )
        settings_override = EmbedderOverrideConfig(
            provider="settings-prov",
            model="settings-model",
            dims=512,
        )
        config = CompanyMemoryConfig(embedder=yaml_override)
        result = resolve_embedder_config(
            config,
            settings_override=settings_override,
        )
        assert result.provider == "settings-prov"
        assert result.model == "settings-model"
        assert result.dims == 512

    def test_default_provider_from_ranking(self) -> None:
        """When no provider override, provider defaults to model_id."""
        top = LMEB_RANKINGS[0]
        config = CompanyMemoryConfig()
        result = resolve_embedder_config(
            config,
            available_models=(top.model_id,),
        )
        assert result.provider == top.model_id

    def test_tier_filtered_miss_falls_back_to_all(self) -> None:
        """When no tier match, falls back to all-tier selection."""
        cpu_models = [r for r in LMEB_RANKINGS if r.tier == DeploymentTier.CPU]
        gpu_models = [r for r in LMEB_RANKINGS if r.tier == DeploymentTier.GPU_FULL]
        if not cpu_models or not gpu_models:
            pytest.skip("Need both CPU and GPU_FULL models in rankings")
        # Offer only a GPU model but infer CPU tier -- tier filter
        # misses, all-tier fallback should find the GPU model.
        gpu_model = gpu_models[0]
        config = CompanyMemoryConfig()
        result = resolve_embedder_config(
            config,
            available_models=(gpu_model.model_id,),
            provider_preset_name="ollama",
            has_gpu=False,
        )
        assert result.model == gpu_model.model_id
