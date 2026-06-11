"""Vision verifier factory.

Builds the configured :class:`VisionVerifier` from a
:class:`VisionVerifyConfig`. Returns ``None`` when the subsystem is
disabled so callers skip gate construction entirely (mirroring the
trust-strategy factory). A selected ``llm_vision`` kind missing its
provider / tier resolver fails fast with :class:`VisionVerifyConfigError`.
"""

from collections.abc import Callable
from pathlib import Path

from synthorg.budget.tracker import CostTracker
from synthorg.core.types import ModelTier, NotBlankStr
from synthorg.providers.protocol import CompletionProvider
from synthorg.security.visionverify.config import (
    VisionVerifierKind,
    VisionVerifyConfig,
)
from synthorg.security.visionverify.errors import VisionVerifyConfigError
from synthorg.security.visionverify.protocol import VisionVerifier
from synthorg.security.visionverify.verifiers import (
    HeuristicVisionVerifier,
    LLMVisionVerifier,
    NoOpVisionVerifier,
)

type TierResolver = Callable[[ModelTier], str | None]


def build_vision_verifier(
    config: VisionVerifyConfig,
    *,
    workspace: Path,
    provider: CompletionProvider | None = None,
    tier_resolver: TierResolver | None = None,
    cost_tracker: CostTracker | None = None,
) -> VisionVerifier | None:
    """Build the configured verifier, or ``None`` when disabled.

    Args:
        config: The vision verify configuration.
        workspace: Workspace root holding screenshots (heuristic + llm).
        provider: Multimodal provider, required for ``llm_vision``.
        tier_resolver: Maps the configured model tier to a model id,
            required for ``llm_vision``.
        cost_tracker: Optional cost tracker for the ``llm_vision`` call.

    Returns:
        A :class:`VisionVerifier`, or ``None`` when ``config.enabled``
        is ``False``.

    Raises:
        VisionVerifyConfigError: When ``llm_vision`` is selected without
            a provider / tier resolver, or the tier resolves to no model.
    """
    if not config.enabled:
        return None
    match config.verifier_kind:
        case VisionVerifierKind.NOOP:
            return NoOpVisionVerifier()
        case VisionVerifierKind.HEURISTIC:
            return HeuristicVisionVerifier(workspace=workspace)
        case VisionVerifierKind.LLM_VISION:
            return _build_llm_vision(
                config,
                workspace=workspace,
                provider=provider,
                tier_resolver=tier_resolver,
                cost_tracker=cost_tracker,
            )


def _build_llm_vision(
    config: VisionVerifyConfig,
    *,
    workspace: Path,
    provider: CompletionProvider | None,
    tier_resolver: TierResolver | None,
    cost_tracker: CostTracker | None,
) -> VisionVerifier:
    """Construct the ``llm_vision`` verifier, failing fast on missing deps.

    Returns:
        A configured ``LLMVisionVerifier``.

    Raises:
        VisionVerifyConfigError: If the provider or tier resolver is
            missing, or the tier resolves to no model id.
    """
    if provider is None or tier_resolver is None:
        msg = (
            "llm_vision verifier requires a CompletionProvider and a "
            "tier_resolver; pass both to build_vision_verifier()"
        )
        raise VisionVerifyConfigError(msg)
    model_id = tier_resolver(config.model_tier)
    if not model_id or not model_id.strip():
        msg = f"llm_vision verifier tier {config.model_tier!r} resolved to no model id"
        raise VisionVerifyConfigError(msg, context={"tier": config.model_tier})
    return LLMVisionVerifier(
        provider=provider,
        model_id=NotBlankStr(model_id),
        workspace=workspace,
        cost_tracker=cost_tracker,
    )
