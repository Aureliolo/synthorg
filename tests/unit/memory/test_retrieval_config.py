"""Tests for MemoryRetrievalConfig."""

import pytest
from pydantic import ValidationError

from synthorg.memory.injection import InjectionPoint, InjectionStrategy
from synthorg.memory.retrieval_config import MemoryRetrievalConfig

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestMemoryRetrievalConfigDefaults:
    def test_defaults(self) -> None:
        c = MemoryRetrievalConfig()
        assert c.strategy is InjectionStrategy.CONTEXT
        assert c.relevance_weight == 0.7
        assert c.recency_weight == 0.3
        assert c.recency_decay_rate == 0.01
        assert c.personal_boost == 0.1
        assert c.min_relevance == 0.3
        assert c.max_memories == 20
        assert c.include_shared is True
        assert c.default_relevance == 0.5
        assert c.injection_point is InjectionPoint.SYSTEM

    def test_frozen(self) -> None:
        c = MemoryRetrievalConfig()
        with pytest.raises(ValidationError):
            c.relevance_weight = 0.5  # type: ignore[misc]


@pytest.mark.unit
class TestMemoryRetrievalConfigValidation:
    def test_weights_must_sum_to_one(self) -> None:
        with pytest.raises(ValidationError, match=r"must equal 1\.0"):
            MemoryRetrievalConfig(relevance_weight=0.5, recency_weight=0.3)

    def test_custom_weights_summing_to_one(self) -> None:
        c = MemoryRetrievalConfig(relevance_weight=0.6, recency_weight=0.4)
        assert c.relevance_weight == 0.6
        assert c.recency_weight == 0.4

    def test_all_relevance_no_recency(self) -> None:
        c = MemoryRetrievalConfig(relevance_weight=1.0, recency_weight=0.0)
        assert c.relevance_weight == 1.0

    def test_all_recency_no_relevance(self) -> None:
        c = MemoryRetrievalConfig(relevance_weight=0.0, recency_weight=1.0)
        assert c.recency_weight == 1.0

    def test_relevance_weight_below_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryRetrievalConfig(relevance_weight=-0.1, recency_weight=1.1)

    def test_relevance_weight_above_one_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryRetrievalConfig(relevance_weight=1.1, recency_weight=-0.1)

    def test_recency_decay_rate_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryRetrievalConfig(recency_decay_rate=-0.01)

    def test_personal_boost_below_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryRetrievalConfig(personal_boost=-0.1)

    def test_personal_boost_above_one_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryRetrievalConfig(personal_boost=1.1)

    def test_min_relevance_below_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryRetrievalConfig(min_relevance=-0.1)

    def test_min_relevance_above_one_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryRetrievalConfig(min_relevance=1.1)

    def test_max_memories_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryRetrievalConfig(max_memories=0)

    def test_max_memories_above_100_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryRetrievalConfig(max_memories=101)

    def test_max_memories_boundary_accepted(self) -> None:
        c = MemoryRetrievalConfig(max_memories=1)
        assert c.max_memories == 1
        c2 = MemoryRetrievalConfig(max_memories=100)
        assert c2.max_memories == 100

    def test_default_relevance_boundaries(self) -> None:
        c = MemoryRetrievalConfig(default_relevance=0.0)
        assert c.default_relevance == 0.0
        c2 = MemoryRetrievalConfig(default_relevance=1.0)
        assert c2.default_relevance == 1.0

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("relevance_weight", float("nan")),
            ("relevance_weight", float("inf")),
            ("recency_weight", float("nan")),
            ("recency_weight", float("-inf")),
            ("personal_boost", float("nan")),
            ("min_relevance", float("inf")),
            ("default_relevance", float("nan")),
            ("recency_decay_rate", float("inf")),
        ],
    )
    def test_nan_inf_rejected(self, field: str, value: float) -> None:
        with pytest.raises(ValidationError):
            MemoryRetrievalConfig(**{field: value})  # type: ignore[arg-type]


@pytest.mark.unit
class TestMemoryRetrievalConfigStrategy:
    def test_context_strategy_accepted(self) -> None:
        c = MemoryRetrievalConfig(strategy=InjectionStrategy.CONTEXT)
        assert c.strategy is InjectionStrategy.CONTEXT

    def test_unsupported_strategy_rejected(self) -> None:
        with pytest.raises(ValueError, match="not yet implemented"):
            MemoryRetrievalConfig(strategy=InjectionStrategy.TOOL_BASED)
        with pytest.raises(ValueError, match="not yet implemented"):
            MemoryRetrievalConfig(strategy=InjectionStrategy.SELF_EDITING)

    def test_injection_point_user(self) -> None:
        c = MemoryRetrievalConfig(injection_point=InjectionPoint.USER)
        assert c.injection_point is InjectionPoint.USER


@pytest.mark.unit
class TestMemoryRetrievalConfigSerialization:
    def test_json_roundtrip(self) -> None:
        c = MemoryRetrievalConfig(
            relevance_weight=0.6,
            recency_weight=0.4,
            personal_boost=0.2,
            max_memories=50,
        )
        json_str = c.model_dump_json()
        restored = MemoryRetrievalConfig.model_validate_json(json_str)
        assert restored == c

    def test_dict_roundtrip(self) -> None:
        c = MemoryRetrievalConfig()
        data = c.model_dump()
        restored = MemoryRetrievalConfig.model_validate(data)
        assert restored == c
