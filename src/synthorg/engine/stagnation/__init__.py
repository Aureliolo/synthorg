"""Intra-loop stagnation detection.

Detects repetitive tool-call patterns within execution loops and
intervenes with corrective prompt injection or early termination.

Re-exports the public API: config, models, protocol, and default
detector implementation.
"""

from synthorg.engine.stagnation.detector import ToolRepetitionDetector
from synthorg.engine.stagnation.factory import create_stagnation_detector
from synthorg.engine.stagnation.models import (
    QualityErosionConfig,
    StagnationConfig,
    StagnationDetectionConfig,
    StagnationResult,
    StagnationVerdict,
)
from synthorg.engine.stagnation.protocol import StagnationDetector
from synthorg.engine.stagnation.quality_erosion_detector import QualityErosionDetector

__all__ = [
    "QualityErosionConfig",
    "QualityErosionDetector",
    "StagnationConfig",
    "StagnationDetectionConfig",
    "StagnationDetector",
    "StagnationResult",
    "StagnationVerdict",
    "ToolRepetitionDetector",
    "create_stagnation_detector",
]
