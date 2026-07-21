"""Unit tests for the stakes-routing configuration models."""

import pytest
from pydantic import ValidationError

from synthorg.core.completion_enums import ReasoningEffort
from synthorg.core.task_enums import Stakes
from synthorg.engine.routing_policy.config import (
    StakesReasoning,
    StakesTierRequirement,
)

pytestmark = pytest.mark.unit


def test_default_requirement_maps_each_stakes_level() -> None:
    req = StakesTierRequirement()
    assert req.for_stakes(Stakes.LOW) == "small"
    assert req.for_stakes(Stakes.NORMAL) == "medium"
    assert req.for_stakes(Stakes.HIGH) == "large"
    assert req.for_stakes(Stakes.CRITICAL) == "large"


def test_non_decreasing_requirement_is_accepted() -> None:
    req = StakesTierRequirement(
        low="small", normal="small", high="medium", critical="large"
    )
    assert req.for_stakes(Stakes.NORMAL) == "small"


def test_inverted_requirement_is_rejected() -> None:
    # low-stakes must not demand a stronger tier than high-stakes.
    with pytest.raises(ValidationError, match="non-decreasing"):
        StakesTierRequirement(low="large", normal="medium", high="small")


def test_default_reasoning_maps_each_stakes_level() -> None:
    reasoning = StakesReasoning()
    assert reasoning.for_stakes(Stakes.LOW) is None
    assert reasoning.for_stakes(Stakes.NORMAL) == ReasoningEffort.LOW
    assert reasoning.for_stakes(Stakes.HIGH) == ReasoningEffort.MEDIUM
    assert reasoning.for_stakes(Stakes.CRITICAL) == ReasoningEffort.HIGH


def test_non_decreasing_reasoning_is_accepted() -> None:
    reasoning = StakesReasoning(
        low=None,
        normal=ReasoningEffort.LOW,
        high=ReasoningEffort.LOW,
        critical=ReasoningEffort.HIGH,
    )
    assert reasoning.for_stakes(Stakes.HIGH) == ReasoningEffort.LOW


def test_inverted_reasoning_is_rejected() -> None:
    # low-stakes must not request deeper reasoning than critical-stakes.
    with pytest.raises(ValidationError, match="non-decreasing"):
        StakesReasoning(
            low=ReasoningEffort.HIGH,
            normal=ReasoningEffort.LOW,
            critical=ReasoningEffort.MINIMAL,
        )
