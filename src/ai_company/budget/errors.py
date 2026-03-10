"""Budget-layer error hierarchy.

Defines budget-specific exceptions in a leaf module with no intra-project
imports, preventing circular dependency chains when these exceptions are
needed by both the budget enforcer and the engine layer.
"""


class BudgetExhaustedError(Exception):
    """Budget exhaustion signal.

    Used in two contexts:

    1. Raised directly by :meth:`BudgetEnforcer.check_can_execute`
       when pre-flight budget checks fail (e.g., monthly hard stop,
       daily limit, or provider quota exceeded).
    2. Caught by the engine layer (``AgentEngine.run``) and used to
       build an ``AgentRunResult`` with
       ``TerminationReason.BUDGET_EXHAUSTED``.
    """


class DailyLimitExceededError(BudgetExhaustedError):
    """Per-agent daily spending limit exceeded."""


class QuotaExhaustedError(BudgetExhaustedError):
    """Raised when provider quota is exhausted.

    Raised for all degradation strategies. Degradation routing
    (FALLBACK/QUEUE) is not yet implemented.
    """
