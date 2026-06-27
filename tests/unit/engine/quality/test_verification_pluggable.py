"""Tests for verification pluggable protocols, factory, and catalog."""

import pytest
from pydantic import ValidationError

from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.engine.quality.decomposer_protocol import CriteriaDecomposer
from synthorg.engine.quality.decomposers.identity import (
    IdentityCriteriaDecomposer,
)
from synthorg.engine.quality.decomposers.llm import LLMCriteriaDecomposer
from synthorg.engine.quality.grader_protocol import RubricGrader
from synthorg.engine.quality.graders.heuristic import (
    HeuristicRubricGrader,
)
from synthorg.engine.quality.graders.llm import LLMRubricGrader
from synthorg.engine.quality.rubric_catalog import (
    BUILTIN_RUBRICS,
    get_rubric,
)
from synthorg.engine.quality.verification_config import (
    DecomposerVariant,
    GraderVariant,
    VerificationConfig,
)
from synthorg.engine.quality.verification_factory import (
    build_decomposer,
    build_grader,
)
from tests._shared import mock_of
from tests.unit.providers.conftest import FakeProvider


def _tier_resolver(tier: str) -> str:
    return f"test-{tier}-001"


# -- Protocol conformance ----------------------------------------


@pytest.mark.unit
class TestProtocolConformance:
    def test_identity_decomposer_is_criteria_decomposer(self) -> None:
        assert isinstance(IdentityCriteriaDecomposer(), CriteriaDecomposer)

    def test_llm_decomposer_is_criteria_decomposer(self) -> None:
        decomposer = LLMCriteriaDecomposer(
            provider=FakeProvider(),
            model_id="test-medium-001",
        )
        assert isinstance(decomposer, CriteriaDecomposer)

    def test_heuristic_grader_is_rubric_grader(self) -> None:
        assert isinstance(HeuristicRubricGrader(), RubricGrader)

    def test_llm_grader_is_rubric_grader(self) -> None:
        grader = LLMRubricGrader(
            provider=FakeProvider(),
            model_id="test-medium-001",
        )
        assert isinstance(grader, RubricGrader)


# -- Factory -----------------------------------------------------


@pytest.mark.unit
class TestFactory:
    def test_build_identity_decomposer(self) -> None:
        cfg = VerificationConfig(decomposer=DecomposerVariant.IDENTITY)
        d = build_decomposer(cfg)
        assert isinstance(d, IdentityCriteriaDecomposer)
        assert d.name == "identity"

    def test_build_llm_decomposer_requires_provider(self) -> None:
        cfg = VerificationConfig(decomposer=DecomposerVariant.LLM)
        with pytest.raises(ValueError, match="LLM decomposer requires"):
            build_decomposer(cfg)

    def test_build_llm_decomposer_requires_tier_resolver(self) -> None:
        cfg = VerificationConfig(decomposer=DecomposerVariant.LLM)
        with pytest.raises(ValueError, match="LLM decomposer requires"):
            build_decomposer(cfg, provider=FakeProvider())

    def test_build_llm_decomposer_with_dependencies(self) -> None:
        cfg = VerificationConfig(
            decomposer=DecomposerVariant.LLM,
            max_probes_per_criterion=7,
        )
        d = build_decomposer(
            cfg,
            provider=FakeProvider(),
            tier_resolver=_tier_resolver,
        )
        assert isinstance(d, LLMCriteriaDecomposer)
        assert d.name == "llm"
        # VerificationConfig values must propagate into the decomposer.
        assert d._max_probes_per_criterion == cfg.max_probes_per_criterion
        assert d._model_id == _tier_resolver(cfg.decomposer_model_tier)

    def test_build_llm_decomposer_forwards_cost_tracker(self) -> None:
        """The factory forwards cost_tracker so probes are cost-tracked."""
        cfg = VerificationConfig(decomposer=DecomposerVariant.LLM)
        tracker = mock_of[CostTrackerProtocol]()
        d = build_decomposer(
            cfg,
            provider=FakeProvider(),
            tier_resolver=_tier_resolver,
            cost_tracker=tracker,
        )
        assert isinstance(d, LLMCriteriaDecomposer)
        assert d._cost_tracker is tracker

    def test_build_heuristic_grader(self) -> None:
        cfg = VerificationConfig(grader=GraderVariant.HEURISTIC)
        g = build_grader(cfg)
        assert isinstance(g, HeuristicRubricGrader)
        assert g.name == "heuristic"

    def test_build_llm_grader_requires_provider(self) -> None:
        cfg = VerificationConfig(grader=GraderVariant.LLM)
        with pytest.raises(ValueError, match="LLM grader requires"):
            build_grader(cfg)

    def test_build_llm_grader_requires_tier_resolver(self) -> None:
        cfg = VerificationConfig(grader=GraderVariant.LLM)
        with pytest.raises(ValueError, match="LLM grader requires"):
            build_grader(cfg, provider=FakeProvider())

    def test_build_llm_grader_with_dependencies(self) -> None:
        cfg = VerificationConfig(
            grader=GraderVariant.LLM,
            min_confidence_override=0.8,
        )
        g = build_grader(
            cfg,
            provider=FakeProvider(),
            tier_resolver=_tier_resolver,
        )
        assert isinstance(g, LLMRubricGrader)
        assert g.name == "llm"
        # VerificationConfig values must propagate into the grader.
        assert g._min_confidence_override == cfg.min_confidence_override
        assert g._model_id == _tier_resolver(cfg.grader_model_tier)

    def test_build_llm_grader_forwards_cost_tracker(self) -> None:
        """The factory forwards cost_tracker so grading calls are tracked."""
        cfg = VerificationConfig(grader=GraderVariant.LLM)
        tracker = mock_of[CostTrackerProtocol]()
        g = build_grader(
            cfg,
            provider=FakeProvider(),
            tier_resolver=_tier_resolver,
            cost_tracker=tracker,
        )
        assert isinstance(g, LLMRubricGrader)
        assert g._cost_tracker is tracker

    def test_each_build_produces_fresh_instance(self) -> None:
        cfg = VerificationConfig(decomposer=DecomposerVariant.IDENTITY)
        d1 = build_decomposer(cfg)
        d2 = build_decomposer(cfg)
        assert d1 is not d2


