# module-kind: declarative
"""Routing-strategy name constants.

Shared by ``strategies.py`` and ``_smart_strategy.py`` so the latter can
reference ``STRATEGY_NAME_SMART`` without importing ``strategies`` (which
imports ``SmartStrategy`` back, forming a cycle).
"""

from typing import Final

STRATEGY_NAME_MANUAL: Final[str] = "manual"
STRATEGY_NAME_ROLE_BASED: Final[str] = "role_based"
STRATEGY_NAME_COST_AWARE: Final[str] = "cost_aware"
STRATEGY_NAME_FASTEST: Final[str] = "fastest"
STRATEGY_NAME_SMART: Final[str] = "smart"
