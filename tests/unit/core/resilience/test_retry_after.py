"""Tests for the shared Retry-After delta validator."""

import math

import pytest

from synthorg.core.resilience import coerce_finite_nonneg_seconds

pytestmark = pytest.mark.unit


def test_accepts_finite_nonneg() -> None:
    assert coerce_finite_nonneg_seconds(5) == 5.0
    assert coerce_finite_nonneg_seconds(0) == 0.0
    assert coerce_finite_nonneg_seconds(1.5) == 1.5


def test_rejects_negative() -> None:
    assert coerce_finite_nonneg_seconds(-1) is None


def test_rejects_non_finite() -> None:
    assert coerce_finite_nonneg_seconds(math.inf) is None
    assert coerce_finite_nonneg_seconds(math.nan) is None


def test_rejects_bool_and_non_numeric() -> None:
    assert coerce_finite_nonneg_seconds(True) is None
    assert coerce_finite_nonneg_seconds("5") is None
    assert coerce_finite_nonneg_seconds(None) is None
