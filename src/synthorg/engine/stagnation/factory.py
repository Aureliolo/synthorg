# module-kind: code
"""Factory selecting the intra-loop stagnation detector from config.

The ``off`` strategy (default) returns ``None`` so the engine runs with
no detector, reproducing the historical boot exactly. ``tool_repetition``
and ``quality_erosion`` build the matching :class:`StagnationDetector`
from its co-located sub-config. Construction is silent (the boot wiring
that calls this factory is logged on the startup path); the runtime
``STAGNATION_CHECK_PERFORMED`` event is reserved for actual intra-loop
checks, not detector construction.
"""

from typing import assert_never

from synthorg.engine.stagnation.detector import ToolRepetitionDetector
from synthorg.engine.stagnation.models import StagnationDetectionConfig
from synthorg.engine.stagnation.protocol import StagnationDetector
from synthorg.engine.stagnation.quality_erosion_detector import QualityErosionDetector


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
            return None
        case "tool_repetition":
            return ToolRepetitionDetector(config.tool_repetition)
        case "quality_erosion":
            return QualityErosionDetector(
                threshold=config.quality_erosion.threshold,
                window_size=config.quality_erosion.window_size,
            )
        case _:  # pragma: no cover
            assert_never(config.strategy)
