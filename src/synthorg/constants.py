"""Shared constants for the synthorg package."""

from typing import Final

BUDGET_ROUNDING_PRECISION: Final[int] = 10
"""Decimal places for budget sum rounding; avoids IEEE 754 float artifacts."""
