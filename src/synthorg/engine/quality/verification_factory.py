"""Factory functions for verification decomposers and graders.

Builds a ``CriteriaDecomposer`` or ``RubricGrader`` instance from a
``VerificationConfig``.  The LLM variants require a
``CompletionProvider`` plus a ``tier_resolver`` callable that maps
``ModelTier`` to a concrete model identifier.  The factory is the only
place these dependencies cross from the provider layer into the quality
subsystem, keeping the quality modules decoupled from provider presets
and model-matching logic.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING

from synthorg.core.validation import require_non_blank
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

if TYPE_CHECKING:
    from synthorg.core.types import ModelTier, NotBlankStr
    from synthorg.providers.protocol import CompletionProvider

logger = get_logger(__name__)

TierResolver = Callable[["ModelTier"], "NotBlankStr"]


def _require_non_blank_model_id(
    value: str,
    *,
    component: str,
    tier: ModelTier,
) -> NotBlankStr:
    """Validate that a resolved model id is non-blank.

    The ``TierResolver`` protocol promises a ``NotBlankStr`` but it is a
    runtime callable; untrusted callers may still return a blank
    string. Fail here with a clear message instead of letting Pydantic
    raise from inside ``LLMCriteriaDecomposer`` / ``LLMRubricGrader``.

    Returns:
        ``value`` typed as :class:`NotBlankStr` when it is non-blank.

    Raises:
        ValueError: When ``value`` is blank or whitespace-only.
    """
    try:
        return require_non_blank(value, name=f"{component} tier_resolver({tier!r})")
    except ValueError as exc:
        logger.error(
            VERIFICATION_FACTORY_MISSING_PROVIDER,
            component=component,
            tier=str(tier),
            reason="blank_model_id",
        )
        msg = (
            f"{component} tier_resolver returned a blank model id for "
            f"tier {tier!r}; expected a non-blank string"
        )
        raise ValueError(msg) from exc


def build_decomposer(
    config: VerificationConfig,
    *,
    provider: CompletionProvider | None = None,
    tier_resolver: TierResolver | None = None,
) -> CriteriaDecomposer:
    """Build a criteria decomposer from config.

    Args:
        config: Verification configuration.
        provider: Required for ``DecomposerVariant.LLM``; ignored otherwise.
        tier_resolver: Maps ``config.decomposer_model_tier`` to a concrete
            model identifier.  Required for ``DecomposerVariant.LLM``.

    Returns:
        A ``CriteriaDecomposer`` instance.

    Raises:
        ValueError: If the variant is unknown, or if the LLM variant is
            requested without ``provider`` and ``tier_resolver``.
    """
    if config.decomposer == DecomposerVariant.IDENTITY:
        return IdentityCriteriaDecomposer()
    if config.decomposer == DecomposerVariant.LLM:
        if provider is None or tier_resolver is None:
            logger.error(
                VERIFICATION_FACTORY_MISSING_PROVIDER,
                variant=config.decomposer.value,
                component="decomposer",
                has_provider=provider is not None,
                has_tier_resolver=tier_resolver is not None,
            )
            msg = (
                "LLM decomposer requires a CompletionProvider and a "
                "tier_resolver; pass both to build_decomposer()"
            )
            raise ValueError(msg)
        model_id = _require_non_blank_model_id(
            tier_resolver(config.decomposer_model_tier),
            component="decomposer",
            tier=config.decomposer_model_tier,
        )
        return LLMCriteriaDecomposer(
            provider=provider,
            model_id=model_id,
            max_probes_per_criterion=config.max_probes_per_criterion,
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
    provider: CompletionProvider | None = None,
    tier_resolver: TierResolver | None = None,
    heuristic_grader_config: HeuristicGraderConfig | None = None,
) -> RubricGrader:
    """Build a rubric grader from config.

    Args:
        config: Verification configuration.
        provider: Required for ``GraderVariant.LLM``; ignored otherwise.
        tier_resolver: Maps ``config.grader_model_tier`` to a concrete
            model identifier.  Required for ``GraderVariant.LLM``.
        heuristic_grader_config: Optional :class:`HeuristicGraderConfig`
            with operator-tunable thresholds resolved from
            ``EngineBridgeConfig``. ``None`` falls back to grader
            defaults that mirror the historical hardcoded values.

    Returns:
        A ``RubricGrader`` instance.

    Raises:
        ValueError: If the variant is unknown, or if the LLM variant is
            requested without ``provider`` and ``tier_resolver``.
    """
    if config.grader == GraderVariant.HEURISTIC:
        return HeuristicRubricGrader(config=heuristic_grader_config)
    if config.grader == GraderVariant.LLM:
        if provider is None or tier_resolver is None:
            logger.error(
                VERIFICATION_FACTORY_MISSING_PROVIDER,
                variant=config.grader.value,
                component="grader",
                has_provider=provider is not None,
                has_tier_resolver=tier_resolver is not None,
            )
            msg = (
                "LLM grader requires a CompletionProvider and a "
                "tier_resolver; pass both to build_grader()"
            )
            raise ValueError(msg)
        model_id = _require_non_blank_model_id(
            tier_resolver(config.grader_model_tier),
            component="grader",
            tier=config.grader_model_tier,
        )
        return LLMRubricGrader(
            provider=provider,
            model_id=model_id,
            min_confidence_override=config.min_confidence_override,
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
