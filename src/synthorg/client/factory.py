"""Factory functions for client-simulation strategies.

Each strategy family has a configuration discriminator
(``config.strategy`` / ``config.selection_strategy``) that selects a
concrete implementation. The factories below turn those strings into
instances, failing loudly on unknown values so misconfiguration never
silently falls through to a no-op default.
"""

from pathlib import Path
from typing import ClassVar

from synthorg.client.adapters import (
    DirectAdapter,
    IntakeAdapter,
    ProjectAdapter,
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
from synthorg.core.domain_errors import ValidationError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.core.types import NotBlankStr
from synthorg.core.validation import require_non_blank
from synthorg.observability import get_logger
from synthorg.observability.events.client import (
    CLIENT_FACTORY_UNKNOWN_STRATEGY,
)

logger = get_logger(__name__)

# Every name referenced in this module's public factory signatures
# (``build_requirement_generator``, ``build_feedback_strategy``, etc.)
# must resolve at runtime when downstream tooling evaluates the
# annotations -- e.g. ``typing.get_type_hints(...)`` from API docs
# generators or DI containers.  Keep them out of the ``TYPE_CHECKING``
# block so the names are present in module globals.
from synthorg.budget.tracker import CostTracker  # noqa: E402, TC001
from synthorg.client.config import (  # noqa: E402, TC001
    ClientPoolConfig,
    FeedbackConfig,
    ReportConfig,
    RequirementGeneratorConfig,
)
from synthorg.client.protocols import (  # noqa: E402, TC001
    ClientPoolStrategy,
    EntryPointStrategy,
    FeedbackStrategy,
    ReportStrategy,
    RequirementGenerator,
)
from synthorg.providers.protocol import CompletionProvider  # noqa: E402, TC001

_GENERATOR_STRATEGIES: frozenset[str] = frozenset(
    {"template", "llm", "dataset", "hybrid", "procedural"},
)
_FEEDBACK_STRATEGIES: frozenset[str] = frozenset(
    {"binary", "scored", "criteria_check", "adversarial"},
)
_REPORT_STRATEGIES: frozenset[str] = frozenset(
    {"summary", "detailed", "json_export", "metrics_only"},
)
_POOL_STRATEGIES: frozenset[str] = frozenset(
    {"round_robin", "weighted_random", "domain_matched"},
)
_ENTRY_POINT_STRATEGIES: frozenset[str] = frozenset(
    {"direct", "project", "intake"},
)


class UnknownStrategyError(ValidationError):
    """Raised when a config discriminator does not map to any strategy."""

    default_message: ClassVar[str] = "Unknown strategy discriminator"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    error_code: ClassVar[ErrorCode] = ErrorCode.VALIDATION_ERROR
    status_code: ClassVar[int] = 422


def _require_non_blank(
    value: object,
    *,
    factory: str,
    strategy: str,
    field: str,
) -> str:
    """Return ``value`` as a non-blank string or raise ``UnknownStrategyError``.

    Thin wrapper around :func:`synthorg.core.validation.require_non_blank`
    that surfaces a domain-specific error (``UnknownStrategyError``) and
    emits the ``CLIENT_FACTORY_UNKNOWN_STRATEGY`` event with the contextual
    factory / strategy / field labels before failing.
    """
    try:
        return require_non_blank(value, name=field)
    except ValueError as exc:
        logger.warning(
            CLIENT_FACTORY_UNKNOWN_STRATEGY,
            factory=factory,
            strategy=strategy,
            missing=field,
        )
        msg = f"{strategy} strategy requires {field}"
        raise UnknownStrategyError(msg) from exc


_REQ_GEN_FACTORY = "requirement_generator"


def _build_template_generator(
    config: RequirementGeneratorConfig,
    strategy: str,
) -> RequirementGenerator:
    template_path = _require_non_blank(
        config.template_path,
        factory=_REQ_GEN_FACTORY,
        strategy=strategy,
        field="config.template_path",
    )
    return TemplateGenerator(template_path=Path(template_path))


def _build_llm_generator(
    config: RequirementGeneratorConfig,
    strategy: str,
    *,
    provider: CompletionProvider | None,
    model: NotBlankStr | None,
    cost_tracker: CostTracker | None,
) -> RequirementGenerator:
    if provider is None:
        logger.warning(
            CLIENT_FACTORY_UNKNOWN_STRATEGY,
            factory=_REQ_GEN_FACTORY,
            strategy=strategy,
            missing="provider",
        )
        msg = "llm strategy requires a provider"
        raise UnknownStrategyError(msg)
    effective_model = _require_non_blank(
        model or config.llm_model,
        factory=_REQ_GEN_FACTORY,
        strategy=strategy,
        field="model (argument or config.llm_model)",
    )
    return LLMGenerator(
        provider=provider,
        model=NotBlankStr(effective_model),
        cost_tracker=cost_tracker,
    )


def _build_dataset_generator(
    config: RequirementGeneratorConfig,
    strategy: str,
) -> RequirementGenerator:
    dataset_path = _require_non_blank(
        config.dataset_path,
        factory=_REQ_GEN_FACTORY,
        strategy=strategy,
        field="config.dataset_path",
    )
    return DatasetGenerator(dataset_path=Path(dataset_path))


def _reject_hybrid_generator(
    _config: RequirementGeneratorConfig,
    strategy: str,
) -> RequirementGenerator:
    logger.warning(
        CLIENT_FACTORY_UNKNOWN_STRATEGY,
        factory=_REQ_GEN_FACTORY,
        strategy=strategy,
        reason="no_single_argument_factory",
    )
    msg = (
        "hybrid strategy has no single-argument factory; compose "
        "HybridGenerator directly with a tuple of (generator, weight) pairs"
    )
    raise UnknownStrategyError(msg)


def build_requirement_generator(
    config: RequirementGeneratorConfig,
    *,
    provider: CompletionProvider | None = None,
    model: NotBlankStr | None = None,
    cost_tracker: CostTracker | None = None,
) -> RequirementGenerator:
    """Construct a ``RequirementGenerator`` from configuration.

    Dispatches on ``config.strategy``:

    * ``template`` -> ``TemplateGenerator``
    * ``llm`` -> ``LLMGenerator`` (requires ``provider`` + ``model``;
      threads ``cost_tracker`` through so the chokepoint records each
      generated batch).
    * ``dataset`` -> ``DatasetGenerator`` (requires ``dataset_path``)
    * ``procedural`` -> ``ProceduralGenerator``
    * ``hybrid`` is **intentionally excluded** from factory dispatch:
      ``HybridGenerator`` composes multiple generators with weights
      and has no single-argument factory, so it must be constructed
      manually. Passing ``strategy="hybrid"`` here raises
      ``UnknownStrategyError``.

    Args:
        config: Strategy + per-strategy configuration.
        provider: LLM provider used by the ``llm`` strategy.
            Ignored by other strategies.
        model: Model identifier used by the ``llm`` strategy when
            the config does not pin one.  Ignored by other strategies.
        cost_tracker: Optional :class:`CostTracker` propagated to
            the ``llm`` strategy so cost-recording (`CostRecord`) is
            emitted on each generated batch.  Ignored by all other
            strategies (template / dataset / procedural don't talk
            to a provider).  When ``None``, the ``llm`` strategy
            still works -- the chokepoint just stays silent.
    """
    strategy = str(config.strategy)
    if strategy == "template":
        return _build_template_generator(config, strategy)
    if strategy == "llm":
        return _build_llm_generator(
            config,
            strategy,
            provider=provider,
            model=model,
            cost_tracker=cost_tracker,
        )
    if strategy == "dataset":
        return _build_dataset_generator(config, strategy)
    if strategy == "procedural":
        return ProceduralGenerator()
    if strategy == "hybrid":
        return _reject_hybrid_generator(config, strategy)
    logger.warning(
        CLIENT_FACTORY_UNKNOWN_STRATEGY,
        factory=_REQ_GEN_FACTORY,
        strategy=strategy,
        expected=sorted(_GENERATOR_STRATEGIES),
    )
    msg = (
        f"unknown requirement generator strategy {strategy!r}; "
        f"expected one of {sorted(_GENERATOR_STRATEGIES)}"
    )
    raise UnknownStrategyError(msg)


def build_feedback_strategy(
    config: FeedbackConfig,
    *,
    client_id: NotBlankStr,
) -> FeedbackStrategy:
    """Construct a ``FeedbackStrategy`` from configuration.

    Dispatches on ``config.strategy``:

    * ``binary`` -> ``BinaryFeedback``
    * ``scored`` -> ``ScoredFeedback``
    * ``criteria_check`` -> ``CriteriaCheckFeedback``
    * ``adversarial`` -> ``AdversarialFeedback``
    """
    strategy = str(config.strategy)
    if strategy == "binary":
        return BinaryFeedback(
            client_id=client_id,
            strictness_multiplier=config.strictness_multiplier,
        )
    if strategy == "scored":
        return ScoredFeedback(
            client_id=client_id,
            passing_score=config.passing_score,
            strictness_multiplier=config.strictness_multiplier,
        )
    if strategy == "criteria_check":
        return CriteriaCheckFeedback(client_id=client_id)
    if strategy == "adversarial":
        return AdversarialFeedback(client_id=client_id)
    logger.warning(
        CLIENT_FACTORY_UNKNOWN_STRATEGY,
        factory="feedback_strategy",
        strategy=strategy,
        expected=sorted(_FEEDBACK_STRATEGIES),
    )
    msg = (
        f"unknown feedback strategy {strategy!r}; "
        f"expected one of {sorted(_FEEDBACK_STRATEGIES)}"
    )
    raise UnknownStrategyError(msg)


def build_report_strategy(config: ReportConfig) -> ReportStrategy:
    """Construct a ``ReportStrategy`` from configuration.

    Dispatches on ``config.strategy`` in ``{summary, detailed,
    json_export, metrics_only}``.
    """
    strategy = str(config.strategy)
    if strategy == "summary":
        return SummaryReport()
    if strategy == "detailed":
        return DetailedReport()
    if strategy == "json_export":
        return JsonExportReport()
    if strategy == "metrics_only":
        return MetricsOnlyReport()
    logger.warning(
        CLIENT_FACTORY_UNKNOWN_STRATEGY,
        factory="report_strategy",
        strategy=strategy,
        expected=sorted(_REPORT_STRATEGIES),
    )
    msg = (
        f"unknown report strategy {strategy!r}; "
        f"expected one of {sorted(_REPORT_STRATEGIES)}"
    )
    raise UnknownStrategyError(msg)


def build_client_pool_strategy(
    config: ClientPoolConfig,
) -> ClientPoolStrategy:
    """Construct a ``ClientPoolStrategy`` from configuration.

    Dispatches on ``config.selection_strategy`` in ``{round_robin,
    weighted_random, domain_matched}``. Defaults to ``round_robin``.
    """
    strategy = str(config.selection_strategy)
    if strategy == "round_robin":
        return RoundRobinStrategy()
    if strategy == "weighted_random":
        return WeightedRandomStrategy()
    if strategy == "domain_matched":
        return DomainMatchedStrategy()
    logger.warning(
        CLIENT_FACTORY_UNKNOWN_STRATEGY,
        factory="client_pool_strategy",
        strategy=strategy,
        expected=sorted(_POOL_STRATEGIES),
    )
    msg = (
        f"unknown pool selection strategy {strategy!r}; "
        f"expected one of {sorted(_POOL_STRATEGIES)}"
    )
    raise UnknownStrategyError(msg)


def build_entry_point_strategy(
    adapter: NotBlankStr,
    *,
    project_id: NotBlankStr | None = None,
) -> EntryPointStrategy:
    """Construct an ``EntryPointStrategy`` from the adapter identifier.

    Dispatches on ``adapter`` in ``{direct, project, intake}``.

    Args:
        adapter: Discriminator identifier.
        project_id: Required when ``adapter == 'project'``.
    """
    if adapter == "direct":
        return DirectAdapter()
    if adapter == "project":
        if project_id is None:
            logger.warning(
                CLIENT_FACTORY_UNKNOWN_STRATEGY,
                factory="entry_point_strategy",
                adapter=adapter,
                missing="project_id",
            )
            msg = "project adapter requires project_id"
            raise UnknownStrategyError(msg)
        return ProjectAdapter(project_id=project_id)
    if adapter == "intake":
        return IntakeAdapter()
    logger.warning(
        CLIENT_FACTORY_UNKNOWN_STRATEGY,
        factory="entry_point_strategy",
        adapter=adapter,
        expected=sorted(_ENTRY_POINT_STRATEGIES),
    )
    msg = (
        f"unknown entry-point adapter {adapter!r}; "
        f"expected one of {sorted(_ENTRY_POINT_STRATEGIES)}"
    )
    raise UnknownStrategyError(msg)
