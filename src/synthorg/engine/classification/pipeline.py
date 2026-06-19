# module-kind: service
"""Error classification pipeline.

Orchestrates the detection of coordination errors from an execution
result using the configured error taxonomy.  Detectors are discovered
dynamically from the ``ErrorTaxonomyConfig.detectors`` dict and
dispatched via the ``Detector`` protocol.  The pipeline never raises
exceptions -- all errors are caught and logged.
"""

import copy
from collections.abc import Callable, Mapping
from types import MappingProxyType

from synthorg.budget.coordination_config import (
    DetectionScope,
    DetectorCategoryConfig,
    DetectorVariant,
    ErrorCategory,
    ErrorTaxonomyConfig,
)
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.engine.classification.budget_tracker import (
    ClassificationBudgetTracker,
)
from synthorg.engine.classification.composite import CompositeDetector
from synthorg.engine.classification.heuristic_detectors import (
    HeuristicContextOmissionDetector,
    HeuristicContradictionDetector,
    HeuristicCoordinationFailureDetector,
    HeuristicNumericalDriftDetector,
)
from synthorg.engine.classification.loaders import (
    SameTaskLoader,
    TaskTreeLoader,
)
from synthorg.engine.classification.models import (
    ClassificationResult,
    ErrorFinding,
)
from synthorg.engine.classification.protocol import (
    ClassificationSink,
    DetectionContext,
    Detector,
    ScopedContextLoader,
)
from synthorg.engine.classification.protocol_detectors import (
    AuthorityBreachDetector,
    DelegationProtocolDetector,
    ReviewPipelineProtocolDetector,
)
from synthorg.engine.classification.semantic_detectors import (
    SemanticContradictionDetector,
    SemanticCoordinationDetector,
    SemanticMissingReferenceDetector,
    SemanticNumericalVerificationDetector,
)
from synthorg.engine.loop_protocol import ExecutionResult
from synthorg.engine.timeout_enforcement import engine_timeout
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.classification import (
    CLASSIFICATION_COMPLETE,
    CLASSIFICATION_ERROR,
    CLASSIFICATION_FINDING,
    CLASSIFICATION_SINK_ERROR,
    CLASSIFICATION_SKIPPED,
    CLASSIFICATION_START,
    CONTEXT_LOADER_ERROR,
    DETECTOR_ERROR,
    DETECTOR_SCOPE_MISMATCH,
    DETECTOR_TIMEOUT,
)
from synthorg.persistence.task_protocol import TaskRepository
from synthorg.providers.protocol import CompletionProvider

logger = get_logger(__name__)


# ── Detector factory maps ──────────────────────────────────────

_HEURISTIC_FACTORIES: MappingProxyType[
    ErrorCategory,
    Callable[[], Detector],
] = MappingProxyType(
    copy.deepcopy(
        {
            ErrorCategory.LOGICAL_CONTRADICTION: HeuristicContradictionDetector,
            ErrorCategory.NUMERICAL_DRIFT: HeuristicNumericalDriftDetector,
            ErrorCategory.CONTEXT_OMISSION: HeuristicContextOmissionDetector,
            ErrorCategory.COORDINATION_FAILURE: HeuristicCoordinationFailureDetector,
        },
    ),
)

_PROTOCOL_FACTORIES: MappingProxyType[
    ErrorCategory,
    Callable[[], Detector],
] = MappingProxyType(
    copy.deepcopy(
        {
            ErrorCategory.DELEGATION_PROTOCOL_VIOLATION: DelegationProtocolDetector,
            ErrorCategory.REVIEW_PIPELINE_VIOLATION: ReviewPipelineProtocolDetector,
        },
    ),
)

_BEHAVIOR_FACTORIES: MappingProxyType[
    ErrorCategory,
    Callable[[], Detector],
] = MappingProxyType(
    copy.deepcopy(
        {
            ErrorCategory.AUTHORITY_BREACH_ATTEMPT: AuthorityBreachDetector,
        },
    ),
)

_SEMANTIC_FACTORIES: MappingProxyType[ErrorCategory, type] = MappingProxyType(
    copy.deepcopy(
        {
            ErrorCategory.LOGICAL_CONTRADICTION: SemanticContradictionDetector,
            ErrorCategory.NUMERICAL_DRIFT: SemanticNumericalVerificationDetector,
            ErrorCategory.CONTEXT_OMISSION: SemanticMissingReferenceDetector,
            ErrorCategory.COORDINATION_FAILURE: SemanticCoordinationDetector,
        },
    ),
)

