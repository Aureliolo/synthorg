"""Tests for coordination metrics configuration models."""

from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from synthorg.budget.coordination_config import (
    CoordinationMetricName,
    CoordinationMetricsConfig,
    DetectionScope,
    DetectorCategoryConfig,
    DetectorVariant,
    ErrorCategory,
    ErrorTaxonomyConfig,
    OrchestrationAlertThresholds,
)
from synthorg.persistence.settings_protocol import SettingsRepository
from synthorg.settings import definitions as _settings_definitions  # noqa: F401
from synthorg.settings.enums import SettingType
from synthorg.settings.errors import SettingReadOnlyError
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService
from tests._shared import mock_of


@pytest.mark.unit
class TestCoordinationMetricName:
    """CoordinationMetricName enum."""

    def test_values(self) -> None:
        assert CoordinationMetricName.EFFICIENCY.value == "efficiency"
        assert CoordinationMetricName.OVERHEAD.value == "overhead"
        assert CoordinationMetricName.ERROR_AMPLIFICATION.value == "error_amplification"
        assert CoordinationMetricName.MESSAGE_DENSITY.value == "message_density"
        assert CoordinationMetricName.REDUNDANCY.value == "redundancy"
        assert CoordinationMetricName.AMDAHL_CEILING.value == "amdahl_ceiling"
        assert CoordinationMetricName.STRAGGLER_GAP.value == "straggler_gap"
        assert CoordinationMetricName.TOKEN_SPEEDUP_RATIO.value == "token_speedup_ratio"
        assert CoordinationMetricName.MESSAGE_OVERHEAD.value == "message_overhead"

    def test_member_count(self) -> None:
        assert len(CoordinationMetricName) == 9


@pytest.mark.unit
class TestErrorCategory:
    """ErrorCategory enum."""

    def test_original_values(self) -> None:
        assert ErrorCategory.LOGICAL_CONTRADICTION.value == "logical_contradiction"
        assert ErrorCategory.NUMERICAL_DRIFT.value == "numerical_drift"
        assert ErrorCategory.CONTEXT_OMISSION.value == "context_omission"
        assert ErrorCategory.COORDINATION_FAILURE.value == "coordination_failure"

    def test_new_values(self) -> None:
        assert (
            ErrorCategory.DELEGATION_PROTOCOL_VIOLATION.value
            == "delegation_protocol_violation"
        )
        assert (
            ErrorCategory.REVIEW_PIPELINE_VIOLATION.value == "review_pipeline_violation"
        )
        assert (
            ErrorCategory.AUTHORITY_BREACH_ATTEMPT.value == "authority_breach_attempt"
        )

    def test_member_count(self) -> None:
        assert len(ErrorCategory) == 7


@pytest.mark.unit
class TestDetectionScope:
    """DetectionScope enum."""

    def test_values(self) -> None:
        assert DetectionScope.SAME_TASK.value == "same_task"
        assert DetectionScope.TASK_TREE.value == "task_tree"

    def test_member_count(self) -> None:
        assert len(DetectionScope) == 2


@pytest.mark.unit
class TestDetectorVariant:
    """DetectorVariant enum."""

    def test_values(self) -> None:
        assert DetectorVariant.HEURISTIC.value == "heuristic"
        assert DetectorVariant.LLM_SEMANTIC.value == "llm_semantic"
        assert DetectorVariant.PROTOCOL_CHECK.value == "protocol_check"
        assert DetectorVariant.BEHAVIOR_CHECK.value == "behavior_check"

    def test_member_count(self) -> None:
        assert len(DetectorVariant) == 4


