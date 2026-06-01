"""Procedural memory capture event constants."""

from typing import Final

CAPTURE_STRATEGY_BUILT: Final[str] = "capture.strategy.built"
# Engine post-execution capture-strategy dispatch: skipped when no strategy or
# no backend is wired; failed when the strategy raises (non-fatal, swallowed).
CAPTURE_STRATEGY_SKIPPED: Final[str] = "capture.strategy.skipped"
CAPTURE_STRATEGY_FAILED: Final[str] = "capture.strategy.failed"