_SIMPLE_FACTORIES: MappingProxyType[
    DetectorVariant,
    MappingProxyType[ErrorCategory, Callable[[], Detector]],
] = MappingProxyType(
    {
        DetectorVariant.HEURISTIC: _HEURISTIC_FACTORIES,
        DetectorVariant.PROTOCOL_CHECK: _PROTOCOL_FACTORIES,
        DetectorVariant.BEHAVIOR_CHECK: _BEHAVIOR_FACTORIES,
    },
)


# ── Detector construction ──────────────────────────────────────


def _build_detectors(
    config: ErrorTaxonomyConfig,
    *,
    provider: CompletionProvider | None = None,
    budget_tracker: ClassificationBudgetTracker | None = None,
) -> tuple[Detector, ...]:
    """Instantiate detectors from config.

    For each category, instantiates one detector per configured
    variant.  When multiple variants target the same category,
    wraps them in a ``CompositeDetector``.  Skips LLM variants
    when no provider is available.

    Returns:
        A tuple of :class:`Detector` instances (one per category,
        wrapped in :class:`CompositeDetector` when multiple variants
        target the same category).
    """
    detectors: list[Detector] = []

    for category, cat_config in config.detectors.items():
        variants = _build_variants(
            category,
            cat_config,
            config=config,
            provider=provider,
            budget_tracker=budget_tracker,
        )
        if len(variants) == 1:
            detectors.append(variants[0])
        elif len(variants) > 1:
            detectors.append(
                CompositeDetector(detectors=tuple(variants)),
            )

    return tuple(detectors)


def _build_variants(
    category: ErrorCategory,
    cat_config: DetectorCategoryConfig,
    *,
    config: ErrorTaxonomyConfig,
    provider: CompletionProvider | None,
    budget_tracker: ClassificationBudgetTracker | None,
) -> list[Detector]:
    """Build detector instances for a single category.

    Returns:
        A list of :class:`Detector` instances (one per configured
        variant); empty when no variant resolves to a factory.
    """
    variants: list[Detector] = []
    for variant in cat_config.variants:
        if variant == DetectorVariant.LLM_SEMANTIC:
            _maybe_add_semantic(
                variants,
                category,
                provider=provider,
                model_id=config.llm_provider_tier,
                budget_tracker=budget_tracker,
            )
        else:
            factory_map: Mapping[ErrorCategory, Callable[[], Detector]] = (
                _SIMPLE_FACTORIES.get(variant, {})
            )
            factory = factory_map.get(category)
            if factory is not None:
                variants.append(factory())
    return variants


def _maybe_add_semantic(
    variants: list[Detector],
    category: ErrorCategory,
    *,
    provider: CompletionProvider | None,
    model_id: str,
    budget_tracker: ClassificationBudgetTracker | None,
) -> None:
    """Add a semantic detector variant if provider is available."""
    if provider is None:
        logger.debug(
            DETECTOR_ERROR,
            detector=f"semantic({category.value})",
            reason="no provider configured",
        )
        return
    sem_cls = _SEMANTIC_FACTORIES.get(category)
    if sem_cls is not None:
        variants.append(
            sem_cls(
                provider=provider,
                model_id=model_id,
                budget_tracker=budget_tracker,
            ),
        )


def _select_loader(
    scope: DetectionScope,
    task_repo: TaskRepository | None,
) -> ScopedContextLoader | None:
    """Select a context loader for the requested detection scope.

    TASK_TREE detectors are skipped (``None`` returned) when no task
    repository is configured -- the previous behaviour silently fell
    back to :class:`SameTaskLoader`, which produced a context with
    ``scope=SAME_TASK``, causing TASK_TREE detectors to run against
    missing delegation/review data.  Skipping them instead keeps
    every detector aligned with its declared scope.

    Args:
        scope: Detection scope requested by a detector category.
        task_repo: Optional task repository supporting TASK_TREE
            enrichment.

    Returns:
        The loader to use, or ``None`` when TASK_TREE scope was
        requested but no task repository was provided.
    """
    if scope == DetectionScope.TASK_TREE:
        if task_repo is None:
            return None
        return TaskTreeLoader(task_repo=task_repo)
    return SameTaskLoader()


# ── Public API ─────────────────────────────────────────────────


