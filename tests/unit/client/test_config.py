"""Unit tests for client simulation configuration models."""

import pytest
from pydantic import ValidationError

from synthorg.client.config import (
    ClientPoolConfig,
    ClientSimulationConfig,
    ContinuousModeConfig,
    FeedbackConfig,
    IntakeConfig,
    RequirementGeneratorConfig,
    SimulationRunnerConfig,
)

pytestmark = pytest.mark.unit


class TestRequirementGeneratorConfig:
    """Tests for RequirementGeneratorConfig."""

    def test_defaults(self) -> None:
        config = RequirementGeneratorConfig()
        assert config.strategy == "template"
        assert config.template_path is None
        assert config.dataset_path is None

    def test_frozen(self) -> None:
        config = RequirementGeneratorConfig()
        with pytest.raises(ValidationError):
            config.strategy = "llm"  # type: ignore[misc]


class TestFeedbackConfig:
    """Tests for FeedbackConfig."""

    def test_defaults(self) -> None:
        config = FeedbackConfig()
        assert config.strategy == "binary"
        assert config.passing_score == 0.7
        assert config.strictness_multiplier == 1.0

    def test_passing_score_bounds(self) -> None:
        with pytest.raises(ValidationError):
            FeedbackConfig(passing_score=1.5)

    def test_strictness_multiplier_positive(self) -> None:
        with pytest.raises(ValidationError):
            FeedbackConfig(strictness_multiplier=0.0)


class TestClientPoolConfig:
    """Tests for ClientPoolConfig."""

    def test_defaults(self) -> None:
        config = ClientPoolConfig()
        assert config.pool_size == 10
        assert config.ai_ratio == 0.8
        assert config.human_ratio == 0.1
        assert config.hybrid_ratio == 0.1

    def test_pool_size_positive(self) -> None:
        with pytest.raises(ValidationError):
            ClientPoolConfig(pool_size=0)

    def test_ratio_bounds(self) -> None:
        with pytest.raises(ValidationError):
            ClientPoolConfig(ai_ratio=1.5)

    def test_ratio_sum_must_equal_one(self) -> None:
        with pytest.raises(ValidationError, match="sum to approximately"):
            ClientPoolConfig(
                ai_ratio=0.9,
                human_ratio=0.9,
                hybrid_ratio=0.9,
            )

    def test_valid_ratio_sum(self) -> None:
        config = ClientPoolConfig(
            ai_ratio=0.6,
            human_ratio=0.3,
            hybrid_ratio=0.1,
        )
        assert config.ai_ratio == 0.6


class TestSimulationRunnerConfig:
    """Tests for SimulationRunnerConfig."""

    def test_defaults(self) -> None:
        config = SimulationRunnerConfig()
        assert config.max_concurrent_tasks == 10
        assert config.task_timeout_sec == 300.0
        assert config.review_timeout_sec == 60.0

    def test_timeout_positive(self) -> None:
        with pytest.raises(ValidationError):
            SimulationRunnerConfig(task_timeout_sec=0.0)


class TestContinuousModeConfig:
    """Tests for ContinuousModeConfig."""

    def test_defaults(self) -> None:
        config = ContinuousModeConfig()
        assert config.enabled is False
        assert config.request_interval_sec == 300.0
        assert config.max_concurrent_requests == 5

    def test_interval_positive(self) -> None:
        with pytest.raises(ValidationError):
            ContinuousModeConfig(request_interval_sec=0.0)


class TestIntakeConfig:
    """Tests for IntakeConfig."""

    def test_defaults(self) -> None:
        config = IntakeConfig()
        assert config.strategy == "direct"
        assert config.model is None

    def test_agent_strategy_with_model(self) -> None:
        config = IntakeConfig(strategy="agent", model="test-model-001")
        assert config.strategy == "agent"
        assert config.model == "test-model-001"

    def test_frozen(self) -> None:
        config = IntakeConfig()
        with pytest.raises(ValidationError):
            config.strategy = "agent"  # type: ignore[misc]

    def test_extra_forbid(self) -> None:
        with pytest.raises(ValidationError):
            IntakeConfig(unknown="x")  # type: ignore[call-arg]

    def test_blank_strategy_rejected(self) -> None:
        with pytest.raises(ValidationError):
            IntakeConfig(strategy="  ")


class TestClientSimulationConfig:
    """Tests for the top-level ClientSimulationConfig."""

    def test_defaults(self) -> None:
        config = ClientSimulationConfig()
        assert isinstance(config.pool, ClientPoolConfig)
        assert isinstance(config.generators, RequirementGeneratorConfig)
        assert isinstance(config.feedback, FeedbackConfig)
        assert isinstance(config.runner, SimulationRunnerConfig)
        assert isinstance(config.continuous, ContinuousModeConfig)
        assert isinstance(config.intake, IntakeConfig)
        assert config.intake.strategy == "direct"

    def test_frozen(self) -> None:
        config = ClientSimulationConfig()
        with pytest.raises(ValidationError):
            config.pool = ClientPoolConfig()  # type: ignore[misc]

    def test_nested_access(self) -> None:
        config = ClientSimulationConfig()
        assert config.pool.pool_size == 10
        assert config.feedback.strategy == "binary"
