"""Unit tests for the deterministic auto-rewriter."""

import pytest

from synthorg.engine.output_style.rewriter import RewriteOp, apply_rewrites


class TestApplyRewrites:
    @pytest.mark.unit
    def test_no_ops_is_identity(self) -> None:
        assert apply_rewrites("hello", ()) == "hello"

    @pytest.mark.unit
    def test_single_replacement(self) -> None:
        ops = (RewriteOp(start=6, end=7, replacement=", "),)
        assert apply_rewrites("hello X world", ops) == "hello ,  world"

    @pytest.mark.unit
    def test_multiple_replacements_stay_aligned(self) -> None:
        text = "aXbXc"
        ops = (
            RewriteOp(start=1, end=2, replacement="--"),
            RewriteOp(start=3, end=4, replacement="--"),
        )
        assert apply_rewrites(text, ops) == "a--b--c"

    @pytest.mark.unit
    def test_overlapping_ops_skip_later(self) -> None:
        text = "abcdef"
        ops = (
            RewriteOp(start=1, end=4, replacement="X"),
            RewriteOp(start=2, end=5, replacement="Y"),
        )
        # The first op wins; the overlapping second is skipped.
        assert apply_rewrites(text, ops) == "aXef"

    @pytest.mark.unit
    def test_unordered_ops_are_sorted(self) -> None:
        text = "aXbXc"
        ops = (
            RewriteOp(start=3, end=4, replacement="2"),
            RewriteOp(start=1, end=2, replacement="1"),
        )
        assert apply_rewrites(text, ops) == "a1b2c"
