"""Tests for MemoryRetrievalConfig."""

import pytest
import structlog.testing
from pydantic import ValidationError

from synthorg.memory.injection import InjectionPoint, InjectionStrategy
from synthorg.memory.ranking import FusionStrategy
from synthorg.memory.retrieval_config import MemoryRetrievalConfig
from synthorg.observability.events.config import CONFIG_VALIDATION_FAILED


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
        assert c.max_memories == 5
        assert c.include_shared is True
        assert c.default_relevance == 0.5
        assert c.injection_point is InjectionPoint.SYSTEM
        assert c.fusion_strategy is FusionStrategy.LINEAR
        assert c.rrf_k == 60

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

    def test_tool_based_strategy_accepted(self) -> None:
        c = MemoryRetrievalConfig(strategy=InjectionStrategy.TOOL_BASED)
        assert c.strategy is InjectionStrategy.TOOL_BASED

    def test_self_editing_strategy_accepted(self) -> None:
        c = MemoryRetrievalConfig(strategy=InjectionStrategy.SELF_EDITING)
        assert c.strategy is InjectionStrategy.SELF_EDITING

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


@pytest.mark.unit
class TestMemoryRetrievalConfigFusion:
    def test_default_fusion_strategy_is_linear(self) -> None:
        c = MemoryRetrievalConfig()
        assert c.fusion_strategy is FusionStrategy.LINEAR

    def test_rrf_fusion_strategy_accepted(self) -> None:
        c = MemoryRetrievalConfig(fusion_strategy=FusionStrategy.RRF)
        assert c.fusion_strategy is FusionStrategy.RRF

    def test_rrf_skips_weight_sum_validation(self) -> None:
        """RRF does not need relevance + recency weights to sum to 1."""
        c = MemoryRetrievalConfig(
            fusion_strategy=FusionStrategy.RRF,
            relevance_weight=0.5,
            recency_weight=0.3,
        )
        assert c.fusion_strategy is FusionStrategy.RRF
        assert c.relevance_weight == 0.5
        assert c.recency_weight == 0.3

    def test_linear_strategy_still_enforces_weight_sum(self) -> None:
        with pytest.raises(ValidationError, match=r"must equal 1\.0"):
            MemoryRetrievalConfig(
                fusion_strategy=FusionStrategy.LINEAR,
                relevance_weight=0.5,
                recency_weight=0.3,
            )

    def test_default_rrf_k(self) -> None:
        c = MemoryRetrievalConfig()
        assert c.rrf_k == 60

    def test_rrf_k_boundaries(self) -> None:
        c1 = MemoryRetrievalConfig(rrf_k=1)
        assert c1.rrf_k == 1
        c2 = MemoryRetrievalConfig(rrf_k=1000)
        assert c2.rrf_k == 1000

    def test_rrf_k_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryRetrievalConfig(rrf_k=0)

    def test_rrf_k_above_max_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryRetrievalConfig(rrf_k=1001)

    def test_rrf_k_non_default_with_linear_warns(self) -> None:
        """Custom rrf_k with LINEAR fusion emits a warning."""
        with structlog.testing.capture_logs() as cap:
            c = MemoryRetrievalConfig(rrf_k=42)
        assert c.rrf_k == 42
        events = [e for e in cap if e.get("event") == CONFIG_VALIDATION_FAILED]
        assert len(events) == 1
        assert events[0]["field"] == "rrf_k"
        assert "rrf_k is ignored" in events[0]["reason"]


@pytest.mark.unit
class TestMemoryRetrievalConfigDiversity:
    def test_default_diversity_enabled(self) -> None:
        # On by default: with the tight shipped budget, spending slots on
        # near-duplicate memories is exactly what MMR is here to prevent.
        c = MemoryRetrievalConfig()
        assert c.diversity_penalty_enabled is True

    def test_default_diversity_lambda(self) -> None:
        c = MemoryRetrievalConfig()
        assert c.diversity_lambda == 0.7

    def test_diversity_enabled(self) -> None:
        c = MemoryRetrievalConfig(diversity_penalty_enabled=True)
        assert c.diversity_penalty_enabled is True

    def test_diversity_lambda_boundaries(self) -> None:
        c0 = MemoryRetrievalConfig(
            diversity_penalty_enabled=True,
            diversity_lambda=0.0,
        )
        assert c0.diversity_lambda == 0.0
        c1 = MemoryRetrievalConfig(
            diversity_penalty_enabled=True,
            diversity_lambda=1.0,
        )
        assert c1.diversity_lambda == 1.0

    def test_diversity_lambda_below_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryRetrievalConfig(diversity_lambda=-0.1)

    def test_diversity_lambda_above_one_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryRetrievalConfig(diversity_lambda=1.1)

    def test_diversity_lambda_non_default_when_disabled_warns(self) -> None:
        with structlog.testing.capture_logs() as cap:
            c = MemoryRetrievalConfig(
                diversity_penalty_enabled=False,
                diversity_lambda=0.5,
            )
        assert c.diversity_lambda == 0.5
        events = [
            e
            for e in cap
            if e.get("event") == CONFIG_VALIDATION_FAILED
            and e.get("field") == "diversity_lambda"
        ]
        assert len(events) == 1
        assert "diversity_lambda is ignored" in events[0]["reason"]

    def test_diversity_lambda_default_when_disabled_no_warning(self) -> None:
        with structlog.testing.capture_logs() as cap:
            MemoryRetrievalConfig()
        events = [
            e
            for e in cap
            if e.get("event") == CONFIG_VALIDATION_FAILED
            and e.get("field") == "diversity_lambda"
        ]
        assert len(events) == 0

    def test_diversity_lambda_nan_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryRetrievalConfig(diversity_lambda=float("nan"))


