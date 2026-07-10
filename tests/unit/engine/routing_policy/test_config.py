"""Unit tests for the stakes-routing configuration models."""

import pytest
from pydantic import ValidationError

from synthorg.core.task_enums import Stakes
from synthorg.engine.routing_policy.config import StakesTierRequirement

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
