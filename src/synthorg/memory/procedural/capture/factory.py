"""Factory for building capture strategies.

Constructs the appropriate capture strategy based on configuration,
wiring proposers and backends as needed.
"""

from synthorg.core.registry import StrategyRegistry
from synthorg.memory.procedural.capture.config import CaptureConfig  # noqa: TC001
from synthorg.memory.procedural.capture.failure_capture import FailureCaptureStrategy
from synthorg.memory.procedural.capture.hybrid_capture import HybridCaptureStrategy
from synthorg.memory.procedural.capture.protocol import CaptureStrategy  # noqa: TC001
from synthorg.memory.procedural.capture.success_capture import SuccessCaptureStrategy
from synthorg.memory.procedural.models import ProceduralMemoryConfig  # noqa: TC001
from synthorg.memory.procedural.proposer import ProceduralMemoryProposer  # noqa: TC001
from synthorg.memory.procedural.success_proposer import (
    SuccessMemoryProposer,  # noqa: TC001
)
from synthorg.observability import get_logger
from synthorg.observability.events.capture import CAPTURE_STRATEGY_BUILT

logger = get_logger(__name__)


def _build_failure(
    config: CaptureConfig,
    *,
    failure_proposer: ProceduralMemoryProposer,
    procedural_config: ProceduralMemoryConfig,
    **_unused: object,
) -> CaptureStrategy:
    del config  # quality threshold lives on success branch only
    logger.debug(CAPTURE_STRATEGY_BUILT, strategy_type="failure")
    return FailureCaptureStrategy(
        proposer=failure_proposer,
        config=procedural_config,
    )


def _build_success(
    config: CaptureConfig,
    *,
    success_proposer: SuccessMemoryProposer,
    procedural_config: ProceduralMemoryConfig,
    **_unused: object,
) -> CaptureStrategy:
    logger.debug(CAPTURE_STRATEGY_BUILT, strategy_type="success")
    return SuccessCaptureStrategy(
        proposer=success_proposer,
        config=procedural_config,
        min_quality_score=config.min_quality_score,
    )


def _build_hybrid(
    config: CaptureConfig,
    *,
    failure_proposer: ProceduralMemoryProposer,
    success_proposer: SuccessMemoryProposer,
    procedural_config: ProceduralMemoryConfig,
) -> CaptureStrategy:
    logger.debug(CAPTURE_STRATEGY_BUILT, strategy_type="hybrid")
    return HybridCaptureStrategy(
        failure_strategy=FailureCaptureStrategy(
            proposer=failure_proposer,
            config=procedural_config,
        ),
        success_strategy=SuccessCaptureStrategy(
            proposer=success_proposer,
            config=procedural_config,
            min_quality_score=config.min_quality_score,
        ),
    )


_CAPTURE_REGISTRY: StrategyRegistry[CaptureStrategy] = StrategyRegistry(
    {
        "failure": _build_failure,
        "success": _build_success,
        "hybrid": _build_hybrid,
    },
    kind="capture_strategy",
)


def build_capture_strategy(
    config: CaptureConfig,
    *,
    failure_proposer: ProceduralMemoryProposer,
    success_proposer: SuccessMemoryProposer,
    procedural_config: ProceduralMemoryConfig,
) -> CaptureStrategy:
    """Build a capture strategy based on configuration.

    Routes to the appropriate strategy factory based on the configured
    type. All strategies require both proposers to be pre-constructed.

    Args:
        config: Capture strategy configuration.
        failure_proposer: ProceduralMemoryProposer for failure analysis.
        success_proposer: SuccessMemoryProposer for success analysis.
        procedural_config: ProceduralMemoryConfig for general settings.

    Returns:
        A CaptureStrategy instance matching the configured type.

    Raises:
        StrategyFactoryNotFoundError: If ``config.type`` is not registered.
    """
    return _CAPTURE_REGISTRY.build(
        config.type,
        config,
        failure_proposer=failure_proposer,
        success_proposer=success_proposer,
        procedural_config=procedural_config,
    )