@pytest.mark.unit
class TestDetectorCategoryConfig:
    """DetectorCategoryConfig validation."""

    def test_defaults(self) -> None:
        config = DetectorCategoryConfig()
        assert config.variants == (DetectorVariant.HEURISTIC,)
        assert config.scope == DetectionScope.SAME_TASK

    def test_custom_llm_semantic_task_tree(self) -> None:
        config = DetectorCategoryConfig(
            variants=(DetectorVariant.LLM_SEMANTIC,),
            scope=DetectionScope.TASK_TREE,
        )
        assert len(config.variants) == 1
        assert config.scope == DetectionScope.TASK_TREE

    def test_heuristic_task_tree_rejected(self) -> None:
        """Heuristic variant does not support TASK_TREE scope."""
        with pytest.raises(ValidationError, match="does not support scope"):
            DetectorCategoryConfig(
                variants=(DetectorVariant.HEURISTIC,),
                scope=DetectionScope.TASK_TREE,
            )

    def test_behavior_check_task_tree_rejected(self) -> None:
        """Behavior check variant does not support TASK_TREE scope."""
        with pytest.raises(ValidationError, match="does not support scope"):
            DetectorCategoryConfig(
                variants=(DetectorVariant.BEHAVIOR_CHECK,),
                scope=DetectionScope.TASK_TREE,
            )

    def test_protocol_check_task_tree_allowed(self) -> None:
        config = DetectorCategoryConfig(
            variants=(DetectorVariant.PROTOCOL_CHECK,),
            scope=DetectionScope.TASK_TREE,
        )
        assert config.scope == DetectionScope.TASK_TREE

    def test_empty_variants_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must not be empty"):
            DetectorCategoryConfig(variants=())

    def test_duplicate_variants_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must not contain duplicates"):
            DetectorCategoryConfig(
                variants=(
                    DetectorVariant.HEURISTIC,
                    DetectorVariant.HEURISTIC,
                ),
            )

    def test_frozen(self) -> None:
        config = DetectorCategoryConfig()
        with pytest.raises(ValidationError):
            config.scope = DetectionScope.TASK_TREE  # type: ignore[misc]


@pytest.mark.unit
class TestErrorTaxonomyConfig:
    """ErrorTaxonomyConfig defaults and validation."""

    def test_defaults(self) -> None:
        config = ErrorTaxonomyConfig()
        assert config.enabled is False
        assert len(config.categories) == 7
        assert len(config.detectors) == 7

    def test_categories_computed_from_detectors(self) -> None:
        config = ErrorTaxonomyConfig(
            enabled=True,
            detectors={
                ErrorCategory.LOGICAL_CONTRADICTION: DetectorCategoryConfig(),
                ErrorCategory.NUMERICAL_DRIFT: DetectorCategoryConfig(),
            },
        )
        assert config.enabled is True
        assert len(config.categories) == 2
        assert ErrorCategory.LOGICAL_CONTRADICTION in config.categories
        assert ErrorCategory.NUMERICAL_DRIFT in config.categories

    def test_empty_detectors(self) -> None:
        config = ErrorTaxonomyConfig(
            enabled=True,
            detectors={},
        )
        assert config.categories == ()

    def test_default_llm_provider_tier(self) -> None:
        config = ErrorTaxonomyConfig()
        assert config.llm_provider_tier == "large"

    def test_default_classification_budget(self) -> None:
        config = ErrorTaxonomyConfig()
        assert config.classification_budget_per_task == 0.01

    def test_negative_budget_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ErrorTaxonomyConfig(
                classification_budget_per_task=-0.01,
            )

    def test_frozen(self) -> None:
        config = ErrorTaxonomyConfig()
        with pytest.raises(ValidationError):
            config.enabled = True  # type: ignore[misc]

    def test_default_detectors_structure(self) -> None:
        """Default detectors have correct variant types."""
        config = ErrorTaxonomyConfig()

        # Original 4 categories use HEURISTIC
        for cat in (
            ErrorCategory.LOGICAL_CONTRADICTION,
            ErrorCategory.NUMERICAL_DRIFT,
            ErrorCategory.CONTEXT_OMISSION,
            ErrorCategory.COORDINATION_FAILURE,
        ):
            assert config.detectors[cat].variants == (DetectorVariant.HEURISTIC,)
            assert config.detectors[cat].scope == DetectionScope.SAME_TASK

        # Protocol-level detectors
        for cat in (
            ErrorCategory.DELEGATION_PROTOCOL_VIOLATION,
            ErrorCategory.REVIEW_PIPELINE_VIOLATION,
        ):
            assert config.detectors[cat].variants == (DetectorVariant.PROTOCOL_CHECK,)
            assert config.detectors[cat].scope == DetectionScope.TASK_TREE

        # Authority breach
        assert config.detectors[ErrorCategory.AUTHORITY_BREACH_ATTEMPT].variants == (
            DetectorVariant.BEHAVIOR_CHECK,
        )