# -- Config ------------------------------------------------------


@pytest.mark.unit
class TestVerificationConfig:
    def test_default_config(self) -> None:
        cfg = VerificationConfig()
        assert cfg.decomposer == DecomposerVariant.IDENTITY
        assert cfg.grader == GraderVariant.HEURISTIC
        assert cfg.decomposer_model_tier == "medium"
        assert cfg.grader_model_tier == "medium"

    def test_frozen(self) -> None:
        cfg = VerificationConfig()
        with pytest.raises(ValidationError, match="frozen"):
            cfg.decomposer = DecomposerVariant.IDENTITY  # type: ignore[misc]

    def test_roundtrip(self) -> None:
        cfg = VerificationConfig(
            decomposer=DecomposerVariant.IDENTITY,
            grader=GraderVariant.HEURISTIC,
            max_probes_per_criterion=10,
        )
        restored = VerificationConfig.model_validate(cfg.model_dump())
        assert restored == cfg


# -- Rubric catalog ----------------------------------------------


@pytest.mark.unit
class TestRubricCatalog:
    def test_frontend_design_rubric_exists(self) -> None:
        rubric = get_rubric("frontend-design")
        assert rubric.name == "frontend-design"
        assert len(rubric.criteria) == 4

    def test_default_task_rubric_exists(self) -> None:
        rubric = get_rubric("default-task")
        assert rubric.name == "default-task"
        assert len(rubric.criteria) == 3

    def test_unknown_rubric_raises(self) -> None:
        with pytest.raises(KeyError, match="nonexistent"):
            get_rubric("nonexistent")

    def test_catalog_is_immutable(self) -> None:
        with pytest.raises(TypeError):
            BUILTIN_RUBRICS["new"] = None  # type: ignore[index]


@pytest.mark.unit
class TestFactoryErrorPaths:
    def test_unknown_decomposer_variant_raises(self) -> None:
        cfg = VerificationConfig(decomposer=DecomposerVariant.IDENTITY)
        valid_cfg = cfg.model_copy(
            update={"decomposer": "nonexistent"},
        )
        with pytest.raises(ValueError, match="Unknown decomposer"):
            build_decomposer(valid_cfg)

    def test_unknown_grader_variant_raises(self) -> None:
        cfg = VerificationConfig(grader=GraderVariant.HEURISTIC)
        valid_cfg = cfg.model_copy(
            update={"grader": "nonexistent"},
        )
        with pytest.raises(ValueError, match="Unknown grader"):
            build_grader(valid_cfg)
