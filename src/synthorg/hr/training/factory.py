"""Factory for building the training service from config."""

from typing import TYPE_CHECKING

from synthorg.core.registry import StrategyRegistry
from synthorg.core.validation import coerce_positive_int
from synthorg.hr.training.models import ContentType
from synthorg.observability import get_logger
from synthorg.observability.events.training import HR_TRAINING_CONFIG_INVALID

if TYPE_CHECKING:
    from synthorg.approval.protocol import ApprovalStoreProtocol
    from synthorg.hr.performance.tracker import PerformanceTracker
    from synthorg.hr.registry import AgentRegistryService
    from synthorg.hr.training.config import TrainingConfig
    from synthorg.hr.training.protocol import (
        ContentExtractor,
        CurationStrategy,
        SourceSelector,
        TrainingGuard,
    )
    from synthorg.hr.training.service import TrainingService
    from synthorg.memory.protocol import MemoryBackend
    from synthorg.providers.protocol import CompletionProvider
    from synthorg.tools.invocation_tracker import ToolInvocationTracker

logger = get_logger(__name__)


def _coerce_positive_int(
    value: object,
    *,
    field_name: str,
    default: int,
) -> int:
    """HR-training wrapper that adds ``HR_TRAINING_CONFIG_INVALID`` logging.

    Delegates the parsing rules (bool rejection, int / int-string acceptance,
    non-positive rejection) to
    :func:`synthorg.core.validation.coerce_positive_int`. On failure emits
    ``HR_TRAINING_CONFIG_INVALID`` with the original exception type before
    re-raising the underlying ``TypeError`` / ``ValueError``.

    Returns:
        Result of type ``int``.

    Raises:
        TypeError: If an argument has an unexpected type.
        ValueError: If an argument fails domain validation.
    """
    try:
        return coerce_positive_int(value, name=field_name, default=default)
    except (TypeError, ValueError) as exc:
        logger.warning(
            HR_TRAINING_CONFIG_INVALID,
            field=field_name,
            expected="positive_int",
            value_type=type(value).__name__,
            value_repr=repr(value),
            error_type=type(exc).__name__,
        )
        raise


def build_training_service(  # noqa: PLR0913
    config: TrainingConfig,
    *,
    memory_backend: MemoryBackend,
    tracker: PerformanceTracker,
    registry: AgentRegistryService,
    approval_store: ApprovalStoreProtocol,
    tool_tracker: ToolInvocationTracker,
    provider: CompletionProvider | None = None,
) -> TrainingService:
    """Build a fully wired ``TrainingService`` from configuration.

    Args:
        config: Training configuration.
        memory_backend: Memory backend.
        tracker: Performance tracker.
        registry: Agent registry.
        approval_store: Approval store for review gate.
        tool_tracker: Tool invocation tracker.
        provider: LLM completion provider (optional).

    Returns:
        Configured training service.
    """
    from synthorg.hr.training.service import (  # noqa: PLC0415
        TrainingService,
    )

    selector = _build_selector(config, tracker=tracker, registry=registry)
    extractors = _build_extractors(
        config,
        memory_backend=memory_backend,
        tool_tracker=tool_tracker,
    )
    curation = _build_curation(config, provider=provider)
    guards = _build_guards(config, approval_store=approval_store)

    return TrainingService(
        selector=selector,
        extractors=extractors,
        curation=curation,
        guards=guards,
        memory_backend=memory_backend,
        training_namespace=str(config.training_namespace),
        training_tags=tuple(str(t) for t in config.training_tags),
    )


def _build_role_top_performers(
    config: TrainingConfig,
    *,
    tracker: PerformanceTracker,
    registry: AgentRegistryService,
) -> SourceSelector:
    """Build role top performers.

    Returns:
        Result of type ``SourceSelector``.
    """
    from synthorg.hr.training.source_selectors.role_top_performers import (  # noqa: PLC0415
        RoleTopPerformers,
    )

    top_n = _coerce_positive_int(
        config.source_selector_config.get("top_n"),
        field_name="source_selector_config.top_n",
        default=3,
    )
    return RoleTopPerformers(
        registry=registry,
        tracker=tracker,
        top_n=top_n,
    )


def _build_department_diversity(
    config: TrainingConfig,
    *,
    tracker: PerformanceTracker,
    registry: AgentRegistryService,
) -> SourceSelector:
    """Build department diversity.

    Returns:
        Result of type ``SourceSelector``.
    """
    del config  # discriminator-only branch, no config fields needed
    from synthorg.hr.training.source_selectors.department_diversity import (  # noqa: PLC0415
        DepartmentDiversitySampling,
    )

    return DepartmentDiversitySampling(
        registry=registry,
        tracker=tracker,
    )


