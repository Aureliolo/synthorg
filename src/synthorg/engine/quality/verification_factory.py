"""Factory functions for verification decomposers and graders.

Builds a ``CriteriaDecomposer`` or ``RubricGrader`` instance from a
``VerificationConfig``.  The LLM variants require the operator's own
``(provider, model)`` pair and a selector that resolves the connection it
names, because a provider is a registered connection carrying its own
credentials, endpoint and quota.  The factory is the only place these
dependencies cross from the provider layer into the quality subsystem,
keeping the quality modules decoupled from provider presets and
model-matching logic.
"""

from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.types import NotBlankStr
from synthorg.engine.quality.decomposer_protocol import (
    CriteriaDecomposer,
)
from synthorg.engine.quality.decomposers.identity import (
    IdentityCriteriaDecomposer,
)
from synthorg.engine.quality.decomposers.llm import LLMCriteriaDecomposer
from synthorg.engine.quality.grader_protocol import RubricGrader
from synthorg.engine.quality.graders.heuristic import (
    HeuristicGraderConfig,
    HeuristicRubricGrader,
)
from synthorg.engine.quality.graders.llm import LLMRubricGrader
from synthorg.engine.quality.verification_config import (
    DecomposerVariant,
    GraderVariant,
    VerificationConfig,
)
from synthorg.observability import get_logger
from synthorg.observability.events.verification import (
    VERIFICATION_FACTORY_MISSING_PROVIDER,
    VERIFICATION_FACTORY_UNKNOWN_DECOMPOSER,
    VERIFICATION_FACTORY_UNKNOWN_GRADER,
)
from synthorg.providers.protocol import ConnectionSelector
from synthorg.settings.model_ref import ModelRef

logger = get_logger(__name__)


def build_decomposer(
    config: VerificationConfig,
    *,
    connections: ConnectionSelector | None = None,
    model: ModelRef | None = None,
    cost_tracker: CostTrackerProtocol | None = None,
) -> CriteriaDecomposer:
    """Build a criteria decomposer from config.

    Args:
        config: Verification configuration.
        connections: Resolves the connection *model* names; required for
            ``DecomposerVariant.LLM``, ignored otherwise.
        model: The operator's decomposer ``(provider, model)`` pair;
            required for ``DecomposerVariant.LLM``, ignored otherwise.
        cost_tracker: Records the verification LLM's token spend; forwarded
            to the LLM decomposer so its probes are not a cost blind spot.

    Returns:
        A ``CriteriaDecomposer`` instance.

    Raises:
        ValueError: If the variant is unknown, or if the LLM variant is
            requested without a bound pair to dispatch on.
    """
    if config.decomposer == DecomposerVariant.IDENTITY:
        return IdentityCriteriaDecomposer()
    if config.decomposer == DecomposerVariant.LLM:
        if connections is None or model is None:
            logger.error(
                VERIFICATION_FACTORY_MISSING_PROVIDER,
                variant=config.decomposer.value,
                component="decomposer",
                has_connections=connections is not None,
                has_model=model is not None,
            )
            msg = (
                "LLM decomposer requires a bound (provider, model) pair and a "
                "connection selector; pass both to build_decomposer()"
            )
            raise ValueError(msg)
        return LLMCriteriaDecomposer(
            provider=connections(model.provider),
            model_id=NotBlankStr(model.model_id),
            max_probes_per_criterion=config.max_probes_per_criterion,
            cost_tracker=cost_tracker,
        )

    # Reachable when a tampered config holds an unknown discriminator
    # (e.g. model_copy(update={"decomposer": "nonexistent"})).
    valid = sorted(v.value for v in DecomposerVariant)  # type: ignore[unreachable]
    logger.error(
        VERIFICATION_FACTORY_UNKNOWN_DECOMPOSER,
        variant=str(config.decomposer),
        valid=valid,
    )
    msg = f"Unknown decomposer variant {config.decomposer!r}, valid: {valid}"
    raise ValueError(msg)


def build_grader(
    config: VerificationConfig,
    *,
    connections: ConnectionSelector | None = None,
    model: ModelRef | None = None,
    heuristic_grader_config: HeuristicGraderConfig | None = None,
    cost_tracker: CostTrackerProtocol | None = None,
) -> RubricGrader:
    """Build a rubric grader from config.

    Args:
        config: Verification configuration.
        connections: Resolves the connection *model* names; required for
            ``GraderVariant.LLM``, ignored otherwise.
        model: The operator's grader ``(provider, model)`` pair; required
            for ``GraderVariant.LLM``, ignored otherwise.
        heuristic_grader_config: Optional :class:`HeuristicGraderConfig`
            with operator-tunable thresholds resolved from
            ``EngineBridgeConfig``. ``None`` falls back to grader
            defaults that mirror the historical hardcoded values.
        cost_tracker: Records the verification LLM's token spend; forwarded
            to the LLM grader so its grading calls are not a cost blind spot.

    Returns:
        A ``RubricGrader`` instance.

    Raises:
        ValueError: If the variant is unknown, or if the LLM variant is
            requested without a bound pair to dispatch on.
    """
    if config.grader == GraderVariant.HEURISTIC:
        return HeuristicRubricGrader(config=heuristic_grader_config)
    if config.grader == GraderVariant.LLM:
        if connections is None or model is None:
            logger.error(
                VERIFICATION_FACTORY_MISSING_PROVIDER,
                variant=config.grader.value,
                component="grader",
                has_connections=connections is not None,
                has_model=model is not None,
            )
            msg = (
                "LLM grader requires a bound (provider, model) pair and a "
                "connection selector; pass both to build_grader()"
            )
            raise ValueError(msg)
        return LLMRubricGrader(
            provider=connections(model.provider),
            model_id=NotBlankStr(model.model_id),
            min_confidence_override=config.min_confidence_override,
            cost_tracker=cost_tracker,
        )

    # Reachable when a tampered config holds an unknown discriminator
    # (e.g. model_copy(update={"grader": "nonexistent"})).
    valid = sorted(v.value for v in GraderVariant)  # type: ignore[unreachable]
    logger.error(
        VERIFICATION_FACTORY_UNKNOWN_GRADER,
        variant=str(config.grader),
        valid=valid,
    )
    msg = f"Unknown grader variant {config.grader!r}, valid: {valid}"
    raise ValueError(msg)
