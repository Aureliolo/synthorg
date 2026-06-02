"""Tests for client-simulation factory dispatch."""

from pathlib import Path

import pytest
from typeguard import suppress_type_checks

from synthorg.client.adapters import (
    DirectAdapter,
    IntakeAdapter,
    ProjectAdapter,
)
from synthorg.client.config import (
    ClientPoolConfig,
    FeedbackConfig,
    ReportConfig,
    RequirementGeneratorConfig,
)
from synthorg.client.factory import (
    UnknownStrategyError,
    build_client_pool_strategy,
    build_entry_point_strategy,
    build_feedback_strategy,
    build_report_strategy,
    build_requirement_generator,
)
from synthorg.client.feedback.adversarial import AdversarialFeedback
from synthorg.client.feedback.binary import BinaryFeedback
from synthorg.client.feedback.criteria_check import CriteriaCheckFeedback
from synthorg.client.feedback.scored import ScoredFeedback
from synthorg.client.generators.dataset import DatasetGenerator
from synthorg.client.generators.llm import LLMGenerator
from synthorg.client.generators.procedural import ProceduralGenerator
from synthorg.client.generators.template import TemplateGenerator
from synthorg.client.pool import (
    DomainMatchedStrategy,
    RoundRobinStrategy,
    WeightedRandomStrategy,
)
from synthorg.client.report.detailed import DetailedReport
from synthorg.client.report.json_export import JsonExportReport
from synthorg.client.report.metrics_only import MetricsOnlyReport
from synthorg.client.report.summary import SummaryReport
from synthorg.core.types import NotBlankStr

pytestmark = pytest.mark.unit


class TestFeedbackFactory:
    @pytest.mark.parametrize(
        ("config", "expected_type"),
        [
            (FeedbackConfig(strategy="binary"), BinaryFeedback),
            (
                FeedbackConfig(strategy="scored", passing_score=0.75),
                ScoredFeedback,
            ),
            (FeedbackConfig(strategy="criteria_check"), CriteriaCheckFeedback),
            (FeedbackConfig(strategy="adversarial"), AdversarialFeedback),
        ],
    )
    def test_dispatch_by_strategy(
        self,
        config: FeedbackConfig,
        expected_type: type,
    ) -> None:
        impl = build_feedback_strategy(config, client_id=NotBlankStr("c1"))
        assert isinstance(impl, expected_type)

    def test_unknown_raises(self) -> None:
        with pytest.raises(UnknownStrategyError, match="unknown feedback"):
            build_feedback_strategy(
                FeedbackConfig(strategy="bogus"),
                client_id=NotBlankStr("c1"),
            )


class TestReportFactory:
    @pytest.mark.parametrize(
        ("strategy", "expected_type"),
        [
            ("summary", SummaryReport),
            ("detailed", DetailedReport),
            ("json_export", JsonExportReport),
            ("metrics_only", MetricsOnlyReport),
        ],
    )
    def test_dispatch_by_strategy(
        self,
        strategy: str,
        expected_type: type,
    ) -> None:
        impl = build_report_strategy(ReportConfig(strategy=strategy))
        assert isinstance(impl, expected_type)

    def test_unknown_raises(self) -> None:
        with pytest.raises(UnknownStrategyError, match="unknown report"):
            build_report_strategy(ReportConfig(strategy="xml"))


class TestPoolStrategyFactory:
    @pytest.mark.parametrize(
        ("selection_strategy", "expected_type"),
        [
            ("round_robin", RoundRobinStrategy),
            ("weighted_random", WeightedRandomStrategy),
            ("domain_matched", DomainMatchedStrategy),
        ],
    )
    def test_dispatch_by_strategy(
        self,
        selection_strategy: str,
        expected_type: type,
    ) -> None:
        impl = build_client_pool_strategy(
            ClientPoolConfig(selection_strategy=selection_strategy),
        )
        assert isinstance(impl, expected_type)

    def test_default_is_round_robin(self) -> None:
        impl = build_client_pool_strategy(ClientPoolConfig())
        assert isinstance(impl, RoundRobinStrategy)

    def test_unknown_raises(self) -> None:
        with pytest.raises(UnknownStrategyError, match="pool selection"):
            build_client_pool_strategy(
                ClientPoolConfig(selection_strategy="unknown"),
            )