async def classify_execution_errors(  # noqa: PLR0913
    execution_result: ExecutionResult,
    agent_id: NotBlankStr,
    task_id: NotBlankStr,
    *,
    config: ErrorTaxonomyConfig,
    task_repo: TaskRepository | None = None,
    provider: CompletionProvider | None = None,
    sinks: tuple[ClassificationSink, ...] = (),
) -> ClassificationResult | None:
    """Classify coordination errors from an execution result.

    Discovers detectors from ``config.detectors``, loads
    scope-appropriate context, runs detectors sequentially
    (concurrency happens inside ``CompositeDetector``), and
    dispatches results to registered sinks.

    Rate limiting is handled by the ``BaseCompletionProvider``
    internally -- semantic detectors no longer accept a separate
    rate limiter to avoid double-throttling.

    Returns ``None`` when the taxonomy is disabled.  Never raises;
    all exceptions except ``MemoryError``/``RecursionError`` are
    caught and logged as ``CLASSIFICATION_ERROR``.

    Args:
        execution_result: The completed execution result to analyse.
        agent_id: Agent that executed the task.
        task_id: Task that was executed.
        config: Error taxonomy configuration.
        task_repo: Optional task repository for TASK_TREE scope.
        provider: Optional LLM provider for semantic detectors.
        sinks: Downstream consumers to notify after classification.

    Returns:
        Classification result with findings, or ``None`` if disabled.
    """
    if not config.enabled:
        logger.debug(
            CLASSIFICATION_SKIPPED,
            agent_id=agent_id,
            task_id=task_id,
            reason="error taxonomy disabled",
        )
        return None

    execution_id = execution_result.context.execution_id
    logger.info(
        CLASSIFICATION_START,
        agent_id=agent_id,
        task_id=task_id,
        execution_id=execution_id,
        categories=tuple(c.value for c in config.categories),
    )

    result = await _classify_safely(
        execution_result,
        agent_id,
        task_id,
        execution_id=execution_id,
        config=config,
        task_repo=task_repo,
        provider=provider,
    )
    if result is None:
        return None

    await _dispatch_to_sinks(result, sinks, agent_id, task_id)
    return result


async def _classify_safely(  # noqa: PLR0913
    execution_result: ExecutionResult,
    agent_id: str,
    task_id: str,
    *,
    execution_id: str,
    config: ErrorTaxonomyConfig,
    task_repo: TaskRepository | None,
    provider: CompletionProvider | None,
) -> ClassificationResult | None:
    """Run the pipeline and catch all non-fatal errors.

    Returns:
        The :class:`ClassificationResult` from ``_run_pipeline``, or
        ``None`` when a non-critical exception is logged and swallowed.

    Raises:
        MemoryError: Re-raised unchanged after logging redacted
            context (interpreter-critical, never swallowed).
        RecursionError: Re-raised unchanged after logging redacted
            context (interpreter-critical, never swallowed).
    """
    try:
        return await _run_pipeline(
            execution_result,
            agent_id,
            task_id,
            execution_id=execution_id,
            config=config,
            task_repo=task_repo,
            provider=provider,
        )
    except (MemoryError, RecursionError) as exc:
        # Using ``logger.error`` (not ``logger.exception``) is
        # deliberate: structlog's exc-info processor serialises
        # traceback frame-locals into the event, leaking any
        # in-scope credential. Log redacted classification context
        # via ``safe_error_description`` and re-raise.
        log_exception_redacted(
            logger,
            CLASSIFICATION_ERROR,
            exc,
            agent_id=agent_id,
            task_id=task_id,
            severity="non_recoverable",
        )
        raise
    except Exception as exc:  # noqa: BLE001 -- best-effort: log and skip
        logger.warning(
            CLASSIFICATION_ERROR,
            agent_id=agent_id,
            task_id=task_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None


async def _dispatch_to_sinks(
    result: ClassificationResult,
    sinks: tuple[ClassificationSink, ...],
    agent_id: str,
    task_id: str,
) -> None:
    """Dispatch classification result to all registered sinks.

    Best-effort: individual sink errors are logged and swallowed.
    ``MemoryError`` and ``RecursionError`` always propagate.
    """
    for sink in sinks:
        try:
            await sink.on_classification(result)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                CLASSIFICATION_SINK_ERROR,
                agent_id=agent_id,
                task_id=task_id,
                sink=type(sink).__name__,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )


async def _run_pipeline(  # noqa: PLR0913
    execution_result: ExecutionResult,
    agent_id: str,
    task_id: str,
    *,
    execution_id: str,
    config: ErrorTaxonomyConfig,
    task_repo: TaskRepository | None,
    provider: CompletionProvider | None,
) -> ClassificationResult:
    """Build detectors, load contexts, run, and collect findings.

    Returns:
        A :class:`ClassificationResult` carrying the execution id,
        agent id, task id, the categories that were actually
        checked, and every :class:`ErrorFinding` collected from the
        detectors.
    """
    budget_tracker = ClassificationBudgetTracker(
        budget=config.classification_budget_per_task,
    )
    all_detectors = _build_detectors(
        config,
        provider=provider,
        budget_tracker=budget_tracker,
    )
    all_findings, checked_categories = await _run_detectors_by_scope(
        all_detectors,
        execution_result,
        agent_id,
        task_id,
        execution_id=execution_id,
        config=config,
        task_repo=task_repo,
    )

    for finding in all_findings:
        logger.info(
            CLASSIFICATION_FINDING,
            agent_id=agent_id,
            task_id=task_id,
            execution_id=execution_id,
            category=finding.category.value,
            severity=finding.severity.value,
            description=finding.description,
        )

    classification = ClassificationResult(
        execution_id=execution_id,
        agent_id=agent_id,
        task_id=task_id,
        categories_checked=tuple(sorted(checked_categories, key=lambda c: c.value)),
        findings=tuple(all_findings),
    )
    logger.info(
        CLASSIFICATION_COMPLETE,
        agent_id=agent_id,
        task_id=task_id,
        execution_id=execution_id,
        finding_count=classification.finding_count,
    )
    return classification