_SELECTOR_REGISTRY: StrategyRegistry[SourceSelector] = StrategyRegistry(
    {
        "role_top_performers": _build_role_top_performers,
        "department_diversity": _build_department_diversity,
    },
    kind="training_selector",
)


def _build_selector(
    config: TrainingConfig,
    *,
    tracker: PerformanceTracker,
    registry: AgentRegistryService,
) -> SourceSelector:
    """Build source selector from config.

    Note:
        The ``user_curated`` selector type is intentionally not
        available in config: user-curated sources are passed via
        ``TrainingPlan.override_sources`` which the service uses
        directly without routing through a selector.

    Returns:
        Result of type ``SourceSelector``.
    """
    return _SELECTOR_REGISTRY.build(
        str(config.source_selector_type),
        config,
        tracker=tracker,
        registry=registry,
    )


def _build_extractors(
    config: TrainingConfig,  # noqa: ARG001
    *,
    memory_backend: MemoryBackend,
    tool_tracker: ToolInvocationTracker,
) -> dict[ContentType, ContentExtractor]:
    """Build extractors for all content types.

    Returns:
        Mapping from ``ContentType`` to ``ContentExtractor``.
    """
    from synthorg.hr.training.extractors.procedural import (  # noqa: PLC0415
        ProceduralMemoryExtractor,
    )
    from synthorg.hr.training.extractors.semantic import (  # noqa: PLC0415
        SemanticMemoryExtractor,
    )
    from synthorg.hr.training.extractors.tool_patterns import (  # noqa: PLC0415
        ToolPatternExtractor,
    )

    return {
        ContentType.PROCEDURAL: ProceduralMemoryExtractor(
            backend=memory_backend,
        ),
        ContentType.SEMANTIC: SemanticMemoryExtractor(
            backend=memory_backend,
        ),
        ContentType.TOOL_PATTERNS: ToolPatternExtractor(
            tracker=tool_tracker,
        ),
    }


def _build_relevance_curation(
    config: TrainingConfig,
    *,
    provider: CompletionProvider | None,
) -> CurationStrategy:
    """Build relevance curation.

    Returns:
        Result of type ``CurationStrategy``.
    """
    del provider  # heuristic curation does not need an LLM
    from synthorg.hr.training.curation.relevance import (  # noqa: PLC0415
        RelevanceScoreCuration,
    )

    top_k = _coerce_positive_int(
        config.curation_strategy_config.get("top_k"),
        field_name="curation_strategy_config.top_k",
        default=50,
    )
    return RelevanceScoreCuration(top_k=top_k)


def _build_llm_curation(
    config: TrainingConfig,
    *,
    provider: CompletionProvider | None,
) -> CurationStrategy:
    """Build llm curation.

    Returns:
        Result of type ``CurationStrategy``.
    """
    from synthorg.hr.training.curation.llm_curated import (  # noqa: PLC0415
        LLMCurated,
    )

    top_k = _coerce_positive_int(
        config.curation_strategy_config.get("top_k"),
        field_name="curation_strategy_config.top_k",
        default=50,
    )
    return LLMCurated(provider=provider, top_k=top_k)


_CURATION_REGISTRY: StrategyRegistry[CurationStrategy] = StrategyRegistry(
    {
        "relevance": _build_relevance_curation,
        "llm_curated": _build_llm_curation,
    },
    kind="training_curation",
)


def _build_curation(
    config: TrainingConfig,
    *,
    provider: CompletionProvider | None,
) -> CurationStrategy:
    """Build curation strategy from config.

    Returns:
        Result of type ``CurationStrategy``.
    """
    return _CURATION_REGISTRY.build(
        str(config.curation_strategy_type),
        config,
        provider=provider,
    )


def _build_guards(
    config: TrainingConfig,
    *,
    approval_store: ApprovalStoreProtocol,
) -> tuple[TrainingGuard, ...]:
    """Build guard chain from config.

    Always includes SanitizationGuard first (mandatory).

    Returns:
        Tuple of ``TrainingGuard``.
    """
    from synthorg.hr.training.guards.review_gate import (  # noqa: PLC0415
        ReviewGateGuard,
    )
    from synthorg.hr.training.guards.sanitization import (  # noqa: PLC0415
        SanitizationGuard,
    )
    from synthorg.hr.training.guards.volume_cap import (  # noqa: PLC0415
        VolumeCapGuard,
    )

    guards: list[TrainingGuard] = [
        SanitizationGuard(
            max_length=config.sanitization_max_length,
        ),
        VolumeCapGuard(),
        ReviewGateGuard(approval_store=approval_store),
    ]
    return tuple(guards)