class TestEntryPointFactory:
    @pytest.mark.parametrize(
        ("adapter", "expected_type", "project_id"),
        [
            ("direct", DirectAdapter, None),
            ("project", ProjectAdapter, NotBlankStr("proj-123")),
            ("intake", IntakeAdapter, None),
        ],
    )
    def test_dispatch_by_adapter(
        self,
        adapter: str,
        expected_type: type,
        project_id: NotBlankStr | None,
    ) -> None:
        impl = build_entry_point_strategy(
            NotBlankStr(adapter),
            project_id=project_id,
        )
        assert isinstance(impl, expected_type)

    def test_project_requires_id(self) -> None:
        with pytest.raises(UnknownStrategyError, match="project_id"):
            build_entry_point_strategy(NotBlankStr("project"))

    def test_unknown_raises(self) -> None:
        with pytest.raises(UnknownStrategyError, match="entry-point"):
            build_entry_point_strategy(NotBlankStr("unknown"))


class _StubProvider:
    """Minimal ``CompletionProvider`` stub for factory tests.

    The factory only wires the provider into ``LLMGenerator``; it
    never calls it. Using a stub avoids dragging a real provider
    backend into unit tests while still letting the happy path
    construct a real ``LLMGenerator``.
    """


class TestRequirementGeneratorFactory:
    def test_procedural(self) -> None:
        impl = build_requirement_generator(
            RequirementGeneratorConfig(strategy="procedural"),
        )
        assert isinstance(impl, ProceduralGenerator)

    def test_template_happy_path(self, tmp_path: Path) -> None:
        """``template`` strategy with ``template_path`` returns a TemplateGenerator."""
        template_file = tmp_path / "req.json"
        template_file.write_text(
            '[{"title": "t", "description": "d"}]',
            encoding="utf-8",
        )
        impl = build_requirement_generator(
            RequirementGeneratorConfig(
                strategy="template",
                template_path=str(template_file),
            ),
        )
        assert isinstance(impl, TemplateGenerator)

    def test_dataset_happy_path(self, tmp_path: Path) -> None:
        """``dataset`` strategy with ``dataset_path`` returns a DatasetGenerator."""
        dataset_file = tmp_path / "rows.jsonl"
        dataset_file.write_text(
            '{"title":"t","description":"d","priority":"medium"}\n',
            encoding="utf-8",
        )
        impl = build_requirement_generator(
            RequirementGeneratorConfig(
                strategy="dataset",
                dataset_path=str(dataset_file),
            ),
        )
        assert isinstance(impl, DatasetGenerator)

    def test_llm_happy_path(self) -> None:
        """``llm`` strategy with provider + model returns an LLMGenerator."""
        with suppress_type_checks():
            impl = build_requirement_generator(
                RequirementGeneratorConfig(
                    strategy="llm",
                    llm_model=NotBlankStr("test-small-001"),
                ),
                provider=_StubProvider(),  # type: ignore[arg-type]
            )
        assert isinstance(impl, LLMGenerator)

    def test_template_requires_path(self) -> None:
        with pytest.raises(UnknownStrategyError, match="template_path"):
            build_requirement_generator(
                RequirementGeneratorConfig(strategy="template"),
            )

    def test_dataset_requires_path(self) -> None:
        with pytest.raises(UnknownStrategyError, match="dataset_path"):
            build_requirement_generator(
                RequirementGeneratorConfig(strategy="dataset"),
            )

    def test_llm_requires_provider(self) -> None:
        with pytest.raises(UnknownStrategyError, match="provider"):
            build_requirement_generator(
                RequirementGeneratorConfig(strategy="llm"),
            )

    def test_hybrid_rejects_single_arg(self) -> None:
        with pytest.raises(UnknownStrategyError, match="hybrid"):
            build_requirement_generator(
                RequirementGeneratorConfig(strategy="hybrid"),
            )

    def test_unknown_raises(self) -> None:
        with pytest.raises(
            UnknownStrategyError,
            match="unknown requirement generator",
        ):
            build_requirement_generator(
                RequirementGeneratorConfig(strategy="mystery"),
            )
