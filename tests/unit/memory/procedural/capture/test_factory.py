"""Tests for capture strategy factory."""

from typing import Literal
from unittest.mock import AsyncMock

import pytest

from synthorg.memory.procedural.capture.config import CaptureConfig
from synthorg.memory.procedural.capture.factory import build_capture_strategy
from synthorg.memory.procedural.capture.failure_capture import (
    FailureCaptureStrategy,
)
from synthorg.memory.procedural.capture.hybrid_capture import (
    HybridCaptureStrategy,
)
from synthorg.memory.procedural.capture.success_capture import (
    SuccessCaptureStrategy,
)
from synthorg.memory.procedural.models import ProceduralMemoryConfig
from synthorg.memory.procedural.proposer import ProceduralMemoryProposer
from synthorg.memory.procedural.success_proposer import SuccessMemoryProposer


def _build(
    type_: Literal["failure", "success", "hybrid"] = "hybrid",
) -> object:
    """Helper to build a capture strategy with mocked deps."""
    config = CaptureConfig(type=type_)
    proc_config = ProceduralMemoryConfig(
        enabled=True,
        model="test-model",
        temperature=0.3,
        max_tokens=1500,
        min_confidence=0.5,
    )
    return build_capture_strategy(
        config,
        failure_proposer=AsyncMock(spec=ProceduralMemoryProposer),
        success_proposer=AsyncMock(spec=SuccessMemoryProposer),
        procedural_config=proc_config,
    )


@pytest.mark.unit
class TestBuildCaptureStrategy:
    """build_capture_strategy dispatches on config type."""

    def test_failure_type(self) -> None:
        assert isinstance(_build("failure"), FailureCaptureStrategy)

    def test_success_type(self) -> None:
        assert isinstance(_build("success"), SuccessCaptureStrategy)

    def test_hybrid_type(self) -> None:
        assert isinstance(_build("hybrid"), HybridCaptureStrategy)

    def test_default_is_hybrid(self) -> None:
        assert isinstance(_build(), HybridCaptureStrategy)
