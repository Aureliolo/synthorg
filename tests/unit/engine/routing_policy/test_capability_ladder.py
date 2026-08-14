"""Unit tests for the capability-ladder helpers."""

import pytest

from synthorg.core.types import CapabilityLevel
from synthorg.engine.routing_policy.capability_ladder import (
    LADDER,
    bump_one,
    meets_required,
    rank,
)


@pytest.mark.unit
class TestMeetsRequired:
    """A candidate meets a requirement when it is at least as strong."""

    @pytest.mark.parametrize(
        ("candidate", "required", "expected"),
        [
            ("basic", "basic", True),
            ("expert", "basic", True),
            ("capable", "expert", False),
            ("basic", "capable", False),
            ("expert", "expert", True),
        ],
    )
    def test_meets(
        self, candidate: CapabilityLevel, required: CapabilityLevel, expected: bool
    ) -> None:
        assert meets_required(candidate, required) is expected


@pytest.mark.unit
class TestTierRank:
    """Weakest-first rank: basic=0, capable=1, expert=2."""

    @pytest.mark.parametrize(
        ("capability", "expected"),
        [("basic", 0), ("capable", 1), ("expert", 2)],
    )
    def test_rank(self, capability: CapabilityLevel, expected: int) -> None:
        assert rank(capability) == expected

    def test_ladder_is_weakest_first(self) -> None:
        assert LADDER == ("basic", "capable", "expert")


@pytest.mark.unit
class TestBumpOne:
    """``bump_one`` steps up one rung and saturates at the strongest."""

    @pytest.mark.parametrize(
        ("rung", "expected"),
        [
            ("basic", "capable"),
            ("capable", "expert"),
            ("expert", "expert"),
        ],
    )
    def test_bump(self, rung: CapabilityLevel, expected: CapabilityLevel) -> None:
        assert bump_one(rung) == expected
