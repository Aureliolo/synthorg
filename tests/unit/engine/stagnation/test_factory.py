"""Unit tests for the stagnation-detector selection factory."""

import pytest

from synthorg.engine.stagnation.detector import ToolRepetitionDetector
from synthorg.engine.stagnation.factory import create_stagnation_detector
from synthorg.engine.stagnation.models import (
    QualityErosionConfig,
    StagnationConfig,
    StagnationDetectionConfig,
)
from synthorg.engine.stagnation.quality_erosion_detector import QualityErosionDetector

pytestmark = pytest.mark.unit


class TestCreateStagnationDetector:
    def test_off_is_default_and_returns_none(self) -> None:
        assert create_stagnation_detector(StagnationDetectionConfig()) is None

    def test_tool_repetition_selection(self) -> None:
        config = StagnationDetectionConfig(strategy="tool_repetition")
        detector = create_stagnation_detector(config)
        assert isinstance(detector, ToolRepetitionDetector)
        assert detector.get_detector_type() == "tool_repetition"

    def test_quality_erosion_selection(self) -> None:
        config = StagnationDetectionConfig(strategy="quality_erosion")
        detector = create_stagnation_detector(config)
        assert isinstance(detector, QualityErosionDetector)
        assert detector.get_detector_type() == "quality_erosion"

    def test_quality_erosion_threads_config_params(self) -> None:
        config = StagnationDetectionConfig(
            strategy="quality_erosion",
            quality_erosion=QualityErosionConfig(threshold=0.8, window_size=20),
        )
        detector = create_stagnation_detector(config)
        assert isinstance(detector, QualityErosionDetector)
        assert detector.threshold == 0.8
        assert detector.window_size == 20

    def test_tool_repetition_threads_config(self) -> None:
        config = StagnationDetectionConfig(
            strategy="tool_repetition",
            tool_repetition=StagnationConfig(window_size=7),
        )
        detector = create_stagnation_detector(config)
        assert isinstance(detector, ToolRepetitionDetector)
        assert detector.config.window_size == 7