@pytest.mark.unit
class TestMemoryRetrievalConfigReformulation:
    def test_default_reformulation_disabled(self) -> None:
        c = MemoryRetrievalConfig()
        assert c.query_reformulation_enabled is False

    def test_default_max_reformulation_rounds(self) -> None:
        c = MemoryRetrievalConfig()
        assert c.max_reformulation_rounds == 2

    def test_reformulation_enabled_with_context_rejected(self) -> None:
        """Reformulation is only wired into TOOL_BASED strategy."""
        with pytest.raises(ValueError, match=r"requires strategy='tool_based'"):
            MemoryRetrievalConfig(
                strategy=InjectionStrategy.CONTEXT,
                query_reformulation_enabled=True,
            )

    def test_reformulation_enabled_with_tool_based_accepted(self) -> None:
        c = MemoryRetrievalConfig(
            strategy=InjectionStrategy.TOOL_BASED,
            query_reformulation_enabled=True,
        )
        assert c.query_reformulation_enabled is True
        assert c.strategy is InjectionStrategy.TOOL_BASED

    def test_max_reformulation_rounds_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryRetrievalConfig(max_reformulation_rounds=0)

    def test_max_reformulation_rounds_six_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryRetrievalConfig(max_reformulation_rounds=6)

    def test_max_reformulation_rounds_boundaries(self) -> None:
        c1 = MemoryRetrievalConfig(max_reformulation_rounds=1)
        assert c1.max_reformulation_rounds == 1
        c5 = MemoryRetrievalConfig(max_reformulation_rounds=5)
        assert c5.max_reformulation_rounds == 5


@pytest.mark.unit
class TestPersonalBoostRRFWarning:
    def test_personal_boost_with_rrf_warns(self) -> None:
        """personal_boost > 0 with RRF fusion emits a warning."""
        with structlog.testing.capture_logs() as cap:
            c = MemoryRetrievalConfig(
                fusion_strategy=FusionStrategy.RRF,
                personal_boost=0.1,
            )
        assert c.personal_boost == 0.1
        events = [e for e in cap if e.get("event") == CONFIG_VALIDATION_FAILED]
        assert len(events) == 1
        assert "personal_boost" in events[0]["reason"]

    def test_default_personal_boost_with_rrf_no_warning(self) -> None:
        """Default personal_boost (0.1) should not warn -- only explicit."""
        with structlog.testing.capture_logs() as cap:
            c = MemoryRetrievalConfig(fusion_strategy=FusionStrategy.RRF)
        assert c.personal_boost == 0.1
        events = [
            e
            for e in cap
            if e.get("event") == CONFIG_VALIDATION_FAILED
            and e.get("field") == "personal_boost"
        ]
        assert len(events) == 0

    def test_personal_boost_zero_with_rrf_no_warning(self) -> None:
        with structlog.testing.capture_logs() as cap:
            MemoryRetrievalConfig(
                fusion_strategy=FusionStrategy.RRF,
                personal_boost=0.0,
            )
        events = [
            e
            for e in cap
            if e.get("event") == CONFIG_VALIDATION_FAILED
            and e.get("field") == "personal_boost"
        ]
        assert len(events) == 0


@pytest.mark.unit
class TestDiversityStrategyConsistency:
    def test_diversity_with_tool_based_raises(self) -> None:
        """Diversity penalty only applies to CONTEXT; raise on TOOL_BASED.

        Symmetric enforcement with
        ``_validate_reformulation_requires_tool_based``: a silent no-op
        is worse than a hard error because the misconfiguration would
        survive deployment unnoticed.
        """
        with (
            structlog.testing.capture_logs() as cap,
            pytest.raises(
                ValueError,
                match=(
                    r"diversity_penalty_enabled is only applied by "
                    r"ContextInjectionStrategy"
                ),
            ),
        ):
            MemoryRetrievalConfig(
                strategy=InjectionStrategy.TOOL_BASED,
                diversity_penalty_enabled=True,
            )
        events = [
            e
            for e in cap
            if e.get("event") == CONFIG_VALIDATION_FAILED
            and e.get("field") == "diversity_penalty_enabled"
        ]
        assert len(events) == 1
        assert "ContextInjectionStrategy" in events[0]["reason"]

    def test_diversity_with_context_no_warning(self) -> None:
        """Diversity penalty with CONTEXT strategy is the supported path."""
        with structlog.testing.capture_logs() as cap:
            MemoryRetrievalConfig(
                strategy=InjectionStrategy.CONTEXT,
                diversity_penalty_enabled=True,
            )
        events = [
            e
            for e in cap
            if e.get("event") == CONFIG_VALIDATION_FAILED
            and e.get("field") == "diversity_penalty_enabled"
        ]
        assert len(events) == 0