async def _run_detectors_by_scope(  # noqa: PLR0913
    all_detectors: tuple[Detector, ...],
    execution_result: ExecutionResult,
    agent_id: str,
    task_id: str,
    *,
    execution_id: str,
    config: ErrorTaxonomyConfig,
    task_repo: TaskRepository | None,
) -> tuple[list[ErrorFinding], set[ErrorCategory]]:
    """Group detectors by scope, load contexts, and run them.

    Returns:
        ``(findings, checked_categories)`` where ``findings`` is the
        flat list of :class:`ErrorFinding` from every executed
        detector and ``checked_categories`` is the subset of
        categories whose detector actually ran (excluding scopes
        with no loader, loader failures, or scope mismatches).
    """
    scope_detectors: dict[DetectionScope, list[Detector]] = {}
    for detector in all_detectors:
        cat_cfg = config.detectors[detector.category]
        scope_detectors.setdefault(cat_cfg.scope, []).append(detector)

    all_findings: list[ErrorFinding] = []
    checked_categories: set[ErrorCategory] = set()
    for scope, detectors in scope_detectors.items():
        loader = _select_loader(scope, task_repo)
        if loader is None:
            for detector in detectors:
                logger.warning(
                    CLASSIFICATION_SKIPPED,
                    agent_id=agent_id,
                    task_id=task_id,
                    execution_id=execution_id,
                    detector=type(detector).__name__,
                    scope=scope.value,
                    reason="TASK_TREE scope requested but no task_repo configured",
                )
            continue
        try:
            context = await loader.load(execution_result, agent_id, task_id)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            detector_names = [type(d).__name__ for d in detectors]
            logger.warning(
                CONTEXT_LOADER_ERROR,
                agent_id=agent_id,
                task_id=task_id,
                execution_id=execution_id,
                scope=scope.value,
                detectors=detector_names,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            continue
        for detector in detectors:
            if context.scope not in detector.supported_scopes:
                logger.warning(
                    DETECTOR_SCOPE_MISMATCH,
                    agent_id=agent_id,
                    task_id=task_id,
                    execution_id=execution_id,
                    detector=type(detector).__name__,
                    context_scope=context.scope.value,
                    supported_scopes=sorted(s.value for s in detector.supported_scopes),
                )
                continue
            findings = await _safe_detect(
                detector,
                context,
                agent_id,
                task_id,
                execution_id,
                timeout_seconds=config.detector_timeout_seconds,
            )
            all_findings.extend(findings)
            checked_categories.add(detector.category)
    return all_findings, checked_categories


async def _safe_detect(  # noqa: PLR0913
    detector: Detector,
    context: DetectionContext,
    agent_id: str,
    task_id: str,
    execution_id: str,
    *,
    timeout_seconds: float,
) -> tuple[ErrorFinding, ...]:
    """Run a single detector with isolation and a timeout.

    Re-raises ``MemoryError`` and ``RecursionError`` via
    :func:`reraise_critical`; catches and logs all other exceptions
    (including ``asyncio.TimeoutError``) without stopping the
    pipeline.

    Returns:
        The tuple of :class:`ErrorFinding` produced by the detector,
        or an empty tuple when the detector timed out or raised a
        non-critical exception.
    """
    try:
        async with engine_timeout(timeout_seconds):
            return await detector.detect(context)
    except TimeoutError:
        logger.warning(
            DETECTOR_TIMEOUT,
            agent_id=agent_id,
            task_id=task_id,
            execution_id=execution_id,
            detector=type(detector).__name__,
            timeout_seconds=timeout_seconds,
        )
        return ()
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            DETECTOR_ERROR,
            agent_id=agent_id,
            task_id=task_id,
            execution_id=execution_id,
            detector=type(detector).__name__,
            message_count=len(
                context.execution_result.context.conversation,
            ),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return ()
