"""Tests for ancestry cycle detection check."""

import pytest

from synthorg.communication.loop_prevention.ancestry import (
    check_ancestry,
)

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestCheckAncestry:
    def test_empty_chain_passes(self) -> None:
        result = check_ancestry((), "agent-a")
        assert result.passed is True
        assert result.mechanism == "ancestry"

    def test_delegatee_not_in_chain_passes(self) -> None:
        result = check_ancestry(("a", "b", "c"), "d")
        assert result.passed is True

    @pytest.mark.parametrize(
        ("chain", "delegatee"),
        [
            (("a", "b", "c"), "b"),
            (("root", "mid"), "root"),
            (("a", "b"), "b"),
            (("x",), "x"),
        ],
        ids=["mid-chain", "root", "last-in-chain", "single-element"],
    )
    def test_delegatee_in_chain_fails(
        self, chain: tuple[str, ...], delegatee: str
    ) -> None:
        result = check_ancestry(chain, delegatee)
        assert result.passed is False
        assert result.mechanism == "ancestry"
        assert f"'{delegatee}'" in result.message

    def test_single_element_chain_no_match_passes(self) -> None:
        result = check_ancestry(("x",), "y")
        assert result.passed is True
