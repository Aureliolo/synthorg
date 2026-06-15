"""Unit tests for the training source-selector factory registry."""

import pytest

from synthorg.core.registry.errors import StrategyFactoryNotFoundError
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.training.config import CompositeSubSelector, TrainingConfig
from synthorg.hr.training.factory import _build_selector
from synthorg.hr.training.source_selectors.composite import CompositeSelector
from synthorg.hr.training.source_selectors.department_diversity import (
    DepartmentDiversitySampling,
)
from synthorg.hr.training.source_selectors.role_top_performers import (
    RoleTopPerformers,
)
from tests._shared import mock_of

pytestmark = pytest.mark.unit


def _deps() -> tuple[PerformanceTracker, AgentRegistryService]:
    return (
        mock_of[PerformanceTracker](),
        mock_of[AgentRegistryService](),
    )


class TestSelectorFactorySelection:
    def test_role_top_performers_is_default(self) -> None:
        tracker, registry = _deps()
        selector = _build_selector(TrainingConfig(), tracker=tracker, registry=registry)
        assert isinstance(selector, RoleTopPerformers)

    def test_department_diversity_selection(self) -> None:
        tracker, registry = _deps()
        config = TrainingConfig(source_selector_type="department_diversity")
        selector = _build_selector(config, tracker=tracker, registry=registry)
        assert isinstance(selector, DepartmentDiversitySampling)

    def test_composite_selection(self) -> None:
        tracker, registry = _deps()
        config = TrainingConfig(
            source_selector_type="composite",
            composite_sub_selectors=(
                CompositeSubSelector(selector_type="role_top_performers", weight=0.7),
                CompositeSubSelector(selector_type="department_diversity", weight=0.3),
            ),
        )
        selector = _build_selector(config, tracker=tracker, registry=registry)
        assert isinstance(selector, CompositeSelector)

    def test_unknown_strategy_raises(self) -> None:
        tracker, registry = _deps()
        config = TrainingConfig(source_selector_type="bogus")
        with pytest.raises(StrategyFactoryNotFoundError):
            _build_selector(config, tracker=tracker, registry=registry)


class TestCompositeBuild:
    def test_builds_children_with_weights(self) -> None:
        tracker, registry = _deps()
        config = TrainingConfig(
            source_selector_type="composite",
            composite_sub_selectors=(
                CompositeSubSelector(selector_type="role_top_performers", weight=0.6),
                CompositeSubSelector(selector_type="department_diversity", weight=0.4),
            ),
        )
        selector = _build_selector(config, tracker=tracker, registry=registry)
        assert isinstance(selector, CompositeSelector)
        assert selector._weights == (0.6, 0.4)
        assert isinstance(selector._selectors[0], RoleTopPerformers)
        assert isinstance(selector._selectors[1], DepartmentDiversitySampling)

    def test_empty_sub_selectors_raises(self) -> None:
        tracker, registry = _deps()
        config = TrainingConfig(source_selector_type="composite")
        with pytest.raises(ValueError, match="non-empty"):
            _build_selector(config, tracker=tracker, registry=registry)

    def test_nested_composite_rejected(self) -> None:
        tracker, registry = _deps()
        config = TrainingConfig(
            source_selector_type="composite",
            composite_sub_selectors=(
                CompositeSubSelector(selector_type="composite", weight=1.0),
            ),
        )
        with pytest.raises(ValueError, match="must not nest"):
            _build_selector(config, tracker=tracker, registry=registry)
