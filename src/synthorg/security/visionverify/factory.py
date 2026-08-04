"""Vision verifier factory.

Builds the configured :class:`VisionVerifier` from a
:class:`VisionVerifyConfig`. Returns ``None`` when the subsystem is
disabled so callers skip gate construction entirely (mirroring the
trust-strategy factory). A selected ``llm_vision`` kind missing its
provider / tier resolver fails fast with :class:`VisionVerifyConfigError`.
"""

from pathlib import Path

from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.vision_verify import VISION_VERIFY_CONFIG_ERROR
from synthorg.providers.protocol import ConnectionSelector
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
from synthorg.settings.model_ref import ModelRef

logger = get_logger(__name__)


def build_vision_verifier(
    config: VisionVerifyConfig,
    *,
    workspace: Path,
    connections: ConnectionSelector | None = None,
    model: ModelRef | None = None,
    cost_tracker: CostTrackerProtocol | None = None,
) -> VisionVerifier | None:
    """Build the configured verifier, or ``None`` when disabled.

    Args:
        config: The vision verify configuration.
        workspace: Workspace root holding screenshots (heuristic + llm).
        connections: Resolves the connection *model* names, required for
            ``llm_vision``.
        model: The operator's ``security.vision_verify_model`` pair,
            required for ``llm_vision``.
        cost_tracker: Optional cost tracker for the ``llm_vision`` call.

    Returns:
        A :class:`VisionVerifier`, or ``None`` when ``config.enabled``
        is ``False``.

    Raises:
        VisionVerifyConfigError: When ``llm_vision`` is selected without a
            bound pair to dispatch on.
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
                workspace=workspace,
                connections=connections,
                model=model,
                cost_tracker=cost_tracker,
            )


def _build_llm_vision(
    *,
    workspace: Path,
    connections: ConnectionSelector | None,
    model: ModelRef | None,
    cost_tracker: CostTrackerProtocol | None,
) -> VisionVerifier:
    """Construct the ``llm_vision`` verifier, failing fast on missing deps.

    A vision verdict decides whether a GUI deliverable passes, so the model
    that renders it is the operator's explicit choice. There is no tier
    lookup to guess from: a tier names no connection, and a connection
    carries its own credentials, endpoint and quota.

    Returns:
        A configured ``LLMVisionVerifier``.

    Raises:
        VisionVerifyConfigError: If no bound pair or connection selector is
            available to dispatch on.
    """
    if connections is None or model is None:
        msg = (
            "llm_vision verifier requires a bound security.vision_verify_model"
            " pair and a connection selector; pass both to"
            " build_vision_verifier()"
        )
        logger.error(
            VISION_VERIFY_CONFIG_ERROR,
            reason="missing_bound_model_or_connections",
            has_connections=connections is not None,
            has_model=model is not None,
        )
        raise VisionVerifyConfigError(msg)
    return LLMVisionVerifier(
        provider=connections(model.provider),
        model_id=NotBlankStr(model.model_id),
        workspace=workspace,
        cost_tracker=cost_tracker,
    )
