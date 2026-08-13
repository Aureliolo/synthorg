"""Unit tests for the capability-ladder helpers."""

import pytest

from synthorg.core.types import CapabilityLevel
from synthorg.engine.routing_policy.capability_ladder import (
    LADDER,
    bump_one,
    meets_required,
    rank,
    stronger,
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
    """Cheapest-first rank: small=0, medium=1, large=2."""

    @pytest.mark.parametrize(
        ("capability", "expected"),
        [("basic", 0), ("capable", 1), ("expert", 2)],
    )
    def test_rank(self, capability: CapabilityLevel, expected: int) -> None:
        assert rank(capability) == expected

    def test_ladder_is_weakest_first(self) -> None:
        assert LADDER == ("basic", "capable", "expert")


@pytest.mark.unit
class TestStronger:
    """``stronger`` returns the more capable rung, order-independent."""

    @pytest.mark.parametrize(
        ("a", "b", "expected"),
        [
            ("basic", "expert", "expert"),
            ("expert", "basic", "expert"),
            ("basic", "capable", "capable"),
            ("capable", "basic", "capable"),
            ("capable", "expert", "expert"),
            ("capable", "capable", "capable"),
            ("expert", "expert", "expert"),
            ("basic", "basic", "basic"),
        ],
    )
    def test_returns_stronger(
        self,
        a: CapabilityLevel,
        b: CapabilityLevel,
        expected: CapabilityLevel,
    ) -> None:
        assert stronger(a, b) == expected


@pytest.mark.unit
class TestBumpOne:
    """``bump_one`` steps up one tier and saturates at the strongest."""

    @pytest.mark.parametrize(
        ("tier", "expected"),
        [
            ("basic", "capable"),
            ("capable", "expert"),
            ("expert", "expert"),
        ],
    )
    def test_bump(self, tier: CapabilityLevel, expected: CapabilityLevel) -> None:
        assert bump_one(tier) == expected
