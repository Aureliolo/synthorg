"""Tests for delegation depth check."""

import pytest

from synthorg.communication.loop_prevention.depth import (
    check_delegation_depth,
)

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestCheckDelegationDepth:
    def test_empty_chain_passes(self) -> None:
        result = check_delegation_depth((), max_depth=5)
        assert result.passed is True
        assert result.mechanism == "max_depth"

    def test_chain_below_limit_passes(self) -> None:
        result = check_delegation_depth(("a", "b"), max_depth=5)
        assert result.passed is True

    def test_chain_at_limit_fails(self) -> None:
        chain = ("a", "b", "c", "d", "e")
        result = check_delegation_depth(chain, max_depth=5)
        assert result.passed is False
        assert result.mechanism == "max_depth"
        assert "5" in result.message

    def test_chain_above_limit_fails(self) -> None:
        chain = ("a", "b", "c", "d", "e", "f")
        result = check_delegation_depth(chain, max_depth=5)
        assert result.passed is False

    def test_chain_one_below_limit_passes(self) -> None:
        chain = ("a", "b", "c", "d")
        result = check_delegation_depth(chain, max_depth=5)
        assert result.passed is True

    def test_max_depth_one(self) -> None:
        result = check_delegation_depth(("a",), max_depth=1)
        assert result.passed is False

    def test_max_depth_one_empty_passes(self) -> None:
        result = check_delegation_depth((), max_depth=1)
        assert result.passed is True
