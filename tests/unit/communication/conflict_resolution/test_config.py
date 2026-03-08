"""Tests for conflict resolution configuration models."""

import pytest
from pydantic import ValidationError

from ai_company.communication.conflict_resolution.config import (
    ConflictResolutionConfig,
    DebateConfig,
    HybridConfig,
)
from ai_company.communication.enums import ConflictResolutionStrategy

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestDebateConfig:
    def test_defaults(self) -> None:
        cfg = DebateConfig()
        assert cfg.max_tokens_per_argument == 500
        assert cfg.judge == "shared_manager"

    def test_custom_values(self) -> None:
        cfg = DebateConfig(max_tokens_per_argument=1000, judge="ceo")
        assert cfg.max_tokens_per_argument == 1000
        assert cfg.judge == "ceo"

    def test_frozen(self) -> None:
        cfg = DebateConfig()
        with pytest.raises(ValidationError):
            cfg.judge = "changed"  # type: ignore[misc]

    def test_zero_tokens_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DebateConfig(max_tokens_per_argument=0)

    def test_negative_tokens_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DebateConfig(max_tokens_per_argument=-1)


@pytest.mark.unit
class TestHybridConfig:
    def test_defaults(self) -> None:
        cfg = HybridConfig()
        assert cfg.max_tokens_per_argument == 500
        assert cfg.review_agent == "conflict_reviewer"
        assert cfg.escalate_on_ambiguity is True

    def test_custom_values(self) -> None:
        cfg = HybridConfig(
            max_tokens_per_argument=300,
            review_agent="senior_reviewer",
            escalate_on_ambiguity=False,
        )
        assert cfg.review_agent == "senior_reviewer"
        assert cfg.escalate_on_ambiguity is False

    def test_frozen(self) -> None:
        cfg = HybridConfig()
        with pytest.raises(ValidationError):
            cfg.escalate_on_ambiguity = False  # type: ignore[misc]


@pytest.mark.unit
class TestConflictResolutionConfig:
    def test_defaults(self) -> None:
        cfg = ConflictResolutionConfig()
        assert cfg.strategy == ConflictResolutionStrategy.AUTHORITY
        assert isinstance(cfg.debate, DebateConfig)
        assert isinstance(cfg.hybrid, HybridConfig)

    def test_custom_strategy(self) -> None:
        cfg = ConflictResolutionConfig(
            strategy=ConflictResolutionStrategy.DEBATE,
        )
        assert cfg.strategy == ConflictResolutionStrategy.DEBATE

    def test_frozen(self) -> None:
        cfg = ConflictResolutionConfig()
        with pytest.raises(ValidationError):
            cfg.strategy = ConflictResolutionStrategy.HUMAN  # type: ignore[misc]

    def test_nested_config_override(self) -> None:
        cfg = ConflictResolutionConfig(
            debate=DebateConfig(judge="ceo"),
            hybrid=HybridConfig(escalate_on_ambiguity=False),
        )
        assert cfg.debate.judge == "ceo"
        assert cfg.hybrid.escalate_on_ambiguity is False
