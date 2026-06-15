"""Factory selecting the intra-loop stagnation detector from config.

The ``off`` strategy (default) returns ``None`` so the engine runs with
no detector, reproducing the historical boot exactly. ``tool_repetition``
and ``quality_erosion`` build the matching :class:`StagnationDetector`
from its co-located sub-config.
"""

from typing import assert_never

from synthorg.engine.stagnation.detector import ToolRepetitionDetector
from synthorg.engine.stagnation.models import StagnationDetectionConfig
from synthorg.engine.stagnation.protocol import StagnationDetector
from synthorg.engine.stagnation.quality_erosion_detector import QualityErosionDetector
from synthorg.observability import get_logger
from synthorg.observability.events.stagnation import STAGNATION_CHECK_PERFORMED

logger = get_logger(__name__)


def create_stagnation_detector(
    config: StagnationDetectionConfig,
) -> StagnationDetector | None:
    """Build the stagnation detector selected by ``config.strategy``.

    Args:
        config: Stagnation-detection configuration carrying the strategy
            discriminator and per-detector sub-configs.

    Returns:
        The selected :class:`StagnationDetector`, or ``None`` when the
        strategy is ``off`` (detection disabled).
    """
    match config.strategy:
        case "off":
            detector: StagnationDetector | None = None
        case "tool_repetition":
            detector = ToolRepetitionDetector(config.tool_repetition)
        case "quality_erosion":
            detector = QualityErosionDetector(
                threshold=config.quality_erosion.threshold,
                window_size=config.quality_erosion.window_size,
            )
        case _:  # pragma: no cover
            assert_never(config.strategy)
    logger.debug(
        STAGNATION_CHECK_PERFORMED,
        phase="detector_build",
        strategy=config.strategy,
        detector=detector.get_detector_type() if detector is not None else "none",
    )
    return detector
