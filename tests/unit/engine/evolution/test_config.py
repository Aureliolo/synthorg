"""Tests for EvolutionConfig and sub-configs."""

import pytest

from synthorg.engine.evolution.config import (
    AdapterConfig,
    EvolutionConfig,
    GuardConfig,
    ProposerConfig,
    TriggerConfig,
)


class TestEvolutionConfigDefaults:
    """EvolutionConfig ships with safe defaults."""

    @pytest.mark.unit
    def test_defaults(self) -> None:
        config = EvolutionConfig()
        # ``enabled`` defaults to False (kill-switch convention; matches
        # the registered ``engine.evolution_enabled`` default).
        assert config.enabled is False
        assert config.triggers.types == ("batched", "inflection")
        assert config.proposer.type == "composite"
        assert config.adapters.identity is False
        assert config.adapters.strategy_selection is True
        assert config.adapters.prompt_template is True
        assert config.guards.review_gate is True
        assert config.guards.rollback is True
        assert config.guards.rate_limit is True
        assert config.guards.shadow_evaluation is None
        assert config.identity_store.type == "append_only"

    @pytest.mark.unit
    def test_frozen(self) -> None:
        config = EvolutionConfig()
        with pytest.raises(ValueError, match="frozen"):
            config.enabled = True  # type: ignore[misc]

    @pytest.mark.unit
    def test_env_override_enables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SYNTHORG_ENGINE_EVOLUTION_ENABLED=true flips the mirror to True."""
        monkeypatch.setenv("SYNTHORG_ENGINE_EVOLUTION_ENABLED", "true")
        config = EvolutionConfig()
        assert config.enabled is True

    @pytest.mark.unit
    def test_env_override_explicit_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SYNTHORG_ENGINE_EVOLUTION_ENABLED=false keeps the kill-switch off."""
        monkeypatch.setenv("SYNTHORG_ENGINE_EVOLUTION_ENABLED", "false")
        config = EvolutionConfig()
        assert config.enabled is False

    @pytest.mark.unit
    def test_caller_kwarg_wins_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An explicit ``enabled=True`` kwarg beats the env override."""
        monkeypatch.setenv("SYNTHORG_ENGINE_EVOLUTION_ENABLED", "false")
        config = EvolutionConfig(enabled=True)
        assert config.enabled is True


class TestTriggerConfig:
    """TriggerConfig validation."""

    @pytest.mark.unit
    def test_defaults(self) -> None:
        cfg = TriggerConfig()
        assert cfg.batched_interval_seconds == 86400
        assert cfg.per_task_min_tasks == 1

    @pytest.mark.unit
    def test_interval_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="greater"):
            TriggerConfig(batched_interval_seconds=0)


class TestProposerConfig:
    """ProposerConfig validation."""

    @pytest.mark.unit
    def test_defaults(self) -> None:
        cfg = ProposerConfig()
        assert cfg.type == "composite"
        assert cfg.model == "example-small-001"
        assert cfg.temperature == 0.3

    @pytest.mark.unit
    def test_temperature_bounds(self) -> None:
        with pytest.raises(ValueError, match="less than or equal"):
            ProposerConfig(temperature=2.5)


class TestAdapterConfig:
    """AdapterConfig defaults are safe."""

    @pytest.mark.unit
    def test_identity_off_by_default(self) -> None:
        cfg = AdapterConfig()
        assert cfg.identity is False
        assert cfg.strategy_selection is True
        assert cfg.prompt_template is True


class TestGuardConfig:
    """GuardConfig validation."""

    @pytest.mark.unit
    def test_defaults(self) -> None:
        cfg = GuardConfig()
        assert cfg.rate_limit_per_day == 3
        assert cfg.rollback_window_tasks == 20
        assert cfg.rollback_regression_threshold == 0.1

    @pytest.mark.unit
    def test_threshold_bounds(self) -> None:
        with pytest.raises(ValueError, match="less than or equal"):
            GuardConfig(rollback_regression_threshold=1.5)

    @pytest.mark.unit
    def test_rate_limit_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="greater"):
            GuardConfig(rate_limit_per_day=0)