@pytest.mark.unit
class TestOrchestrationAlertThresholds:
    """OrchestrationAlertThresholds validation."""

    def test_defaults(self) -> None:
        t = OrchestrationAlertThresholds()
        assert t.info == 0.30
        assert t.warn == 0.50
        assert t.critical == 0.70

    def test_custom_valid(self) -> None:
        t = OrchestrationAlertThresholds(
            info=0.10,
            warn=0.20,
            critical=0.30,
        )
        assert t.info == 0.10
        assert t.warn == 0.20
        assert t.critical == 0.30

    def test_non_ordered_rejected(self) -> None:
        with pytest.raises(ValidationError, match="strictly ordered"):
            OrchestrationAlertThresholds(
                info=0.50,
                warn=0.30,
                critical=0.70,
            )

    def test_equal_thresholds_rejected(self) -> None:
        with pytest.raises(ValidationError, match="strictly ordered"):
            OrchestrationAlertThresholds(
                info=0.30,
                warn=0.30,
                critical=0.70,
            )

    def test_info_equals_critical_rejected(self) -> None:
        with pytest.raises(ValidationError, match="strictly ordered"):
            OrchestrationAlertThresholds(
                info=0.50,
                warn=0.60,
                critical=0.50,
            )

    def test_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            OrchestrationAlertThresholds(
                info=-0.1,
                warn=0.50,
                critical=0.70,
            )

    def test_above_one_rejected(self) -> None:
        with pytest.raises(ValidationError):
            OrchestrationAlertThresholds(
                info=0.30,
                warn=0.50,
                critical=1.1,
            )

    def test_frozen(self) -> None:
        t = OrchestrationAlertThresholds()
        with pytest.raises(ValidationError):
            t.info = 0.1  # type: ignore[misc]


@pytest.mark.unit
class TestCoordinationMetricsConfig:
    """CoordinationMetricsConfig defaults and validation."""

    def test_defaults(self) -> None:
        config = CoordinationMetricsConfig()
        assert config.enabled is False
        assert len(config.collect) == 9
        assert config.error_taxonomy.enabled is False
        assert config.orchestration_alerts.info == 0.30

    def test_enabled_with_subset(self) -> None:
        config = CoordinationMetricsConfig(
            enabled=True,
            collect=(
                CoordinationMetricName.EFFICIENCY,
                CoordinationMetricName.OVERHEAD,
            ),
        )
        assert config.enabled is True
        assert len(config.collect) == 2

    def test_frozen(self) -> None:
        config = CoordinationMetricsConfig()
        with pytest.raises(ValidationError):
            config.enabled = True  # type: ignore[misc]


@pytest.mark.unit
class TestBaselineWindowSizeSetting:
    """The ``> 0`` baseline-window invariant is enforced at the
    settings layer.

    The single-agent baseline window is no longer a Pydantic
    ``CoordinationMetricsConfig`` field with ``gt=0``; it is the
    registered ``budget.baseline_window_size`` setting. These tests
    guard that the positive-window invariant survived the move: the
    registered definition still carries the ``[1, 1_000_000]`` bound
    and, being read-only post-init, the value cannot be mutated to an
    out-of-range one at runtime.
    """

    def test_registered_definition_bounds(self) -> None:
        defn = get_registry().get("budget", "baseline_window_size")
        assert defn is not None
        assert defn.type is SettingType.INTEGER
        assert defn.default == "50"
        # The bound that the deleted ``gt=0`` field validator enforced.
        assert defn.min_value == 1
        assert defn.max_value == 1_000_000
        assert defn.read_only_post_init is True
        assert defn.restart_required is True
        assert defn.env_var_override == "SYNTHORG_BUDGET_BASELINE_WINDOW_SIZE"

    async def test_read_only_post_init_rejects_runtime_mutation(self) -> None:
        repo = mock_of[SettingsRepository](
            get=AsyncMock(return_value=None),
            get_namespace=AsyncMock(return_value=()),
            list_items=AsyncMock(return_value=()),
        )
        service = SettingsService(repository=repo, registry=get_registry())
        with pytest.raises(SettingReadOnlyError):
            await service.set("budget", "baseline_window_size", "51")
