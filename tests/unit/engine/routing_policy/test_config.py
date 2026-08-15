"""Unit tests for the capability-policy configuration models."""

import pytest
from pydantic import ValidationError

from synthorg.core.completion_enums import ReasoningEffort
from synthorg.core.task_enums import Stakes
from synthorg.engine.routing_policy.config import (
    CapabilityPolicyConfig,
    StakesCapabilityFloor,
    StakesReasoning,
)

pytestmark = pytest.mark.unit


def test_shipped_thresholds_park_and_red_team_from_high() -> None:
    config = CapabilityPolicyConfig()
    assert config.park_min_stakes is Stakes.HIGH
    assert config.red_team_min_stakes is Stakes.HIGH


def test_default_requirement_maps_each_stakes_level() -> None:
    req = StakesCapabilityFloor()
    assert req.for_stakes(Stakes.LOW) == "basic"
    assert req.for_stakes(Stakes.NORMAL) == "capable"
    assert req.for_stakes(Stakes.HIGH) == "expert"
    assert req.for_stakes(Stakes.CRITICAL) == "expert"


def test_non_decreasing_requirement_is_accepted() -> None:
    req = StakesCapabilityFloor(
        low="basic", normal="basic", high="capable", critical="expert"
    )
    assert req.for_stakes(Stakes.NORMAL) == "basic"


def test_inverted_requirement_is_rejected() -> None:
    # low-stakes must not demand a stronger tier than high-stakes.
    with pytest.raises(ValidationError, match="non-decreasing"):
        StakesCapabilityFloor(low="expert", normal="capable", high="basic")


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
