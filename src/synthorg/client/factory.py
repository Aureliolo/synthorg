"""Factory functions for client-simulation strategies.

Each strategy family has a configuration discriminator
(``config.strategy`` / ``config.selection_strategy``) that selects a
concrete implementation. The factories below turn those strings into
instances, failing loudly on unknown values so misconfiguration never
silently falls through to a no-op default.
"""

from pathlib import Path
from typing import ClassVar, NoReturn

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
from synthorg.budget.tracker import CostTracker  # noqa: E402
from synthorg.client.config import (  # noqa: E402
    ClientPoolConfig,
    FeedbackConfig,
    IntakeConfig,
    ReportConfig,
    RequirementGeneratorConfig,
)
from synthorg.client.protocols import (  # noqa: E402
    ClientPoolStrategy,
    EntryPointStrategy,
    FeedbackStrategy,
    ReportStrategy,
    RequirementGenerator,
)
from synthorg.engine.intake.protocol import IntakeStrategy  # noqa: E402
from synthorg.engine.task_engine import TaskEngine  # noqa: E402
from synthorg.providers.protocol import CompletionProvider  # noqa: E402

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
_INTAKE_STRATEGIES: frozenset[str] = frozenset({"direct", "agent"})
_INTAKE_FACTORY = "intake_strategy"


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


def _raise_unknown_strategy(
    *,
    label: str,
    factory: str,
    strategy: str,
    expected: frozenset[str],
) -> NoReturn:
    """Log and raise :class:`UnknownStrategyError` for a bad discriminator."""
    logger.warning(
        CLIENT_FACTORY_UNKNOWN_STRATEGY,
        factory=factory,
        strategy=strategy,
        expected=sorted(expected),
    )
    msg = f"unknown {label} strategy {strategy!r}; expected one of {sorted(expected)}"
    raise UnknownStrategyError(msg)


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
    """Construct a ``RequirementGenerator`` from ``config.strategy``.

    ``template`` -> ``TemplateGenerator``; ``llm`` -> ``LLMGenerator``
    (needs ``provider`` + ``model``; ``cost_tracker`` threaded through
    so the chokepoint records each batch); ``dataset`` ->
    ``DatasetGenerator`` (needs ``dataset_path``); ``procedural`` ->
    ``ProceduralGenerator``. ``hybrid`` is intentionally excluded
    (``HybridGenerator`` composes weighted generators and has no
    single-argument factory); passing it raises
    :class:`UnknownStrategyError`.
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
    _raise_unknown_strategy(
        label="requirement generator",
        factory=_REQ_GEN_FACTORY,
        strategy=strategy,
        expected=_GENERATOR_STRATEGIES,
    )


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


def _build_agent_intake(
    config: IntakeConfig,
    *,
    task_engine: TaskEngine,
    provider: CompletionProvider | None,
    cost_tracker: CostTracker | None,
    default_project: NotBlankStr,
) -> IntakeStrategy:
    """Build the LLM-triage ``AgentIntake`` (needs provider + model)."""
    from synthorg.engine.intake import AgentIntake  # noqa: PLC0415

    if provider is None:
        logger.warning(
            CLIENT_FACTORY_UNKNOWN_STRATEGY,
            factory=_INTAKE_FACTORY,
            strategy="agent",
            missing="provider",
        )
        msg = "agent intake strategy requires a completion provider"
        raise UnknownStrategyError(msg)
    model = _require_non_blank(
        config.model,
        factory=_INTAKE_FACTORY,
        strategy="agent",
        field="model",
    )
    return AgentIntake(
        task_engine=task_engine,
        provider=provider,
        model=NotBlankStr(model),
        project=default_project,
        cost_tracker=cost_tracker,
    )


def build_intake_strategy(
    config: IntakeConfig,
    *,
    task_engine: TaskEngine,
    default_project: NotBlankStr,
    provider: CompletionProvider | None = None,
    cost_tracker: CostTracker | None = None,
) -> IntakeStrategy:
    """Construct an ``IntakeStrategy`` from ``config.strategy``.

    ``direct`` -> :class:`DirectIntake` (no LLM). ``agent`` ->
    :class:`AgentIntake` (LLM triage; needs ``provider`` and a
    non-blank ``config.model``). Misconfiguration fails loudly with
    :class:`UnknownStrategyError`; the caller decides whether to
    degrade. ``cost_tracker`` is threaded into ``AgentIntake``.
    ``default_project`` is the project the strategy files created
    tasks into; the real work-entry adapter stamps the same value on
    the ``WorkItem`` so the pipeline's project-existence check and the
    created task agree.
    """
    # Lazy: synthorg.engine.intake pulls the provider/prompt-safety
    # graph; keep it off the synthorg.client package-import path.
    from synthorg.engine.intake import DirectIntake  # noqa: PLC0415

    strategy = config.strategy
    if strategy == "direct":
        return DirectIntake(task_engine=task_engine, project=default_project)
    if strategy == "agent":
        return _build_agent_intake(
            config,
            task_engine=task_engine,
            provider=provider,
            cost_tracker=cost_tracker,
            default_project=default_project,
        )
    _raise_unknown_strategy(
        label="intake",
        factory=_INTAKE_FACTORY,
        strategy=strategy,
        expected=_INTAKE_STRATEGIES,
    )
