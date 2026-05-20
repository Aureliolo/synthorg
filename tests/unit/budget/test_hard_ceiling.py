"""Hard real-money ceiling enforcement (#1982).

Covers the per-brief absolute ceiling honored by
``BudgetEnforcer.make_budget_checker``. Two failure modes:

* per-task ``Task.hard_ceiling`` set: the closure raises
  ``RunHardCeilingExceededError`` the moment accumulated cost crosses it.
* per-task absent: the closure falls back to the global setting
  ``budget.run_hard_ceiling`` (zero meaning disabled).

``RunHardCeilingExceededError`` is a subclass of
``BudgetExhaustedError`` so existing ``AgentEngine`` catch handlers
absorb it without changes; the engine then routes to the park / resume
path covered separately in
``tests/unit/engine/test_agent_engine_ceiling_park.py``.
"""

import pytest

# Lands in Phase 1 (errors + Task field) and Phase 6 (enforcer wiring).
pytestmark = pytest.mark.skip(
    reason="Awaiting #1982 Phase 1 + Phase 6 (RunHardCeilingExceededError + checker)",
)


def test_per_task_hard_ceiling_triggers_raise_at_threshold() -> None:
    """Task.hard_ceiling=1.50 and accumulated 1.50 raises immediately."""
    pytest.fail("scheduled by #1982 Phase 6 (BudgetChecker hard-ceiling closure)")


def test_global_run_hard_ceiling_used_when_task_field_absent() -> None:
    """Task.hard_ceiling=None falls back to budget.run_hard_ceiling setting."""
    pytest.fail("scheduled by #1982 Phase 6 (global fallback)")


def test_zero_ceiling_means_disabled_no_raise() -> None:
    """Both Task.hard_ceiling=None and run_hard_ceiling=0.0 -> no enforcement."""
    pytest.fail("scheduled by #1982 Phase 6")


def test_run_hard_ceiling_error_is_budget_exhausted_subclass() -> None:
    """RunHardCeilingExceededError inherits from BudgetExhaustedError.

    The engine's existing ``except BudgetExhaustedError`` catch must
    absorb the ceiling error without code changes.
    """
    pytest.fail("scheduled by #1982 Phase 1 (error hierarchy)")
