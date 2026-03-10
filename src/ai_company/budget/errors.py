"""Budget-layer error hierarchy.

Defines budget-specific exceptions. Kept in ``budget/`` to avoid circular
imports (``config.schema`` → ``budget`` → ``engine`` → ``providers`` →
``config.schema``).
"""


class BudgetExhaustedError(Exception):
    """Budget exhaustion signal.

    Used in two contexts:

    1. Raised directly by :meth:`BudgetEnforcer.check_can_execute`
       when pre-flight budget checks fail (monthly hard stop or daily
       limit exceeded).
    2. Available for converting ``TerminationReason.BUDGET_EXHAUSTED``
       loop results into a raised error at the engine layer.
    """


class DailyLimitExceededError(BudgetExhaustedError):
    """Per-agent daily spending limit exceeded."""


class QuotaExhaustedError(BudgetExhaustedError):
    """Raised when provider quota is exhausted.

    Currently raised for all degradation strategies. Degradation routing
    (FALLBACK/QUEUE) is planned for a future milestone.
    """
